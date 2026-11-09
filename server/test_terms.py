"""The 热词和术语: kept per tenant with the people bank's names, the most recently used first; in each engine's request
that takes hotwords (within its limits), in the summary's and the questions' prompts, on the web API and over MCP."""
import json

import anyio
import httpx2
import numpy as np
import pytest
import websockets

import test_volc_asr as volc_peer
from alldayrec import ask
from alldayrec import asr
from alldayrec import db
from alldayrec import live
from alldayrec import mcp_server
from alldayrec import qwen_asr
from alldayrec import qwen_stream
from alldayrec import routes_api
from alldayrec import stepfun_asr
from alldayrec import summary
from alldayrec import tenant
from alldayrec import volc_asr
from test_net import delta, mock, sse


@pytest.fixture
def store(tmp_path):
    """Alice last in a conversation 11-02, Bob 11-01, Carol never; 我 is in the bank too; term 灵犀 set 11-03, 星河 10-30."""
    t = db.open_db(tmp_path / "t.db")
    c = db.conn()
    for name in ("我", "Alice", "Bob", "Carol"):
        c.execute("INSERT INTO people VALUES (?, 1, ?)", (name, b""))
    for i, (start, who) in enumerate([("2026-11-01T10:00:00.000", ["我", "Alice", "Bob"]), ("2026-11-02T10:00:00.000", ["我", "Alice", "S1"])]):
        db.insert_conversation(c, {"id": f"c{i}", "start": start, "end": start, "participants": who, "sentences": 1})
    c.executemany("INSERT INTO terms VALUES (?, ?)", [("灵犀", "2026-11-03T09:00:00"), ("星河", "2026-10-30T09:00:00")])
    return t


def test_hotwords_most_recent_first(store):
    assert db.terms() == ["灵犀", "星河"]
    assert db.hotwords() == ["灵犀", "Alice", "Bob", "星河", "Carol"], "我 and S labels never; a name never in a conversation last"
    db.conn().execute("INSERT INTO terms VALUES ('Bob', '2026-10-01T09:00:00')")
    assert db.hotwords() == ["灵犀", "Alice", "Bob", "星河", "Carol"], "a name typed in as a term too: the later of its two times"
    assert asr.hotwords(2) == ["灵犀", "Alice"], "cut after the order"
    assert asr.hotwords(9, lambda w: w.isascii()) == ["Alice", "Bob", "Carol"]
    token = tenant.current.set(None)
    assert asr.hotwords(9) == [], "no tenant (a replay script): none"
    tenant.current.reset(token)


def test_terms_api(store):
    run = lambda body: anyio.run(routes_api.term_set, body)
    assert anyio.run(routes_api.terms) == {"terms": ["灵犀", "星河"], "names": ["Alice", "Bob", "Carol"]}
    assert run({"term": " 飞书 "})["terms"][0] == "飞书", "added, newest first, trimmed"
    assert run({"old": "飞书", "term": "飞书妙记"})["terms"] == ["飞书妙记", "灵犀", "星河"], "changed"
    assert run({"old": "星河"})["terms"] == ["飞书妙记", "灵犀"], "dropped"
    assert run({"term": "Alice"}) == {"terms": ["Alice", "飞书妙记", "灵犀"], "names": ["Bob", "Carol"]}, "a name typed in shows once"
    for bad in ({}, {"term": "x" * 31}, {"term": "a\nb"}):
        with pytest.raises(routes_api.HTTPException):
            run(bad)


def test_mcp_terms(store):
    text, err, _ = mcp_server.call("terms", {})
    assert not err and text.endswith("灵犀、Alice、Bob、星河、Carol") and "5 个" in text
    assert any(t["name"] == "terms" and t["annotations"]["readOnlyHint"] for t in mcp_server.TOOL_LIST), "read-only"


def test_fits():
    assert qwen_asr.fits("灵犀" * 7) and not qwen_asr.fits("灵犀" * 8), "Bailian: ≤ 15 characters with a non-ASCII one"
    assert qwen_asr.fits("a b c d e f g") and not qwen_asr.fits("a b c d e f g h"), "an ASCII one ≤ 7 words"
    assert volc_asr.fits("一二三四五六七八九") and not volc_asr.fits("一二三四五六七八九十")


def test_qwen_request(store, monkeypatch):
    """asr.qwen sends the tenant's hotwords as parameters.vocabulary, each at WEIGHT; none: no vocabulary."""
    monkeypatch.setattr(qwen_asr, "POLL_S", 0)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dk")
    sent = []

    def handler(req):
        if req.url.path == "/api/v1/uploads":
            return httpx2.Response(200, json={"data": {"upload_host": "http://oss", "upload_dir": "d", "policy": "p", "signature": "s",
                                                       "oss_access_key_id": "a", "x_oss_object_acl": "private", "x_oss_forbid_overwrite": "true"}})
        if req.url.host == "oss":
            return httpx2.Response(200)
        if req.method == "POST":
            sent.append(json.loads(req.content)["parameters"])
            return httpx2.Response(200, json={"output": {"task_id": "t"}})
        return httpx2.Response(200, json={"output": {"task_status": "FAILED", "code": "ASR_RESPONSE_HAVE_NO_WORDS"}})
    mock(monkeypatch, handler)
    x = np.zeros(1600, np.int16)
    db.conn().execute("INSERT INTO terms VALUES (?, '2026-11-04')", ("十六个字的术语一二三四五六七八九十",))
    assert anyio.run(asr.qwen, x) == []
    assert sent[-1]["vocabulary"] == {"灵犀": 4, "Alice": 4, "Bob": 4, "星河": 4, "Carol": 4}, "too long for Bailian: left out"
    assert sent[-1]["diarization_enabled"] is True
    db.conn().execute("DELETE FROM terms")
    db.conn().execute("DELETE FROM people")
    anyio.run(asr.qwen, x)
    assert "vocabulary" not in sent[-1]


