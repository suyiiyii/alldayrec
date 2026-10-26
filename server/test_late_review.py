"""Regression cases from the independent late-audio review; all four must-fixes fail on 6a56f55."""
import datetime as dt
import json
import time

import anyio
import numpy as np
import pytest

from alldayrec import audio, db, infer, late, late_backfill, late_merge, late_store, summaries
from alldayrec.tenant import T
from test_backlog import EnergyVad, flac, tone
from test_late_audio import A, B, D, E1, conv, iso, ms, row, span, store


def test_internal_late_speech_does_not_merge_stopped_neighbor(store):
    with db.tx() as t:
        conv(t, A, 0, 300)
        conv(t, B, 360, 600)
    s = span(100, 120)
    changed, removed = late_merge.attach(s, [row(100, 120)], {"S1": E1}, {})
    assert removed == []
    assert len(db.conversations()) == 2
    assert [c["id"] for c in changed] == [A]


def test_true_bridge_still_respects_four_hour_limit(store):
    from alldayrec.conversations import MAX_S
    with db.tx() as t:
        conv(t, A, 0, MAX_S - 120)
        conv(t, B, MAX_S, MAX_S + 120)
    s = span(MAX_S - 100, MAX_S - 20)
    _, removed = late_merge.attach(s, [row(MAX_S - 100, MAX_S - 20)], {"S1": E1}, {})
    assert not removed
    assert len(db.conversations()) == 2


def test_restart_mid_late_processing_hands_unfinalized_audio_back_to_live(store, monkeypatch):
    monkeypatch.setattr(audio, "decode", lambda body, **kw: np.ones((20 if len(body) > 5 else 10)*audio.SR, np.int16))
    monkeypatch.setattr(audio, "health", lambda x: (0, 0, 0, 0))
    audio.save(D, ms(0), b"short", "audio/wav")
    T().read_to[D] = ms(0)
    db.put_kv(f"read_to:{D}", ms(0))
    db.put_kv(f"read_end:{D}", ms(10))
    db.put_kv(f"finalized_to:{D}", iso(10))
    # A longer resend's unfinalized tail legitimately enters the late processor.
    audio.save(D, ms(0), b"longer", "audio/wav")
    late_store.release(D)
    s = late_store.claim()
    assert s and s["status"] == "processing"
    async def run():
        entered, finish, completed = anyio.Event(), anyio.Event(), anyio.Event()
        async def transcribe(span):
            entered.set()
            await finish.wait()
            return [row(10.1, 19.9, text="same audio, new ASR timing")], {"S1": E1}
        monkeypatch.setattr(late, "transcribe", transcribe)
        async def processing():
            await late.process(s)
            completed.set()
        async with anyio.create_task_group() as tg:
            tg.start_soon(processing)
            await entered.wait()
            stream = audio.stream(D, dt.datetime.fromisoformat(iso(10)))
            await anext(stream)  # pipeline restarts while the real process() is awaiting its provider
            await stream.aclose()
            with db.tx() as t:
                db.replace_sentences(t, iso(10), iso(20), [row(10.2, 19.8, text="finalized during restart")], D)
            finish.set()
            with anyio.fail_after(5):
                await completed.wait()
            tg.cancel_scope.cancel()
    anyio.run(run)
    assert len(db.sentences("", "z", source=D)) == 1
    assert db.sentences("", "z", source=D)[0]["text"] == "finalized during restart"


def test_completed_span_cannot_be_retried_or_inserted_with_shifted_ids(store):
    s = span(0, 20)
    s = late_store.claim()
    late_merge.attach(s, [row(0, 15)], {"S1": E1}, {})
    late_store.failed(s, RuntimeError("broadcast failed after commit"))
    assert db.conn().execute("SELECT status FROM late_spans").fetchone()[0] == "done"
    late_merge.attach(s, [row(0.1, 15.1)], {"S1": E1}, {})
    assert len(db.sentences("", "z", source=D)) == 1


