"""The outbound HTTP on httpx2 (net.py), against httpx2's MockTransport: StepFun's SSE (sentences out as they come),
its balance, the LLM (the calls table as before: codes, timeouts, a call cut off), ask's tool loop, the file-mode ASR
behind the upload bridge, and errors read as urllib's."""
import base64
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

from alldayrec import ask
from alldayrec import asr
from alldayrec import db
from alldayrec import net
from alldayrec import qwen_asr
from alldayrec import stepfun_asr
from alldayrec import summary


def mock(monkeypatch, handler):
    monkeypatch.setattr(net, "TRANSPORT", httpx2.MockTransport(handler))


def calls() -> list[dict]:
    return [dict(r) for r in db.conn().execute("SELECT * FROM calls ORDER BY rowid")]


@pytest.fixture
def store():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")


@pytest.fixture
def llm(monkeypatch, store):
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_BASE_URL", "http://relay/v1/")
    monkeypatch.setenv("LLM_API_KEY", "k")


def sse(m: dict) -> bytes:
    return b"data: " + json.dumps(m, ensure_ascii=False).encode() + b"\n\n"


def delta(t, a, b):
    return {"type": "transcript.text.delta", "delta": t, "start_time": a, "end_time": b}


def test_stepfun_sse_sentences_as_they_come(monkeypatch):
    monkeypatch.setenv("STEPFUN_API_KEY", "k")
    x = np.arange(16000, dtype=np.int16)
    out, said = [], anyio.Event()

    async def body():
        yield b": keep-alive\n\n" + sse(delta("你好。", 0, 300))
        with anyio.fail_after(5):
            await said.wait()  # the first sentence is out while the stream goes on
        yield sse(delta("在", 1200, 1300)) + sse({"type": "transcript.text.done", "text": "你好。在"})

    async def handler(req):
        assert (req.method, str(req.url)) == ("POST", stepfun_asr.URL)
        assert req.headers["authorization"] == "Bearer k" and req.headers["user-agent"] == "curl/8"
        sent = json.loads(req.content)["audio"]
        assert base64.b64decode(sent["data"]) == x.tobytes() and sent["input"]["format"]["rate"] == 16000
        return httpx2.Response(200, content=body())

    def on(ds):
        out.append([d["delta"] for d in ds])
        said.set()
    mock(monkeypatch, handler)
    ds = anyio.run(stepfun_asr.sse, x, on)
    assert [d["delta"] for d in ds] == ["你好。", "在"] and out == [["你好。"], ["你好。", "在"]], out  # 在: after a pause


@pytest.mark.parametrize("answer, error", [
    (httpx2.Response(402, text='{"error": "余额不足"}'), 'stepfun sse HTTP 402: {"error": "余额不足"}'),
    (httpx2.Response(200, content=sse(delta("你", 0, 100)) + sse({"type": "error", "message": "风控"})), "stepfun sse: {'type': 'error'"),
    (httpx2.Response(200, content=sse(delta("你", 0, 100))), "stepfun sse: no transcript.text.done after 1 deltas"),
])
def test_stepfun_sse_errors(monkeypatch, answer, error):
    monkeypatch.setenv("STEPFUN_API_KEY", "k")
    mock(monkeypatch, lambda req: answer)
    with pytest.raises(RuntimeError) as e:
        anyio.run(stepfun_asr.sse, np.zeros(160, np.int16))
    assert str(e.value).startswith(error), e.value


def test_stepfun_balance(monkeypatch):
    monkeypatch.setenv("STEPFUN_API_KEY", "k")

    def handler(req):
        assert (req.method, str(req.url), req.headers["authorization"]) == ("GET", stepfun_asr.ACCOUNT, "Bearer k")
        return httpx2.Response(200, json={"balance": "33.92"})
    mock(monkeypatch, handler)
    assert anyio.run(stepfun_asr.balance) == 33.92


def test_llm_call(monkeypatch, llm):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx2.Response(200, json={"choices": [{"message": {"content": '{"title": "t"}'}}], "model": "m-1",
                                          "usage": {"prompt_tokens": 900, "completion_tokens": 40}})
    mock(monkeypatch, handler)
    res, raw = anyio.run(functools.partial(summary.complete, [{"role": "user", "content": "秘密"}], kind="recap"))
    assert res == {"title": "t"} and raw["model"] == "m-1"
    req = seen[0]
    assert str(req.url) == "http://relay/v1/chat/completions" and req.headers["authorization"] == "Bearer k"
    assert json.loads(req.content) == {"model": "m", "messages": [{"role": "user", "content": "秘密"}], "response_format": {"type": "json_object"}}
    (c,) = calls()
    assert (c["kind"], c["engine"], c["error"], c["prompt_tokens"], c["completion_tokens"]) == ("recap", "m", None, 900, 40) and c["ms"] >= 0


