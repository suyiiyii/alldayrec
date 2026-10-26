"""Summary model calls finish in a chosen order; no network or timing sleeps."""
import anyio
import pytest

from alldayrec import core, db, live, routes_api, routes_sentences, routes_speakers, summaries, summary, tenant


CID = "c20261020100000"
START = "2026-10-20T10:00:00.000"


class Model:
    def __init__(self):
        self.calls = []
        self.pushes = []

    async def chat(self, text, *args):
        call = {"text": text, "gate": anyio.Event(), "owner": core.conversation_rows(db.conversation(CID))[0]["speaker"]}
        self.calls.append(call)
        await call["gate"].wait()
        if call.get("error"):
            raise ValueError("synthetic model error")
        return {"title": "Meeting", "summary": text,
                "todos": [{"text": "Send draft", "owner": call["owner"], "to": None, "at": "10:00:00"}]}, {}

    async def broadcast(self, msg):
        if msg["type"] == "summary":
            self.pushes.append(msg)

    async def started(self, n):
        await anyio.wait_all_tasks_blocked()
        assert len(self.calls) == n

    async def finish(self, i):
        self.calls[i]["gate"].set()
        await anyio.wait_all_tasks_blocked()


@pytest.fixture
def model(tmp_path, monkeypatch):
    db.open_db(tmp_path / "test.db")
    rows = [dict(id=f"r{i}", start=f"2026-10-20T10:00:{i:02}.000", end=f"2026-10-20T10:00:{i:02}.900",
                 speaker=label, text=f"Statement {i}", source="mic")
            for i, label in enumerate(("S1", "S2", "S3", "S1"))]
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, dict(id=CID, start=START, end=rows[-1]["end"], participants=["S1", "S2", "S3"],
                                       sentences=len(rows), speech_s=120, device="mic"))
        db.put_summary(dict(conversation=CID, status="done", title="Before", summary="Before", todos=[
            dict(text="Send draft", owner="S1", to=None, at=START)]), t)
        t.execute("INSERT INTO aliases VALUES (?, ?)", ("Teammate", "Ada"))
    db.mark_todos(db.todo_ids(CID, db.summary(CID)["todos"]), "done")
    monkeypatch.setitem(core.state, "embed", None)
    m = Model()
    monkeypatch.setattr(summary, "chat", m.chat)
    monkeypatch.setattr(summaries.ws, "broadcast", m.broadcast)
    return m


def run_workers(scenario):
    async def run():
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                for _ in range(4):
                    tg.start_soon(summaries.summarize_loop)
                await scenario()
                tg.cancel_scope.cancel()
    anyio.run(run)


def test_three_renames_out_of_order_keep_latest_names(model):
    marks = db.closed_todos()

    async def scenario():
        for i, name in enumerate(("Ada", "Ben", "Cleo")):
            await routes_speakers.rename(CID, {"from": f"S{i + 1}", "to": name})
            await model.started(i + 1)
        # Rename 3 finishes first, then rename 1, then rename 2 (the reported race).
        for i in (2, 0, 1):
            await model.finish(i)
        stored = db.summary(CID)
        assert all(name in stored["summary"] for name in ("Ada", "Ben", "Cleo"))
        assert not any(label in stored["summary"] for label in ("S1", "S2", "S3"))
        assert [p["summary"] for p in model.pushes] == [model.calls[2]["text"]]
        assert stored["todos"][0]["owner"] == "Ada" and len(stored["todos"]) == 1
        assert db.closed_todos() == marks
        assert [tuple(r) for r in db.conn().execute("SELECT * FROM aliases")] == [("Teammate", "Ada")]
    run_workers(scenario)


def test_single_rename_runs_exactly_one_rewrite(model):
    async def scenario():
        await routes_speakers.rename(CID, {"from": "S1", "to": "Ada"})
        await model.started(1)
        await model.finish(0)
        assert len(model.calls) == len(model.pushes) == 1
        assert "Ada" in db.summary(CID)["summary"] and "S1" not in db.summary(CID)["summary"]
        assert tenant.T().summaries[1].statistics().current_buffer_used == 0
    run_workers(scenario)


