"""立即总结 (POST /api/live/{device}): the live analysis of the conversation going on before its minute of speech, one at
a time, only with speech it has not seen. Asked once, its agent writes the final summary though the conversation
stays under summary.MIN_SPEECH_S; one never asked still gets its first sentence as the title and no call. The pipeline
rig of test_backlog: real audio store, streams and pipeline, fake engines and LLM, a page watching."""
import datetime as dt
import tempfile
import time
from pathlib import Path

import anyio
import numpy as np
import pytest
from fastapi import HTTPException

import db
import server
from tenant import T
from test_backlog import DEVICE, SR, flac, hear, rig, save, st, tone


def test_ask(tmp_path, monkeypatch):
    rig(tmp_path, monkeypatch, hear)
    monkeypatch.setattr(server.summary, "MIN_SPEECH_S", 60)  # 4 tones of 4 s: well under it
    calls = []

    async def llm(messages, timeout=None, kind=None):
        calls.append(messages[-1]["content"])
        if server.live.FINAL in messages[-1]["content"]:
            return {"title": "短的"}, {"model": "m", "choices": [{"message": {"content": "{}"}}]}
        return {"ops": [{"op": "add", "title": "t"}]}, {"choices": [{"message": {"content": "{}"}}]}
    monkeypatch.setattr(server.live, "complete", llm)
    # the tones, then 40 s quiet: the final layer finalizes them all (QUIET_S / 2 into the pause), the speech stays put
    x = np.concatenate([np.concatenate([tone(i, 4), np.zeros(2 * SR, np.int16)]) for i in range(4)] + [np.zeros(40 * SR, np.int16)])
    t0 = dt.datetime.fromtimestamp(round(time.time()) - len(x) // SR)

    async def asked() -> str:
        with pytest.raises(HTTPException) as e:
            await server.live_ask(DEVICE)
        assert e.value.status_code == 409
        return e.value.detail

    async def run():
        save(t0, flac(x))
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.pipeline, DEVICE)
            finals = lambda: db.sentences(server.iso(t0), "~", source=DEVICE)
            for _ in range(60):
                await anyio.sleep(0.5)
                if st().conversation and len(finals()) == 4:
                    break
            speech = st().conversation["speech_s"]
            assert 10 < speech < 60, st().conversation
            assert not calls and not st().live, "under a minute: no analysis by itself"

            assert await server.live_ask(DEVICE) == {"analyzing": True}
            assert server.device_status(DEVICE, st())["analyzing"]
            assert await asked() == "[analyzing] 正在分析"
            for _ in range(20):
                if st().live:
                    break
                await anyio.sleep(0.2)
            assert st().live["asked"] and st().live["topics"] and len(calls) == 1
            assert not server.device_status(DEVICE, st())["analyzing"]
            assert await asked() == "[no_new_speech] 没有新的语音", "no wait between asks: only speech it has not seen"

            db.put_kv(f"end_at:{DEVICE}", server.iso(t0 + dt.timedelta(seconds=len(x) / SR)))  # the recorder stops
            for _ in range(20):
                if T().summaries[1].statistics().current_buffer_used:
                    break
                await anyio.sleep(0.5)
            c = T().summaries[1].receive_nowait()
            assert c["via_live"] and c["speech_s"] == speech and db.timeline(c["id"])["asked"]
            assert not st().live and await asked() == "[no_open_conversation] 没有进行中的对话"
            res = await server.summarize(c, [])
            assert res["via"] == "live" and res["title"] == "短的" and len(calls) == 2, "asked: the agent writes the summary"
            db.put_timeline(c["id"], {**db.timeline(c["id"]), "asked": False})
            res = await server.summarize(c, [])
            assert res["model"] is None and res["title"] == server.conversation_rows(c)[0]["text"] and len(calls) == 2, "never asked: the first sentence, no call"
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_ask_without_pipeline(monkeypatch):
    """A device without its pipeline (none, or one whose state exists but did not get as far as its ask: starting, or
    failed on the way and waiting for lasting() to restart it): 404, not a KeyError's 500."""
    db.open_db(Path(tempfile.mkdtemp()) / "t.db").devices["starting"] = server.PipelineState()
    for device in ("nobody", "starting"):
        with pytest.raises(HTTPException) as e:
            anyio.run(server.live_ask, device)
        assert e.value.status_code == 404 and e.value.detail == "[no_pipeline] 没有这台设备的处理流程"