def test_stale_recap_attempts_are_bounded_across_loop_restarts(store, monkeypatch, caplog):
    day = "2026-10-20"
    db.put_kv(f"recap:{day}", {"date": day, "headline": "keep", "todos": [], "stale": True})
    db.put_kv(f"recap:{dt.date.today() - dt.timedelta(days=1)}", {"headline": "already written"})
    calls = []
    async def fail(day):
        calls.append(day)
        raise ValueError("bad JSON")
    monkeypatch.setattr(summaries.recap, "make", fail)
    real_sleep = anyio.sleep
    async def cycles():
        tick = 0
        async def sleep(seconds):
            nonlocal tick
            tick += 1
            # Existing loop sleeps <=60 s: eight ticks must not mean eight model calls.
            if tick == 8:
                raise RuntimeError("end test loop")
            await real_sleep(0)
        monkeypatch.setattr(summaries.anyio, "sleep", sleep)
        with pytest.raises(RuntimeError, match="end test loop"):
            await summaries.recap_loop()
    anyio.run(cycles)
    assert len(calls) <= 3
    assert db.kv(f"recap:{day}")["headline"] == "keep"


def history(store, monkeypatch, *, trace_at=True):
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    day = dt.date.today()
    start = dt.datetime.combine(day, dt.time(10))
    base = round(start.timestamp()*1000)
    for offset, receipt in [(0, 100), (20, 200), (40, 100)]:
        audio.save(D, base+offset*1000, flac(tone(1, 10)), "audio/flac")
        db.conn().execute("UPDATE pieces SET received_ms=? WHERE device=? AND start_ms=?", (base+receipt*1000, D, base+offset*1000))
    stamp = lambda s: (start + dt.timedelta(seconds=s)).isoformat(timespec="milliseconds")
    trace = {"layer": "final", "device": D, "from": stamp(20), "to": stamp(30)}
    if trace_at:
        trace["at"] = stamp(250)
    (store/f"{day}.passes.jsonl").write_text(json.dumps(trace)+"\n")
    with db.tx() as t:
        db.insert_sentences(t, [{"id": "existing", "start": stamp(21), "end": stamp(29), "source": D, "text": "already processed"}])
    return base


def test_dry_run_and_real_run_exclude_processed_pauses_and_edges(store, monkeypatch):
    history(store, monkeypatch)
    dry = anyio.run(late_backfill.scan, 30, True)
    assert dry["days"] == [], "the whole legacy pass, including pauses and edges, was already processed"
    actual = anyio.run(late_backfill.scan, 30, False)
    assert actual["days"] == dry["days"]
    assert actual["enqueued"] == 0
    assert db.conn().execute("SELECT count(*) FROM late_spans").fetchone()[0] == 0


def test_legacy_trace_without_receipt_cutoff_is_reported_ambiguous(store, monkeypatch):
    history(store, monkeypatch, trace_at=False)
    dry = anyio.run(late_backfill.scan, 30, True)
    assert dry["days"] == []
    assert dry["skipped_ambiguous_spans"] > 0


def test_sub_half_second_jitter_holes_are_covered(store):
    db.put_kv(f"read_to:{D}", ms(30))
    T().read_to[D] = ms(30)
    for i in range(10):
        db.conn().execute("INSERT INTO pieces(device,start_ms,seconds,path,received_ms) VALUES (?,?,?,?,?)", (D, ms(i), 0.97, "x", 1))
    with db.tx() as t:
        late_store.record(t, D, ms(0), ms(10), 2)
    assert not list(db.conn().execute("SELECT * FROM late_spans"))


def test_retry_budget_survives_restart_and_logs_exhaustion_once(store, monkeypatch, caplog):
    day = "2026-10-20"
    db.put_kv(f"recap:{day}", {"headline": "old recap", "stale": True})
    calls = []
    async def fail(day):
        calls.append(day)
        raise ValueError("bad response")
    monkeypatch.setattr(summaries.recap, "make", fail)
    base = dt.datetime.now()
    for hours in range(6):
        db.open_db(store / "live.db")
        anyio.run(summaries.refresh_stale_recaps, base + dt.timedelta(hours=hours))
    assert len(calls) == 3
    assert db.kv(f"recap:{day}")["headline"] == "old recap"
    assert sum("stopped after" in r.message for r in caplog.records) == 1