def test_stepfun_request(store, monkeypatch):
    """asr.stepfun_sse sends them as audio.input.transcription.hotwords, at most HOTWORDS."""
    monkeypatch.setenv("STEPFUN_API_KEY", "k")
    monkeypatch.setattr(stepfun_asr, "HOTWORDS", 3)
    sent = []

    def handler(req):
        sent.append(json.loads(req.content)["audio"]["input"]["transcription"])
        return httpx2.Response(200, content=sse(delta("灵犀。", 0, 300)) + sse({"type": "transcript.text.done", "text": "灵犀。"}))
    mock(monkeypatch, handler)
    assert [g["text"] for g in anyio.run(asr.stepfun_sse, np.zeros(1600, np.int16))] == ["灵犀。"]
    assert sent[0]["hotwords"] == ["灵犀", "Alice", "Bob"] and sent[0]["model"] == "stepaudio-2.5-asr"
    anyio.run(stepfun_asr.sse, np.zeros(1600, np.int16))
    assert "hotwords" not in sent[1], "none given: no field"


def test_volc_request(store, monkeypatch):
    """asr.volc sends them inline as request.corpus.context, a JSON string of {"hotwords": [{"word": ...}]}."""
    peer = volc_peer.Peer()
    volc_peer.install(monkeypatch, peer)
    monkeypatch.setattr(volc_asr, "fits", lambda w: w != "Bob")
    anyio.run(asr.volc, np.zeros(4000, np.int16))
    req = peer.sent[0][1]["payload"]["request"]
    assert json.loads(req["corpus"]["context"]) == {"hotwords": [{"word": w} for w in ("灵犀", "Alice", "星河", "Carol")]}
    assert req["enable_speaker_info"] and req["result_type"] == "full"
    assert volc_asr.corpus(()) == {}


def test_qwen_stream_request(store, monkeypatch):
    """The draft layer's streaming session sends them in run-task's parameters.vocabulary."""
    sent = []

    class WS:
        async def send(self, m):
            sent.append(json.loads(m))

        async def recv(self):
            return json.dumps({"header": {"event": "task-started"}})

        async def close(self):
            pass

    async def connect(url, **kw):
        return WS()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dk")
    monkeypatch.setattr(websockets, "connect", connect)

    async def run():
        async with qwen_stream.Session():
            pass
    anyio.run(run)
    p = sent[0]["payload"]["parameters"]
    assert p["vocabulary"] == dict.fromkeys(["灵犀", "Alice", "Bob", "星河", "Carol"], qwen_asr.WEIGHT) and p["sample_rate"] == 16000


def test_prompts_carry_terms(store, monkeypatch):
    """The summary's, the live close's and the questions' prompts list them; the number check takes them as given."""
    db.conn().execute("INSERT INTO terms VALUES ('K8s 1200', '2026-11-05')")
    seen = []

    async def complete(messages, **kw):
        seen.append(messages)
        return {"title": "t", "summary": "K8s 1200 灵犀"}, {"model": "m"}
    monkeypatch.setattr(summary, "complete", complete)
    monkeypatch.setattr(live, "complete", complete)
    line = summary.TERMS.format("K8s 1200、灵犀、Alice、Bob、星河、Carol")
    anyio.run(summary.chat, "[10:00:00] 你好", [], "2026-11-05T10:00:00.000")
    assert line in seen[0][0]["content"] and len(seen) == 1, "in the system prompt; 1200 from the terms is no made-up number"
    rows = [dict(start="2026-11-05T10:00:00.000", end="2026-11-05T10:00:01.000", text="你好", speaker="我")]
    anyio.run(live.final, {"messages": [], "drafted": True}, rows, [], "2026-11-05T10:00:00.000")
    assert line in seen[1][-1]["content"] and len(seen) == 2
    import datetime as dt
    assert line in ask.system(dt.datetime(2026, 11, 5, 12))
    monkeypatch.setattr(summary, "PROMPT_TERMS", 2)
    assert summary.terms_line() == summary.TERMS.format("K8s 1200、灵犀")
    db.conn().execute("DELETE FROM terms")
    db.conn().execute("DELETE FROM people")
    assert summary.terms_line() == "" and "热词" not in summary.instructions([], "2026-11-05T10:00:00") and "热词" not in ask.system(dt.datetime(2026, 11, 5, 12))
