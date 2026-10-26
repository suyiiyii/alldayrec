"""Late arrivals: durable coverage, closed-conversation attachment and atomic reference migration."""
import datetime as dt
import json
import time

import anyio
import numpy as np
import pytest

from alldayrec import audio, core, db, late, late_backfill, late_merge, late_store, monitor, routes_api, routes_speakers, routes_upload, tenant
from alldayrec.tenant import T
from test_backlog import EnergyVad, flac, heard, tone

BASE = dt.datetime(2026, 10, 20, 10)
D = "test-recorder"
A, B = "c20261020100000", "c20261020101000"
E1, E2, E3 = np.eye(3, dtype=np.float32)


def ms(s):
    return round((BASE + dt.timedelta(seconds=s)).timestamp() * 1000)


def iso(s):
    return core.iso(BASE + dt.timedelta(seconds=s))


def row(s, end=None, speaker="S1", text=None):
    return dict(id=f"f{s}", start=iso(s), end=iso(end if end is not None else s + 15),
                speaker=speaker, source=D, text=text or str(s), engine="test", state="final")


def conv(t, cid, start, end, labels=("S1",)):
    c = dict(id=cid, start=iso(start), end=iso(end), participants=list(labels), sentences=1, speech_s=end-start, device=D)
    db.insert_conversation(t, c)
    db.insert_sentences(t, [row(start, end, labels[0])])
    return c


def span(a, b, ready=True):
    with db.tx() as t:
        late_store.enqueue(t, D, ms(a), ms(b), round(time.time()*1000), ready)
    db.put_kv(f"finalized_to:{D}", iso(b+1000))
    return dict(db.conn().execute("SELECT * FROM late_spans ORDER BY id DESC LIMIT 1").fetchone())


@pytest.fixture
def store(tmp_path):
    db.open_db(tmp_path / "live.db")
    return tmp_path


def test_ledger_predicate_duplicates_and_longer_resend(store, monkeypatch):
    monkeypatch.setattr(audio, "decode", lambda body, **kw: np.zeros(len(body)*audio.SR, np.int16))
    monkeypatch.setattr(audio, "health", lambda x: (0, 0, 0, 0))
    audio.save(D, ms(30), b"aaa", "audio/wav")
    assert not list(db.conn().execute("SELECT * FROM late_spans"))
    T().read_to[D] = ms(30)
    assert late_store.is_late(D, ms(0))
    audio.save(D, ms(0), b"aaa", "audio/wav")
    audio.save(D, ms(0), b"aaa", "audio/wav")
    s = dict(db.conn().execute("SELECT * FROM late_spans").fetchone())
    assert json.loads(s["ranges"]) == [[ms(0), ms(3)]]
    audio.save(D, ms(0), b"aaaaa", "audio/wav")
    s = dict(db.conn().execute("SELECT * FROM late_spans").fetchone())
    assert json.loads(s["ranges"]) == [[ms(0), ms(5)]]
    audio.save("other", ms(0), b"aaa", "audio/wav")
    assert db.conn().execute("SELECT count(*) FROM late_spans").fetchone()[0] == 1


def test_ledger_subtracts_all_prior_coverage_and_preserves_holes(store):
    for start, sec in [(10, 10), (30, 10), (15, 2)]:
        db.conn().execute("INSERT INTO pieces (device,start_ms,seconds,path,received_ms) VALUES (?,?,?,?,?)", (D, ms(start), sec, "x", 1))
    db.put_kv(f"read_to:{D}", ms(90))
    T().read_to[D] = ms(90)
    with db.tx() as t:
        late_store.record(t, D, ms(0), ms(50), 100)
    assert [json.loads(r[0]) for r in db.conn().execute("SELECT ranges FROM late_spans ORDER BY start_ms")] == [
        [[ms(0), ms(10)]], [[ms(20), ms(30)]], [[ms(40), ms(50)]]]
    assert late_store.subtract([[0, 10]], [[1, 2], [2, 6], [8, 10]]) == [[0, 1], [6, 8]]