@pytest.mark.parametrize("existing_generation", [None, 7])
def test_request_without_generation_runs_normally(model, existing_generation):
    async def scenario():
        if existing_generation is not None:
            tenant.T().summary_generations[CID] = existing_generation
        tenant.T().summaries[0].send_nowait(db.conversation(CID))
        await model.started(1)
        await model.finish(0)
        assert db.summary(CID)["summary"] == model.calls[0]["text"]
        assert [p["summary"] for p in model.pushes] == [model.calls[0]["text"]]
        # The worker survives and can process the next generation-aware request.
        await routes_api.resummarize(CID)
        await model.started(2)
        await model.finish(1)
        assert len(model.pushes) == 2
    run_workers(scenario)


@pytest.mark.parametrize("keep", [False, True])
def test_failed_queue_write_keeps_inflight_summary_current(model, monkeypatch, keep):
    def fail_write(*args, **kwargs):
        raise RuntimeError("synthetic database write failure")

    async def scenario():
        await routes_api.resummarize(CID)
        await model.started(1)
        generation = tenant.T().summary_generations[CID]
        if keep:
            db.put_summary({**db.summary(CID), "error": "previous attempt failed"})
        with monkeypatch.context() as patch:
            patch.setattr(db, "put_summary", fail_write)
            with pytest.raises(RuntimeError, match="synthetic database write failure"):
                core.queue_summary(db.conversation(CID), keep=keep)
        assert tenant.T().summary_generations[CID] == generation
        assert tenant.T().summaries[1].statistics().current_buffer_used == 0
        await model.finish(0)
        assert db.summary(CID)["summary"] == model.calls[0]["text"]
        assert db.summary(CID)["error"] is None
        assert [p["summary"] for p in model.pushes] == [model.calls[0]["text"]]
    run_workers(scenario)


def test_superseded_queued_requests_skip_model_calls(model):
    async def scenario():
        # These endpoints don't yield between queuing: only the last request should need a model call.
        for name in ("Ada", "Ben", "Cleo"):
            await routes_speakers.rename(CID, {"from": db.conversation(CID)["participants"][0], "to": name})
        await routes_api.resummarize(CID)
        await model.started(1)
        await model.finish(0)
        assert "Cleo" in db.summary(CID)["summary"] and len(model.pushes) == 1
    run_workers(scenario)


def test_superseded_failure_does_not_retry_or_change_new_summary(model, monkeypatch):
    async def no_retry(delay):
        pytest.fail("a superseded failure must not retry or back off")
    monkeypatch.setattr(summaries.anyio, "sleep", no_retry)

    async def scenario():
        await routes_api.resummarize(CID)
        await model.started(1)
        await routes_speakers.rename(CID, {"from": "S1", "to": "Ada"})
        await model.started(2)
        await model.finish(1)
        model.calls[0]["error"] = True
        await model.finish(0)
        assert len(model.calls) == 2 and len(model.pushes) == 1
        assert db.summary(CID)["summary"] == model.calls[1]["text"]
        assert db.summary(CID)["error"] is None
    run_workers(scenario)


def test_sentence_correction_to_existing_participant_rewrites(model):
    async def scenario():
        await routes_api.resummarize(CID)
        await model.started(1)
        # S1 has two rows; correcting just one to existing S2 must invalidate the old input.
        await routes_sentences.sentence_speaker("r0", {"conv": CID, "to": "S2"})
        await model.started(2)
        await model.finish(1)
        await model.finish(0)
        assert db.summary(CID)["summary"] == model.calls[1]["text"]
        assert [p["summary"] for p in model.pushes] == [model.calls[1]["text"]]
    run_workers(scenario)


@pytest.mark.parametrize("first", ["manual", "final", "live"])
def test_all_summary_sources_share_latest_request_guard(model, monkeypatch, first):
    async def final(tl, rows, tags, start):
        return await model.chat(summary.transcript(rows))
    monkeypatch.setattr(live, "final", final)

    async def scenario():
        if first == "manual":  # also the resummarize CLI's POST endpoint
            await routes_api.resummarize(CID)
        else:
            c = db.conversation(CID)
            if first == "live":
                c["via_live"] = True
                db.put_timeline(CID, {"messages": [{"role": "user", "content": "Synthetic meeting"}]})
            core.queue_summary(c)
        await model.started(1)
        await routes_speakers.rename(CID, {"from": "S1", "to": "Ada"})
        await model.started(2)
        await model.finish(1)
        await model.finish(0)
        assert db.summary(CID)["summary"] == model.calls[1]["text"]
        assert [p["summary"] for p in model.pushes] == [model.calls[1]["text"]]
        # A later manual/CLI request must still run after an edit.
        await routes_api.resummarize(CID)
        await model.started(3)
        await model.finish(2)
        assert len(model.pushes) == 2
    run_workers(scenario)
