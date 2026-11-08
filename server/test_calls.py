"""The calls table: every ASR try and LLM call with its time, failure and size, never its text; the summary by
kind and engine; the StepFun balance and how long it lasts."""
import contextvars
import datetime as dt
import functools
import json
import tempfile
import urllib.error
from pathlib import Path

import anyio
import httpx2
import numpy as np
import pytest

from alldayrec import asr
from alldayrec import db
from alldayrec import net
from alldayrec import summary


def test_calls(monkeypatch):
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    lo = (dt.datetime.now() - dt.timedelta(seconds=1)).isoformat()

    async def bad(x, partial=None):
        raise RuntimeError("stepfun sse HTTP 402: balance")

    async def ok(x, partial=None):
        return [{"text": "秘密", "start_ms": 0, "end_ms": 500}]
    monkeypatch.setattr(asr, "PROVIDERS", {**asr.PROVIDERS, "a": bad, "b": ok})
    monkeypatch.setattr(asr, "SQUEEZE_KEEP_S", 0.0)
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "b")
    anyio.run(asr.engine_segs, np.zeros(16000 * 30, np.int16), [], None, "a", "draft", "LR-P3-116C")
    rows = [dict(r) for r in db.conn().execute("SELECT * FROM calls ORDER BY rowid")]
    assert [(r["kind"], r["engine"], r["device"], r["audio_s"], r["sent_s"]) for r in rows] == \
        [("draft", "a", "LR-P3-116C", 30.0, 30.0), ("draft", "b", "LR-P3-116C", 30.0, 30.0)]
    assert "402" in rows[0]["error"] and rows[1]["error"] is None

    answers = [httpx2.Response(502, text="echoes the prompt"),
               httpx2.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 900, "completion_tokens": 40}})]
    monkeypatch.setattr(net, "TRANSPORT", httpx2.MockTransport(lambda req: answers.pop(0)))
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_BASE_URL", "http://relay")
    monkeypatch.setenv("LLM_API_KEY", "k")
    with pytest.raises(urllib.error.HTTPError, match="HTTP Error 502: Bad Gateway"):
        anyio.run(functools.partial(summary.complete, [{"role": "user", "content": "秘密"}], kind="recap"))
    anyio.run(functools.partial(summary.complete, [{"role": "user", "content": "秘密"}], kind="recap"))
    assert "秘密" not in json.dumps([dict(r) for r in db.conn().execute("SELECT * FROM calls")]), "never what was said"
    assert "echoes" not in json.dumps([dict(r) for r in db.conn().execute("SELECT * FROM calls")]), "nor a response body"

    got = {(c["kind"], c["engine"]): c for c in db.calls(lo)}
    assert got[("draft", "a")]["failed"] == {"402": 1} and got[("draft", "a")]["ms50"] is None
    assert got[("draft", "b")]["n"] == 1 and got[("draft", "b")]["sent_h"] == round(30 / 3600, 2)
    r = got[("recap", "m")]
    assert (r["n"], r["failed"], r["prompt_tokens"], r["completion_tokens"]) == (2, {"502": 1}, 900, 40) and r["ms50"] is not None
    assert db.calls(dt.datetime.now().isoformat()) == []

    contextvars.Context().run(db.add_call, "final", "x", None, 1)  # no tenant (a script on a read-only copy): nothing kept, nothing raised
    assert db.calls(dt.datetime.now().isoformat()) == []


def test_balance():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    assert db.balance("stepfun") is None
    now = dt.datetime.now()
    for h, cny in ((48, 30.0), (36, 28.0), (24, 26.0), (23, 46.0), (0, 42.0)):  # ¥2 a 12 h, a ¥20 top-up, ¥4 since
        db.conn().execute("INSERT INTO balances VALUES (?, 'stepfun', ?)", ((now - dt.timedelta(hours=h)).isoformat(timespec="seconds"), cny))
    b = db.balance("stepfun")
    assert (b["cny"], b["per_day"], b["days_left"]) == (42.0, 4.0, 10.5), b  # ¥8 in 2 days, the top-up left out
    db.conn().execute("DELETE FROM balances WHERE at < ?", ((now - dt.timedelta(hours=1)).isoformat(),))
    assert db.balance("stepfun")["per_day"] is None, "one reading: no rate yet"


def test_llm_fallback(monkeypatch):
    """LLM_FALLBACK_* set: a timeout, 429 or 5xx goes once to the backup, each try its own calls row (the backup's engine
    fallback:<model>); a 4xx does not, nor anything with the backup unset, nor a stream that has begun."""
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    hosts, answers = [], []
    def answer(req):
        hosts.append(req.url.host)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(net, "TRANSPORT", httpx2.MockTransport(answer))
    for k, v in {"LLM_MODEL": "m", "LLM_BASE_URL": "http://relay", "LLM_API_KEY": "k"}.items():
        monkeypatch.setenv(k, v)
    ok = lambda: httpx2.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 1}})
    run = lambda **k: anyio.run(functools.partial(summary.complete, [{"role": "user", "content": "x"}], kind="recap", **k))

    answers[:] = [httpx2.Response(503)]
    with pytest.raises(urllib.error.HTTPError):
        run()  # off: no backup
    for k, v in {"LLM_FALLBACK_MODEL": "m2", "LLM_FALLBACK_BASE_URL": "http://backup", "LLM_FALLBACK_API_KEY": "k2"}.items():
        monkeypatch.setenv(k, v)
    for first in (httpx2.Response(503), httpx2.Response(429), httpx2.ReadTimeout("slow"), httpx2.ConnectError("down")):
        hosts.clear()
        answers[:] = [first, ok()]
        assert run()[0] == {} and hosts == ["relay", "backup"], first
    hosts.clear()
    answers[:] = [httpx2.Response(400)]
    with pytest.raises(urllib.error.HTTPError, match="400"):
        run()  # the request itself is wrong: the backup would say the same
    assert hosts == ["relay"]
    answers[:] = [httpx2.Response(502), httpx2.Response(502)]
    with pytest.raises(urllib.error.HTTPError, match="502"):
        run()  # once, no further
    rows = [(r["engine"], (r["error"] or "")[:20]) for r in db.conn().execute("SELECT * FROM calls ORDER BY rowid")]
    assert [e for e, _ in rows] == ["m"] + ["m", "fallback:m2"] * 4 + ["m"] + ["m", "fallback:m2"], rows
    assert [bool(err) for _, err in rows] == [True] + [True, False] * 4 + [True, True, True]

    pieces = []
    async def on(t):
        pieces.append(t)
    stream = "data: " + json.dumps({"choices": [{"delta": {"content": "半"}}]}) + "\n\n"
    class Cut(httpx2.AsyncByteStream):  # one piece, then the connection stalls
        async def __aiter__(self):
            yield stream.encode()
            raise httpx2.ReadTimeout("stalled")
    answers[:] = [httpx2.Response(200, stream=Cut()), ok()]
    hosts.clear()
    with pytest.raises(TimeoutError):
        anyio.run(functools.partial(summary.call, [{"role": "user", "content": "x"}], kind="ask", on_delta=on))
    assert pieces == ["半"] and hosts == ["relay"], "begun: no second reply after the first one's pieces"