def test_upload_note_uses_save_snapshot_when_reader_advances_during_await(store, monkeypatch):
    save = audio.save_file
    def advancing(*args):
        result = save(*args)
        db.put_kv(f"read_to:{D}", ms(100))
        T().read_to[D] = ms(100)
        return result
    monkeypatch.setattr(audio, "save_file", advancing)
    class Upload:
        headers = {"content-type": "audio/wav"}
        async def stream(self):
            yield flac(np.zeros(audio.SR, np.int16))
    anyio.run(routes_upload.chunk, Upload(), D, ms(0))
    assert late_store.is_late(D, ms(0)), "the reader advanced before the route resumed"
    assert T().events[-1]["kind"] == "info"
    assert db.conn().execute("SELECT count(*) FROM late_spans").fetchone()[0] == 0
    anyio.run(routes_upload.chunk, Upload(), D, ms(-10))
    assert T().events[-1]["kind"] == "warn"
    assert db.conn().execute("SELECT count(*) FROM late_spans").fetchone()[0] == 1


def test_settle_defer_release_and_final_commit_barrier(store, monkeypatch):
    monkeypatch.setattr(late_store, "LATE_SETTLE_S", 180)
    with db.tx() as t:
        late_store.enqueue(t, D, ms(0), ms(30), 1_000)
    assert late_store.claim(1_000_000) is None
    db.put_kv(f"finalized_to:{D}", iso(100))
    assert late_store.claim(180_000) is None
    db.put_kv(f"open_conversation:{D}", {"start": iso(100), "end": iso(120)})
    assert late_store.claim(182_000) is None
    assert db.conn().execute("SELECT status FROM late_spans").fetchone()[0] == "deferred"
    db.put_kv(f"open_conversation:{D}", None)
    assert late_store.claim(182_000)["tries"] == 1
    s = span(500, 520, ready=False)
    late_store.release(D)
    assert late_store.claim(s["last_ms"])["ready"] == 1


def test_retry_backoff_failed_and_restart(store):
    span(0, 20)
    for i in range(1, 4):
        s = late_store.claim(round(time.time()*1000)+1_000_000*i)
        assert s["tries"] == i
        late_store.failed(s, ValueError("provider secret should never persist"), now_ms=i*1_000)
        assert db.conn().execute("SELECT note FROM late_spans").fetchone()[0] == "ValueError"
    assert db.conn().execute("SELECT status FROM late_spans").fetchone()[0] == "failed"
    assert late_store.claim(10**16) is None
    s = span(100, 120)
    assert late_store.claim(10**16)
    db.open_db(store / "live.db")
    assert late_store.is_late(D, ms(1))
    late_store.recover()
    assert db.conn().execute("SELECT status,tries FROM late_spans WHERE id=?", (s["id"],)).fetchone()[:] == ("waiting", 1)


@pytest.mark.parametrize("a,b", [(20, 40), (100, 120), (-100, -80)])
def test_attach_inside_adjacent_and_earlier_keeps_id(store, a, b):
    with db.tx() as t:
        conv(t, A, 0, 60)
    before = [tuple(r) for r in db.conn().execute("SELECT rowid,* FROM sentences")]
    s = span(a, b)
    changed, removed = late_merge.attach(s, [row(a, b)], {"S1": E1}, {A: {"S1": E1}})
    assert not removed and changed[0]["id"] == A and changed[0]["sentences"] == 2
    assert db.summary(A)["status"] == "pending"
    assert tuple(db.conn().execute("SELECT rowid,* FROM sentences ORDER BY rowid").fetchone()) == before[0]


