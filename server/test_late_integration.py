"""Late uploads and summaries through the streaming/generation entry points."""
import json

import anyio
import numpy as np
import pytest

from alldayrec import audio, db, late_store, routes_speakers, summaries, tenant
from test_import_memory import client, wav_file
from test_late_audio import D, iso, ms, store
from test_backlog import flac
from test_summary_race import CID, model, run_workers


def test_streaming_chunk_upload_records_late_audio(store, monkeypatch):
    t = tenant.T()
    t.read_to[D] = ms(100)
    db.put_kv(f"finalized_to:{D}", iso(100))
    path = store / "upload.wav"
    wav_file(path, 2)
    monkeypatch.setattr(audio, "save", lambda *a, **kw: pytest.fail("legacy save called"))
    c = client(t)
    for _ in range(2):
        with path.open("rb") as f:
            r = c.post(f"/api/chunks?device={D}&start={ms(0)}", content=f,
                       headers={"content-type": "audio/wav"})
        assert r.status_code == 200 and r.json()["seconds"] == 2
    spans = list(db.conn().execute("SELECT ranges FROM late_spans"))
    assert [json.loads(s[0]) for s in spans] == [[[ms(0), ms(2)]]]
    assert any(e["kind"] == "warn" and "迟到" in e["text"] for e in t.events)
    assert db.conn().execute("SELECT count(*) FROM pieces").fetchone()[0] == 1


@pytest.mark.parametrize("long_file", [False, True])
def test_stream_skips_committed_late_samples_on_both_decode_paths(store, monkeypatch, long_file):
    monkeypatch.setattr(audio, "WHOLE_S", 1 if long_file else 300)
    x = np.arange(3 * audio.SR, dtype=np.int16)
    audio.save(D, ms(0), flac(x), "audio/flac")
    with db.tx() as t:
        db.record_processed(t, D, [[ms(1), ms(2)]], "late")
    async def consume():
        stream = audio.stream(D, None)
        got = []
        try:
            with anyio.fail_after(3):
                async for at, y in stream:
                    got.append((at, y))
                    if at.timestamp() * 1000 + len(y) * 1000 / audio.SR >= ms(3):
                        break
        finally:
            await stream.aclose()
        assert [(round(at.timestamp()*1000), len(y)) for at, y in got] == [(ms(0), audio.SR), (ms(2), audio.SR)]
        np.testing.assert_array_equal(np.concatenate([y for _, y in got]), np.r_[x[:audio.SR], x[2*audio.SR:]])
    anyio.run(consume)


def dirty():
    db.put_summary({**db.summary(CID), "status": "pending"})
    db.put_kv(f"late_summary:{CID}", True)
    db.put_kv(f"conversation_revision:{CID}", 1)


def test_late_summary_generation_preserves_pending_content_and_marks(model):
    dirty()
    old, marks = db.summary(CID), db.closed_todos()
    late_store.queue_summaries()
    queued = tenant.T().summaries[1].receive_nowait()
    assert queued.get("_summary_generation") == tenant.T().summary_generations.get(CID) == 1
    assert queued["_late"] and queued["_revision"] == 1
    assert db.summary(CID) == old
    assert db.closed_todos() == marks
    tenant.T().summaries[0].send_nowait(queued)

    async def scenario():
        await model.started(1)
        await model.finish(0)
        assert db.summary(CID)["status"] == "done"
        assert db.summary(CID)["todos"][0]["owner"] == "S1"
        assert db.closed_todos() == marks
        assert not db.kv(f"late_summary:{CID}")
    run_workers(scenario)


def test_late_summary_failure_keeps_previous_content_and_marks(model, monkeypatch):
    dirty()
    old, marks, attempts = db.summary(CID), db.closed_todos(), []
    async def fail(c, tags):
        attempts.append(c["id"])
        raise ValueError("synthetic failure")
    async def no_wait(delay):
        pass
    monkeypatch.setattr(summaries, "summarize", fail)
    monkeypatch.setattr(summaries.anyio, "sleep", no_wait)
    async def scenario():
        late_store.queue_summaries()
        await model.started(0)
        current = db.summary(CID)
        assert len(attempts) == 4
        assert current["status"] == "done" and current["title"] == old["title"]
        assert current["todos"] == old["todos"] and db.closed_todos() == marks
        assert not db.kv(f"late_summary:{CID}") and CID not in tenant.T().late_queued
    run_workers(scenario)


@pytest.mark.parametrize("fails", [False, True])
def test_late_summary_rename_discards_generation_and_requeues(model, monkeypatch, fails):
    dirty()
    marked_todo = next(iter(db.closed_todos()))
    async def no_retry(delay):
        pytest.fail("superseded late failure retried")
    monkeypatch.setattr(summaries.anyio, "sleep", no_retry)

    async def scenario():
        late_store.queue_summaries()
        await model.started(1)
        await routes_speakers.rename(CID, {"from": "S1", "to": "Ada"})
        assert db.summary(CID).get("title") == "Before"
        assert db.todo(marked_todo)["owner"] == "Ada"
        model.calls[0]["error"] = fails
        await model.finish(0)
        assert model.pushes == []
        assert db.kv(f"late_summary:{CID}")
        assert CID not in tenant.T().late_queued
        # The processor's next idle poll queues a new generation after the stale call exits.
        late_store.queue_summaries()
        await model.started(2)
        await model.finish(1)
        assert db.summary(CID)["status"] == "done"
        assert db.summary(CID)["todos"][0]["owner"] == "Ada"
        assert db.todo(marked_todo)["owner"] == "Ada"
        assert db.closed_todos()[marked_todo] == "done"
        assert len(model.pushes) == 1
        assert not db.kv(f"late_summary:{CID}") and CID not in tenant.T().late_queued
    run_workers(scenario)


def test_late_summary_revision_discards_result_and_requeues(model):
    dirty()
    async def scenario():
        late_store.queue_summaries()
        await model.started(1)
        generation = tenant.T().summary_generations.get(CID)
        db.put_kv(f"conversation_revision:{CID}", 2)
        await model.finish(0)
        assert tenant.T().summary_generations.get(CID) == generation
        assert model.pushes == [] and db.kv(f"late_summary:{CID}")
        assert CID not in tenant.T().late_queued
        late_store.queue_summaries()
        await model.started(2)
        await model.finish(1)
        assert db.summary(CID)["status"] == "done" and len(model.pushes) == 1
        assert not db.kv(f"late_summary:{CID}")
    run_workers(scenario)