@pytest.mark.parametrize("fail, error", [  # what the calls table keeps: as with urllib, never the URL or a body
    (lambda req: httpx2.Response(502, text="echoes the prompt"), "HTTPError: HTTP Error 502: Bad Gateway"),
    (lambda req: (_ for _ in ()).throw(httpx2.ReadTimeout("The read operation timed out", request=req)), "TimeoutError: The read operation timed out"),
    (lambda req: (_ for _ in ()).throw(httpx2.ConnectError("[Errno 111] Connection refused", request=req)),
     "URLError: <urlopen error [Errno 111] Connection refused>"),
])
def test_llm_failures(monkeypatch, llm, fail, error):
    mock(monkeypatch, fail)
    with pytest.raises((urllib.error.URLError, TimeoutError)):
        anyio.run(summary.call, [{"role": "user", "content": "秘密"}])
    (c,) = calls()
    assert c["error"] == error and c["ms"] >= 0
    assert [x["failed"] for x in db.calls("")] == [{error.split(":")[0] if "502" not in error else "502": 1}]


def test_llm_cut_off(monkeypatch, llm):
    """A call cancelled (its task, the server stopping) is kept as such: no answer, no tokens."""
    async def hang(req):
        await anyio.sleep(60)
    mock(monkeypatch, hang)

    async def run():
        with anyio.move_on_after(0.2) as scope:
            await summary.call([{"role": "user", "content": "x"}])
        assert scope.cancelled_caught
    anyio.run(run)
    (c,) = calls()
    assert c["error"].startswith("CancelledError") and c["prompt_tokens"] is None


def test_engine_call_cut_off(monkeypatch, store):
    """An ASR call cancelled is in the calls table (the provider may still bill it) and goes to no fallback."""
    tried = []

    async def hang(x, partial=None):
        tried.append("a")
        await anyio.sleep(60)

    async def fallback(x, partial=None):
        tried.append("b")
        return []
    monkeypatch.setattr(asr, "PROVIDERS", {**asr.PROVIDERS, "a": hang, "b": fallback})
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "b")

    async def run():
        with anyio.move_on_after(0.2):
            await asr.engine_segs(np.zeros(16000, np.int16), [], None, "a", "draft", "LR-P3-116C")
    anyio.run(run)
    assert tried == ["a"]
    assert [(c["kind"], c["engine"], c["device"], c["error"].split(":")[0]) for c in calls()] == [("draft", "a", "LR-P3-116C", "CancelledError")]


def test_errors_in_side_by_side_parts(monkeypatch, store):
    """stepfun runs its SSE and its file job in a TaskGroup: the calls table and the fallback reason keep the failing
    part's own error, as when the file job ran in a thread (not 'ExceptionGroup: unhandled errors in a TaskGroup')."""
    async def sse(x, on=None):
        raise RuntimeError("stepfun sse HTTP 451: refused")

    async def diarize(x):
        await anyio.sleep(60)

    async def ok(x, partial=None):
        return [{"text": "好", "start_ms": 0, "end_ms": 500}]
    monkeypatch.setattr(stepfun_asr, "sse", sse)
    monkeypatch.setattr(asr, "diarize", diarize)
    monkeypatch.setattr(asr, "PROVIDERS", {**asr.PROVIDERS, "volc": ok})
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "volc")
    segs = anyio.run(asr.engine_segs, np.zeros(16000, np.int16), [], None, "stepfun", "final", "LR-P3-116C")
    assert segs[0]["fallback"] == "stepfun: RuntimeError('stepfun sse HTTP 451: refused')", segs[0]
    assert [(c["engine"], c["error"]) for c in calls()] == [("stepfun", "RuntimeError: stepfun sse HTTP 451: refused"), ("volc", None)]

    async def boom(x):
        raise RuntimeError("diarization job j still running after 120 s")

    async def slow(x, on=None):
        await anyio.sleep(60)
    monkeypatch.setattr(asr, "diarize", boom)
    monkeypatch.setattr(stepfun_asr, "sse", slow)
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "")
    with pytest.raises(RuntimeError, match=r"^stepfun: RuntimeError\('diarization job j still running after 120 s'\)$"):
        anyio.run(asr.engine_segs, np.zeros(16000, np.int16), [], None, "stepfun")
    assert calls()[-1]["error"] == "RuntimeError: diarization job j still running after 120 s"