def test_new_conversations_use_gap_minimum_and_keep_corrections(store):
    s = span(0, 1100)
    rs = [row(0, 20), row(700, 720), row(1090, 1095)]
    cs, removed = late_merge.attach(s, rs, {"S1": E1}, {})
    assert len(cs) == 2 and not removed
    assert [c["start"] for c in cs] == [iso(0), iso(700)]
    assert len(db.sentences("", "z")) == 3
    db.conn().execute("UPDATE sentences SET text = ?, speaker = ? WHERE id = ?", ("corrected", "Named", "f0"))
    before = [tuple(r) for r in db.conn().execute("SELECT rowid,* FROM sentences")]
    late_merge.attach(s, rs, {"S1": E1}, {})
    assert [tuple(r) for r in db.conn().execute("SELECT rowid,* FROM sentences")] == before


def test_speaker_mapping_uses_cosine_threshold_and_reserves_collisions():
    got = late_merge.label_map(["S1", "S2", "S3", "我", "Named", "S?"], {"S1": E2, "S2": E1, "S3": E3}, {"S1": E1}, {"S1"})
    assert got == {"Named": "Named", "S1": "S2", "S2": "S1", "S3": "S3", "S?": "S?", "我": "我"}


def test_inside_hole_preserves_edited_rows_and_later_live_gap_commit(store):
    with db.tx() as t:
        c = conv(t, A, 0, 20)
        c.update(end=iso(120), sentences=2)
        db.insert_conversation(t, c)
        db.insert_sentences(t, [row(100, 120, "Named", "corrected existing text")])
    s = span(40, 60)
    cs, _ = late_merge.attach(s, [row(40, 60)], {"S1": E1}, {A: {"S1": E1}})
    assert cs[0]["sentences"] == 3
    db.conn().execute("UPDATE sentences SET text = 'corrected late text' WHERE id = 'f40'")
    with db.tx() as t:
        db.replace_sentences(t, iso(20), iso(90), [], D)
        db.replace_sentences(t, iso(20), iso(90), [row(39, 61, text="replayed copy")], D)
    assert [(r["id"], r["text"]) for r in db.sentences(iso(20), iso(90), source=D)] == [("f40", "corrected late text")]
    assert db.sentences(iso(100), iso(121), source=D)[0]["text"] == "corrected existing text"


def test_longer_retransmit_tail_is_processable_once_live_reader_is_drained(store):
    s = span(60, 90)
    db.put_kv(f"finalized_to:{D}", iso(60))
    db.put_kv(f"read_end:{D}", ms(60))
    assert late_store.claim(10**16)["id"] == s["id"]


def test_alias_chains_preserve_old_todo_and_voice_requests(store):
    with db.tx() as t:
        conv(t, A, 0, 30)
        conv(t, B, 600, 630)
        db.put_summary({"conversation": B, "status": "done", "todos": [{"text": "action", "owner": "我"}]}, t)
    original = db.todo_ids(B, db.summary(B)["todos"])[0]
    s = span(40, 600)
    late_merge.attach(s, [row(40, 590)], {"S1": E1}, {})
    earlier = "c20261020095000"
    with db.tx() as t:
        conv(t, earlier, -600, -570)
    s = span(-560, 0)
    late_merge.attach(s, [row(-560, -10)], {"S1": E1}, {})
    assert db.resolve_conversation(B) == earlier
    assert db.resolve_conversation(A) == earlier
    assert db.todo(original)["text"] == "action"
    anyio.run(routes_api.todo_mark, original, {"mark": "done"})
    assert db.closed_todos()[db.resolve_todo(original)] == "done"


def test_deferred_again_if_open_conversation_appears_during_model_call(store):
    s = span(0, 30)
    db.put_kv(f"open_conversation:{D}", {"start": iso(45), "end": iso(90)})
    assert late_merge.attach(s, [row(0, 20)], {}, {}) == ([], [])
    assert db.conn().execute("SELECT status FROM late_spans").fetchone()[0] == "deferred"
    assert not db.sentences("", "z")