def test_dry_and_real_share_exact_plan_and_cost_estimate(store, monkeypatch):
    base = history(store, monkeypatch)
    day = dt.date.today()
    start = dt.datetime.fromtimestamp(base / 1000)
    stamp = lambda s: (start + dt.timedelta(seconds=s)).isoformat(timespec="milliseconds")
    trace = {"layer": "final", "from": stamp(0), "to": stamp(50), "at": stamp(150)}
    (store/f"{day}.passes.jsonl").write_text(json.dumps(trace)+"\n")
    # This old row spans the clock gap; a second device's overlapping pieces must not make attribution ambiguous.
    db.conn().execute("UPDATE sentences SET start=?,end=?", (stamp(5), stamp(45)))
    db.conn().execute("INSERT INTO pieces(device,start_ms,seconds,path,received_ms) VALUES (?,?,?,?,?)", ("other", base, 60, "x", base+50_000))
    with db.tx() as t:
        db.insert_conversation(t, {"id": A, "start": stamp(0), "end": stamp(50), "participants": ["S1"], "sentences": 1, "device": D})
    db.put_kv(f"recap:{day}", {"headline": "cached"})
    before = db.conn().total_changes
    dry = anyio.run(late_backfill.scan, 30, True)
    assert db.conn().total_changes == before
    actual = anyio.run(late_backfill.scan, 30, False)
    assert dry["days"] == actual["days"]
    assert actual["enqueued"] == sum(x["spans"] for x in dry["days"]) == 1
    for result in (dry, actual):
        assert result["estimated_conversations"] == result["estimated_recaps"] == 1
    jobs = db.conn().execute("SELECT ranges FROM late_spans").fetchall()
    assert sum(b-a for r in jobs for a,b in json.loads(r[0])) == 10_000


def test_one_span_bridges_three_conversations_preserving_named_and_me_rows(store):
    third = "c20261020102000"
    with db.tx() as t:
        conv(t, A, 0, 30)
        conv(t, B, 600, 630, ("Named",))
        conv(t, third, 1200, 1230, ("我",))
    original = {r["id"]: dict(r) for r in db.sentences(iso(600), iso(1231), source=D)}
    s = span(40, 1200)
    rows = [row(i, i+20) for i in (40, 200, 400, 580, 640, 800, 1000, 1180)]
    _, removed = late_merge.attach(s, rows, {"S1": E1}, {})
    assert removed == [B, third]
    assert len(db.conversations()) == 1
    for r in db.sentences(iso(600), iso(1231), source=D):
        if r["id"] in original:
            assert r == original[r["id"]]


def test_committed_coverage_blocks_shifted_asr_rows_even_if_ledger_survived(store):
    span(0, 20)
    s = late_store.claim()
    with db.tx() as t:
        db.insert_sentences(t, [row(1, 19, text="live final")])
        db.record_processed(t, D, [[ms(0), ms(20)]], "final")
    late_merge.attach(s, [row(1.1, 19.1, text="different ASR alignment")], {"S1": E1}, {})
    assert len(db.sentences("", "z", source=D)) == 1
    assert db.conn().execute("SELECT status FROM late_spans").fetchone()[0] == "done"


def test_processor_timeout_allows_next_span(store, monkeypatch):
    first = span(0, 20)
    second = span(100, 120)
    monkeypatch.setattr(late, "LATE_PROCESS_TIMEOUT_S", 0.02)
    async def run():
        completed = anyio.Event()
        async def process(s):
            if s["id"] == first["id"]:
                await anyio.sleep_forever()
            db.conn().execute("UPDATE late_spans SET status='done' WHERE id=?", (s["id"],))
            completed.set()
        monkeypatch.setattr(late, "process", process)
        async with anyio.create_task_group() as tg:
            tg.start_soon(late.processor)
            with anyio.fail_after(2):
                await completed.wait()
            tg.cancel_scope.cancel()
    anyio.run(run)
    assert db.conn().execute("SELECT note FROM late_spans WHERE id=?", (first["id"],)).fetchone()[0] == "TimeoutError"
    assert db.conn().execute("SELECT status FROM late_spans WHERE id=?", (second["id"],)).fetchone()[0] == "done"