def test_ask_tool_loop(monkeypatch, llm):
    """Over HTTP: a tool call answered from the store, one 429 waited out (Retry-After), the answer with its source."""
    with db.tx() as c:
        db.insert_conversation(c, dict(id="c20260801090000", start="2026-08-01T09:00:00.000", end="2026-08-01T09:20:00.000",
                                       participants=["我", "S1"], speech_s=600, sentences=1, device="phone"))
        db.insert_sentences(c, [dict(id="f1", start="2026-08-01T09:05:00.000", end="2026-08-01T09:05:04.000", speaker="S1",
                                     text="去医院复查", source="phone")])
    replies, bodies = [
        {"tool_calls": [{"id": "t1", "type": "function", "function": {"name": "grep", "arguments": '{"q": "复查"}'}}]},
        429,
        {"content": "8 月 1 日聊过复查 [c20260801090000]。"}], []

    def handler(req):
        bodies.append(json.loads(req.content))
        r = replies.pop(0)
        if r == 429:
            return httpx2.Response(429, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"choices": [{"message": r}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    mock(monkeypatch, handler)
    r = anyio.run(ask.ask, "我什么时候说过复查？", dt.datetime(2026, 10, 2, 12))
    assert r["answer"].startswith("8 月 1 日") and [s["id"] for s in r["sources"]] == ["c20260801090000"], r
    assert [x["tool"] for x in r["calls"]] == ["grep"] and len(bodies) == 3
    tool = bodies[1]["messages"][-1]
    assert tool["role"] == "tool" and "去医院复查" in tool["content"] and bodies[2] == bodies[1], "the 429 sent again as it was"
    assert [c["error"] and c["error"].split(":")[0] for c in calls()] == [None, "HTTPError", None]


def test_diarize(monkeypatch):
    """The file-mode ASR: the window encoded, a presigned upload by the bridge, a job polled until done; StepFun's risk
    control refusing it sends it to Doubao; another failure raises."""
    monkeypatch.setenv("ASR_UPLOAD_BRIDGE_URL", "http://bridge/presign")
    monkeypatch.setenv("ASR_SERVICE_URL", "http://asr")
    monkeypatch.setenv("ASR_UPLOAD_BRIDGE_TOKEN", "bt")
    monkeypatch.setenv("ASR_SERVICE_BASIC_AUTH", "u:p")
    seg = {"start_ms": 0, "end_ms": 500, "speaker": "SPEAKER_0", "text": "你好", "words": []}
    jobs, seen = {}, []

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.url.host == "bridge":
            assert req.headers["authorization"] == "Bearer bt" and json.loads(req.content)["content_type"] == "audio/ogg"
            return httpx2.Response(200, json={"upload_url": "http://up/live.ogg", "download_url": "http://dl/live.ogg"})
        if req.url.host == "up":
            assert req.content[:4] == b"OggS" and req.headers["content-type"] == "audio/ogg"
            return httpx2.Response(200)
        assert req.headers["authorization"] == "Basic " + base64.b64encode(b"u:p").decode()
        if req.method == "POST":
            model = json.loads(req.content)["model"]
            jobs[f"j{len(jobs)}"] = [{"status": "RUNNING"}, outcome(model)]
            return httpx2.Response(200, json={"job_id": f"j{len(jobs) - 1}"})
        return httpx2.Response(200, json=jobs[req.url.path.rsplit("/", 1)[1]].pop(0))
    mock(monkeypatch, handler)
    x = (np.sin(np.arange(16000) / 5) * 3000).astype(np.int16)

    outcome = lambda model: {"status": "COMPLETED", "segments": [dict(seg)]}
    assert anyio.run(asr.diarize, x) == [{**seg, "engine": "stepaudio"}]
    assert seen == [("POST", "/presign"), ("PUT", "/live.ogg"), ("POST", "/audio/transcriptions"), ("GET", "/jobs/j0"), ("GET", "/jobs/j0")]

    outcome = lambda model: {"status": "FAILED", "error": "{'code': 'risk', 'retryable': False}"} if model.startswith("stepaudio") else \
        {"status": "COMPLETED", "segments": [dict(seg)]}
    assert anyio.run(asr.diarize, x) == [{**seg, "engine": "doubao"}]

    outcome = lambda model: {"status": "FAILED", "error": "UPSTREAM_TASK_FAILED"}
    with pytest.raises(RuntimeError, match="^UPSTREAM_TASK_FAILED$"):
        anyio.run(asr.diarize, x)


def test_qwen(monkeypatch):
    """Bailian's file job: the window up lossless into its temporary storage (a policy, a form POST to its OSS), a task
    on the oss:// URL polled until done, its transcription's sentences with speaker ids; a failed task raises."""
    monkeypatch.setattr(qwen_asr, "POLL_S", 0)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dk")
    policy = {"upload_host": "http://oss", "upload_dir": "dashscope-instant/d1", "policy": "p", "signature": "s",
              "oss_access_key_id": "ak", "x_oss_object_acl": "private", "x_oss_forbid_overwrite": "true"}
    result = {"transcripts": [{"channel_id": 0, "text": "你好。好的。", "sentences": [
        {"begin_time": 100, "end_time": 900, "text": "你好。", "speaker_id": 0},
        {"begin_time": 1000, "end_time": 1500, "text": " ", "speaker_id": 1},
        {"begin_time": 1600, "end_time": 2400, "text": "好的。", "speaker_id": 1}]}]}
    seen, polls = [], []

    def handler(req):
        seen.append((req.method, req.url.host, req.url.path))
        if req.url.host == "oss":
            form = req.content.decode("latin-1")
            assert 'name="key"\r\n\r\ndashscope-instant/d1/live.wav\r\n' in form and 'name="Signature"\r\n\r\ns\r\n' in form
            assert form.index('name="file"') > form.index('name="key"') and "\r\n\r\nRIFF" in form, "the file last"
            return httpx2.Response(200)
        if req.url.host == "result":
            return httpx2.Response(200, json=result)
        assert req.headers["authorization"] == "Bearer dk"
        if req.url.path == "/api/v1/uploads":
            assert dict(req.url.params) == {"action": "getPolicy", "model": "qwen-audio-3.1-asr-flash-filetrans"}
            return httpx2.Response(200, json={"data": policy})
        if req.method == "POST":
            body = json.loads(req.content)
            assert req.headers["x-dashscope-async"] == "enable" and req.headers["x-dashscope-ossresourceresolve"] == "enable"
            assert body["input"] == {"file_urls": ["oss://dashscope-instant/d1/live.wav"]}
            assert body["model"] == "qwen-audio-3.1-asr-flash-filetrans" and body["parameters"]["diarization_enabled"] is True
            return httpx2.Response(200, json={"output": {"task_id": "t1", "task_status": "PENDING"}})
        return httpx2.Response(200, json={"output": polls.pop(0)})
    mock(monkeypatch, handler)
    x = (np.sin(np.arange(16000) / 5) * 3000).astype(np.int16)

    polls[:] = [{"task_status": "RUNNING"}, {"task_status": "SUCCEEDED", "results": [
        {"subtask_status": "SUCCEEDED", "transcription_url": "http://result/t1.json"}]}]
    assert anyio.run(asr.qwen, x) == [{"start_ms": 100, "end_ms": 900, "text": "你好。", "speaker": "SPEAKER_0"},
                                          {"start_ms": 1600, "end_ms": 2400, "text": "好的。", "speaker": "SPEAKER_1"}]
    assert [s[1:] for s in seen] == [("dashscope.aliyuncs.com", "/api/v1/uploads"), ("oss", "/"),
                                     ("dashscope.aliyuncs.com", "/api/v1/services/audio/asr/transcription"),
                                     ("dashscope.aliyuncs.com", "/api/v1/tasks/t1"), ("dashscope.aliyuncs.com", "/api/v1/tasks/t1"), ("result", "/t1.json")]

    polls[:] = [{"task_status": "FAILED", "code": "ASR_RESPONSE_HAVE_NO_WORDS", "message": "ASR_RESPONSE_HAVE_NO_WORDS"}]
    assert anyio.run(asr.qwen, x) == [], "a window nobody spoke in (10-19 18:27, 47 s): no sentences, not a failure"
    polls[:] = [{"task_status": "SUCCEEDED", "results": [{"subtask_status": "FAILED", "code": "SUCCESS_WITH_NO_VALID_FRAGMENT"}]}]
    assert anyio.run(asr.qwen, x) == [], "nor one too short to hold a fragment (10-19 19:42, a voice memo's last piece)"

    polls[:] = [{"task_status": "SUCCEEDED", "results": [{"subtask_status": "FAILED", "code": "InvalidFile.DownloadFailed"}]}]
    with pytest.raises(RuntimeError, match="DownloadFailed"):
        anyio.run(asr.qwen, x)


def test_http_as_urllib(monkeypatch):
    """_http (the bridge, the metrics push): urllib's method and errors."""
    seen = []

    def handler(req):
        seen.append(req.method)
        if req.url.host == "down":
            raise httpx2.ConnectError("[Errno 111] Connection refused", request=req)
        return httpx2.Response(404 if req.url.path == "/missing" else 200, content=b"ok")
    mock(monkeypatch, handler)
    assert anyio.run(asr._http, "http://x/a") == b"ok" and anyio.run(asr._http, "http://x/a", b"d") == b"ok"
    assert seen == ["GET", "POST"]
    with pytest.raises(urllib.error.HTTPError) as e:
        anyio.run(asr._http, "http://x/missing")
    assert e.value.code == 404 and asr.failure(e.value) == "HTTPError: HTTP Error 404: Not Found"
    with pytest.raises(urllib.error.URLError) as e:
        anyio.run(asr._http, "http://down/")
    assert asr.failure(e.value) == "URLError: <urlopen error [Errno 111] Connection refused>"