def test_inflight_old_summary_cannot_overwrite_merged_transcript(store, monkeypatch):
    from alldayrec import summaries
    with db.tx() as t:
        conv(t, A, 0, 30)
    seen = []
    async def run():
        started, finish = anyio.Event(), anyio.Event()
        async def summarize(c, tags):
            seen.append(c["sentences"])
            if len(seen) == 1:
                started.set()
                await finish.wait()
            return {"conversation": c["id"], "status": "done", "title": str(c["sentences"]), "todos": []}
        monkeypatch.setattr(summaries, "summarize", summarize)
        T().summaries[0].send_nowait(db.conversation(A))
        async with anyio.create_task_group() as tg:
            tg.start_soon(summaries.summarize_loop)
            await started.wait()
            s = span(60, 80)
            cs, _ = late_merge.attach(s, [row(60, 80)], {"S1": E1}, {A: {"S1": E1}})
            late_store.queue_summaries()
            finish.set()
            with anyio.fail_after(5):
                while (db.summary(A) or {}).get("status") != "done":
                    await anyio.sleep(0.01)
            tg.cancel_scope.cancel()
    anyio.run(run)
    assert seen == [1, 2] and db.summary(A)["title"] == "2"


def test_bridge_moves_every_reference_and_alias_api(store):
    with db.tx() as t:
        conv(t, A, 0, 30)
        conv(t, B, 600, 630, ("S1", "S2", "Named"))
        db.insert_sentences(t, [row(615, 625, "S2", "second voice")])
        db.insert_voices(t, [{"conv": A, "label": "S1", "device": "live", "e": E1, "k": 1},
                             {"conv": B, "label": "S1", "device": "live", "e": E2, "k": 1},
                             {"conv": B, "label": "S2", "device": "live", "e": E1, "k": 1}])
        for cid in (A, B):
            db.put_summary({"conversation": cid, "status": "done", "todos": [{"owner": "我", "to": "S1", "text": cid, "at": iso(10 if cid == A else 610)}]}, t)
        todo = db.todo_ids(B, db.summary(B)["todos"])[0]
        db.mark_todos([todo], "done")
        db.put_timeline(B, {"conversation": B, "speaker": "S1", "messages": []}, t)
        db.put_name_hints({"conv": B, "names": [{"who": "S1", "by": "S2", "name": "test"}]}, t)
        t.execute("INSERT INTO feedback VALUES (?,?,?)", (B, "now", "keep feedback"))
        t.execute("INSERT INTO stars VALUES (?,?,?)", ("f600", B, "now"))
        t.execute("INSERT INTO enrolled VALUES (?,?,?,1,?)", ("Named", B, 2, db.blob(E1)))
        t.execute("INSERT INTO apart VALUES (?,?)", (B+"|S1", B+"|S2"))
        db.put_profile({"name": "Named", "facts": [{"text": "fact", "src": [{"conv": B}]}]})
        db.put_kv("recap:2026-10-20", {"date": "2026-10-20", "todos": [{"conv": B, "id": todo, "text": B}], "highlights": [{"conv": B}]}, t)
        db.put_kv("me_centroids", {"me-test": [B+"|我"]}, t)
        db.put_kv(f"live:{D}", {"conversation": B}, t)
        db.put_kv("selfvoice", {"card": {"candidates": [{"members": [{"conv": B, "label": "S1"}]}]}}, t)
    T().undo["old"] = [{"conv": B}]
    s = span(40, 600)
    rows = [row(i, i+20) for i in (40, 200, 400, 580)]
    cs, gone = late_merge.attach(s, rows, {"S1": E1}, {A: {"S1": E1}, B: {"S1": E2, "S2": E1, "Named": E3}})
    assert gone == [B] and cs[0]["id"] == A
    assert db.conversation(B)["id"] == A
    assert db.resolve_voice(B, "S1") == (A, "S2") and db.resolve_voice(B, "S2") == (A, "S1")
    assert db.conn().execute("SELECT speaker FROM sentences WHERE id=?", ("f600",)).fetchone()[0] == "S2"
    assert db.conn().execute("SELECT speaker FROM sentences WHERE id=?", ("f615",)).fetchone()[0] == "S1"
    assert {v["conv"] for v in db.voices()} == {A}
    for table in ("feedback", "stars", "enrolled", "todo_marks"):
        assert db.conn().execute(f"SELECT conv FROM {table}").fetchone()[0] == A
    nid = db.resolve_todo(todo)
    assert db.closed_todos()[nid] == "done" and db.todo(todo)["id"] == nid
    assert db.name_hints(A)[A]["names"][0]["who"] == "S2"
    assert db.timeline(A)["conversation"] == A
    assert db.apart() == {(A+"|S2", A+"|S1")}
    assert db.profile("Named")["facts"][0]["src"][0]["conv"] == A
    assert db.kv("me_centroids")["me-test"] == [A+"|我"]
    assert db.kv(f"live:{D}")["conversation"] == A
    assert db.kv("selfvoice")["card"]["candidates"][0]["members"][0] == {"conv": A, "label": "S2"}
    assert db.kv("recap:2026-10-20")["stale"]
    assert db.kv("recap:2026-10-20")["todos"][0]["id"] == nid
    assert "old" not in T().undo
    got = anyio.run(routes_api.conversation, B)
    assert got["id"] == A and got["summary"]["conversation"] == A
    anyio.run(routes_api.add_feedback, B, {"text": "through old link"})
    assert db.feedback(A)[0]["conv"] == A
    anyio.run(routes_api.todo_unmark, todo)
    assert nid not in db.closed_todos()
    anyio.run(routes_speakers.rename, B, {"from": "S1", "to": "S2"})
    assert "S2" not in db.conversation(A)["participants"], "both labels of an old-ID rename resolve through its alias"


