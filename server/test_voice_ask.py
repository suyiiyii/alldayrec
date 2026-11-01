"""A question said to a device (POST /api/ask/live/{device}): its words from the live transcript, the answer streamed on
/ws (ask.live); and an SSE chat stream put together as one response (summary.stream_reply)."""
import datetime as dt
import json

import anyio

from alldayrec import ask
from alldayrec import db
from alldayrec import summary
from alldayrec import ws
from alldayrec.core import iso
from alldayrec.pipeline_state import PipelineState
from alldayrec.tenant import T


def sse(*chunks):
    async def lines():
        for c in chunks:
            yield "data: " + json.dumps(c)
            yield ""
        yield "data: [DONE]"
    return lines()


def test_stream_reply():
    got = []
    async def on(t):
        got.append(t)
    tool = lambda i, **f: {"choices": [{"delta": {"tool_calls": [{"index": i, **f}]}}]}
    r = anyio.run(summary.stream_reply, sse({"choices": [{"delta": {"content": "好"}}]}, {"choices": [{"delta": {"content": "的"}}]},
                                            tool(0, id="t1", function={"name": "grep", "arguments": '{"q"'}),
                                            tool(0, function={"arguments": ': "x"}'}), tool(1, id="t2", function={"name": "day", "arguments": "{}"}),
                                            {"choices": [], "usage": {"prompt_tokens": 7}}), on)
    m = r["choices"][0]["message"]
    assert got == ["好", "的"] and m["content"] == "好的" and r["usage"] == {"prompt_tokens": 7}
    assert [(t["id"], t["function"]["name"], t["function"]["arguments"]) for t in m["tool_calls"]] == [("t1", "grep", '{"q": "x"}'), ("t2", "day", "{}")]


def test_live(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")
    lo = dt.datetime(2026, 11, 1, 10, 0, 0)
    at = lambda s: iso(lo + dt.timedelta(seconds=s))
    with db.tx() as c:  # final rows: one before the question, one in it
        db.insert_sentences(c, [dict(id="f0", start=at(-9), end=at(-5), speaker="我", text="之前的话", source="dev"),
                                dict(id="f1", start=at(0.2), end=at(1), speaker="我", text="我今天", source="dev"),
                                dict(id="f2", start=at(0.2), end=at(1), speaker="我", text="另一台", source="other")])
    T().devices["dev"] = PipelineState(done_to=lo + dt.timedelta(seconds=1),
                                       drafts=[dict(id="d1", start=at(1.2), end=at(2.5), text="下午聊了")],
                                       partial=dict(id="pdev", start=at(2.6), end=at(3.0), text="什"))  # its last words still to come
    sent = []
    async def broadcast(m):
        sent.append(m)
    monkeypatch.setattr(ws, "broadcast", broadcast)
    replies = iter([{"content": None, "tool_calls": [{"id": "t", "type": "function", "function": {"name": "calendar", "arguments": '{"month": "2026-11"}'}}]},
                    {"content": "聊了存储 [c20261101100000]。"}])
    seen = []
    async def llm(msgs, timeout=300, kind="summary", on_delta=None, **extra):
        seen.append(msgs[0]["content"])
        m = next(replies)
        for piece in ([m["content"][:3], m["content"][3:]] if m["content"] else []):
            await on_delta(piece)
        return {"choices": [{"message": m}]}
    monkeypatch.setattr(summary, "call", llm)
    async def grows():
        await anyio.sleep(0.3)
        T().devices["dev"].partial = dict(id="pdev", start=at(2.6), end=at(3.6), text="什么？")
    async def asked():
        async with anyio.create_task_group() as tg:
            tg.start_soon(grows)
            await ask.live("dev", "a1", lo, lo + dt.timedelta(seconds=4))
    anyio.run(asked)
    assert all(m["type"] == "ask" and m["device"] == "dev" and m["id"] == "a1" for m in sent)
    body = [{k: v for k, v in m.items() if k not in ("type", "device", "id")} for m in sent]
    assert body == [{"stage": "heard", "q": "我今天下午聊了什么？"}, {"stage": "searching"}, {"delta": "聊了存"}, {"delta": "储 [c20261101100000]。"},
                    {"stage": "done", "answer": "聊了存储 [c20261101100000]。"}], body
    assert seen[0].endswith(ask.VOICE) and len(seen) == 2, "short answers; no second call for citations"

    sent.clear()  # nothing said: an error once HEAR_S is up
    monkeypatch.setattr(ask, "HEAR_S", 0.1)
    anyio.run(ask.live, "dev", "a2", lo + dt.timedelta(seconds=20), lo + dt.timedelta(seconds=25))
    assert [(m["stage"], m["error"]) for m in sent] == [("error", "没听清")]


def test_trim():
    lo = dt.datetime(2026, 11, 1, 10, 0, 1, tzinfo=dt.timezone.utc)
    row = lambda s, e, text: {"start": iso(lo + dt.timedelta(seconds=s)), "end": iso(lo + dt.timedelta(seconds=e)), "text": text}
    rows = [row(-2, -1, "前一句"), row(-2, 2, "前文前文问题问题"), row(0.5, 2, "后一句")]
    assert [r["text"] for r in ask.trim(rows, lo)] == ["前一句", "问题问题", "后一句"]