def test_ready_spans_batch_one_summary_for_latest_conversation_revision(store, monkeypatch):
    with db.tx() as t:
        conv(t, A, 0, 300)
    span(40, 60)
    span(180, 200)
    first = late_store.claim()
    late_merge.attach(first, [row(40, 60)], {"S1": E1}, {})
    late_store.queue_summaries()
    assert T().summaries[1].statistics().current_buffer_used == 0
    second = late_store.claim()
    late_merge.attach(second, [row(180, 200)], {"S1": E1}, {})
    late_store.queue_summaries()
    late_store.queue_summaries()
    assert T().summaries[1].statistics().current_buffer_used == 1
    seen = []
    async def summarize(c, tags):
        seen.append(c["id"])
        return {"conversation": c["id"], "status": "done", "title": "latest", "todos": []}
    monkeypatch.setattr(summaries, "summarize", summarize)
    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(summaries.summarize_loop)
            with anyio.fail_after(2):
                while (db.summary(A) or {}).get("status") != "done":
                    await anyio.sleep(0.01)
            tg.cancel_scope.cancel()
    anyio.run(run)
    late_store.queue_summaries()
    assert seen == [A]
    assert T().summaries[1].statistics().current_buffer_used == 0


def test_live_replay_excludes_committed_late_audio_after_restart(store, monkeypatch):
    monkeypatch.setattr(audio, "decode", lambda *a, **kw: np.ones(10*audio.SR, np.int16))
    monkeypatch.setattr(audio, "health", lambda x: (0, 0, 0, 0))
    audio.save(D, ms(0), b"audio", "audio/wav")
    audio.save(D, ms(10), b"audio", "audio/wav")
    s = span(0, 10)
    late_merge.attach(s, [row(0, 10)], {"S1": E1}, {})
    async def run():
        stream = audio.stream(D, dt.datetime.fromisoformat(iso(0)))
        start, x = await anext(stream)
        await stream.aclose()
        assert start == dt.datetime.fromisoformat(iso(10))
        assert len(x) == 10*audio.SR
    anyio.run(run)


def test_mcp_old_conversation_id_keeps_todo_marks(store):
    from alldayrec import mcp_server
    with db.tx() as t:
        conv(t, A, 0, 30)
        conv(t, B, 600, 630)
        db.put_summary({"conversation": B, "status": "done", "title": "meeting", "todos": [{"owner": "我", "text": "action", "at": iso(610)}]}, t)
    tid = db.todo_ids(B, db.summary(B)["todos"])[0]
    db.mark_todos([tid], "done")
    s = span(40, 600)
    late_merge.attach(s, [row(40, 590)], {"S1": E1}, {})
    assert "（已标记）" in mcp_server.conversation(B)


def test_previous_process_read_ahead_is_ignored_before_first_selection(store):
    db.put_kv(f"read_to:{D}", ms(20))
    db.put_kv(f"read_end:{D}", ms(30))
    db.put_kv(f"finalized_to:{D}", iso(5))
    assert not late_store.is_late(D, ms(10))
    assert late_store.is_late(D, ms(1))


def test_recap_budget_is_charged_before_a_mid_request_restart(store, monkeypatch):
    day = "2026-10-20"
    db.put_kv(f"recap:{day}", {"headline": "keep", "stale": True})
    calls = []
    async def run(now):
        entered = anyio.Event()
        async def hang(day):
            calls.append(day)
            entered.set()
            await anyio.sleep_forever()
        monkeypatch.setattr(summaries.recap, "make", hang)
        async with anyio.create_task_group() as tg:
            tg.start_soon(summaries.refresh_stale_recaps, now)
            await entered.wait()
            tg.cancel_scope.cancel()
    base = dt.datetime.now()
    for n in range(3):
        anyio.run(run, base + dt.timedelta(hours=n))
        db.open_db(store / "live.db")
        assert db.kv(f"recap_retry:{day}")["tries"] == n+1
    anyio.run(summaries.refresh_stale_recaps, base + dt.timedelta(hours=4))
    assert len(calls) == 3
    assert db.kv(f"recap:{day}")["headline"] == "keep"


def test_truncated_trace_does_not_turn_unknown_speech_into_backfill(store, monkeypatch):
    history(store, monkeypatch)
    day = dt.date.today()
    (store/f"{day}.passes.jsonl").write_text('{"layer":"final",')
    result = anyio.run(late_backfill.scan, 30, True)
    assert result["days"] == []
    assert result["skipped_ambiguous_spans"] > 0