def test_merge_atomic_rollback(store, monkeypatch):
    with db.tx() as t:
        conv(t, A, 0, 30)
        conv(t, B, 600, 630)
    s = span(40, 600)
    def boom(*args):
        raise RuntimeError("transaction test")
    monkeypatch.setattr(late_merge, "merge", boom)
    with pytest.raises(RuntimeError):
        late_merge.attach(s, [row(40, 590)], {"S1": E1}, {})
    assert db.resolve_conversation(B) == B
    assert len(db.sentences("", "z")) == 2 and len(db.conversations()) == 2


def test_tenant_isolation_and_status_without_live_pipeline(store):
    first = T()
    s = span(0, 20)
    assert monitor.status()["devices"][0]["late"]["waiting"]["spans"] == 1
    db.open_db(store / "other/live.db")
    assert late_store.claim(10**16) is None and monitor.status()["devices"] == []
    tenant.enter(first)
    assert late_store.claim(10**16)["id"] == s["id"]


def test_processor_uses_exact_audio_and_logs_late_pass(store, monkeypatch):
    from alldayrec import infer
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    monkeypatch.setitem(core.state, "embed", lambda x: E1)
    np.savez(store / "owner-redimnet2.npz", owner=E2)
    x = np.concatenate([tone(1, 5), np.zeros(2*audio.SR, np.int16), tone(2, 5)])
    audio.save(D, ms(0), flac(x), "audio/flac")
    s = span(0, 12)
    async def engine(x, speech, **kw):
        assert kw["kind"] == "late"
        return [{**g, "engine": "test"} for g in heard(x)]
    monkeypatch.setattr(late.asr, "engine_segs", engine)
    assert np.array_equal(late.read_exact(D, ms(3), ms(9)), x[3*audio.SR:9*audio.SR])
    anyio.run(late.process, s)
    trace = [json.loads(l) for l in (store/"2026-10-20.passes.jsonl").read_text().splitlines()]
    assert all(p["layer"] == "late" and p["device"] == D for p in trace)
    assert db.conn().execute("SELECT status FROM late_spans").fetchone()[0] == "done"
    assert len(db.sentences("", "z")) >= 1
    assert {r["speaker"] for r in db.sentences("", "z")} == {"S1"}, "close-time evidence resolves the initial S?"
    assert len(db.conversations()) == 1


def test_backfill_hand_built_history_dry_run_and_idempotence(store, monkeypatch):
    from alldayrec import infer
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    day = dt.date.today()
    base = round(dt.datetime.combine(day, dt.time(10)).timestamp()*1000)
    for offset, received in [(0, 100), (20, 200), (40, 100), (60, None)]:
        audio.save(D, base+offset*1000, flac(tone(1, 10)), "audio/flac")
        db.conn().execute("UPDATE pieces SET received_ms=? WHERE start_ms=?", (received, base+offset*1000))
    p = {"layer": "final", "device": D, "from": core.iso(dt.datetime.fromtimestamp(base/1000)),
         "to": core.iso(dt.datetime.fromtimestamp(base/1000)+dt.timedelta(seconds=60)),
         "at": dt.datetime.fromtimestamp(0.15).isoformat()}
    (store/f"{day}.passes.jsonl").write_text(json.dumps(p)+"\n")
    before = db.conn().total_changes
    found = anyio.run(late_backfill.scan, 30, True)
    assert found["days"][0]["spans"] == 1 and found["days"][0]["minutes"] == 0.17
    assert found["days"][0]["speech_minutes"] > 0
    assert found["skipped_null_received_days"] == [str(day)]
    assert db.conn().total_changes == before
    assert anyio.run(late_backfill.scan, 30, False)["enqueued"] == 1
    assert anyio.run(late_backfill.scan, 30, False)["enqueued"] == 0


def test_backfill_exact_pass_ranges_skip_final_speech_but_keep_clock_holes(store):
    day = dt.date.today()
    start = dt.datetime.combine(day, dt.time(10))
    base = round(start.timestamp()*1000)
    for offset, received in [(0, 100), (20, 200), (40, 100)]:
        db.conn().execute("INSERT INTO pieces (device,start_ms,seconds,path,received_ms) VALUES (?,?,?,?,?)",
                          (D, base+offset*1000, 10, "x", received))
    t = lambda s: core.iso(start+dt.timedelta(seconds=s))
    path = store/f"{day}.passes.jsonl"
    p = {"layer": "final", "device": D, "from": t(0), "to": t(50), "audio_ranges": [[t(0), t(10)], [t(40), t(50)]]}
    path.write_text(json.dumps(p)+"\n")
    found, _ = late_backfill.candidates(1)
    assert found[D] == [[[base+20_000, base+30_000]]]
    p["audio_ranges"].append([t(20), t(30)])
    path.write_text(json.dumps(p)+"\n")
    found, _ = late_backfill.candidates(1)
    assert not found[D]


def test_legacy_pass_row_crossing_hole_does_not_hide_audio_received_after_pass(store):
    day = dt.date.today()
    start = dt.datetime.combine(day, dt.time(10))
    base = round(start.timestamp()*1000)
    for offset, received in [(0, base+100_000), (20, base+200_000), (40, base+100_000)]:
        db.conn().execute("INSERT INTO pieces (device,start_ms,seconds,path,received_ms) VALUES (?,?,?,?,?)",
                          (D, base+offset*1000, 10, "x", received))
    t = lambda s: core.iso(start+dt.timedelta(seconds=s))
    with db.tx() as tx:
        db.insert_sentences(tx, [{"id": "crosses-gap", "start": t(5), "end": t(45), "source": D, "text": "heard on either side"}])
    trace = {"layer": "final", "device": D, "from": t(0), "to": t(50), "at": t(150)}
    (store/f"{day}.passes.jsonl").write_text(json.dumps(trace)+"\n")
    found, _ = late_backfill.candidates(1)
    assert found[D] == [[[base+20_000, base+30_000]]]
