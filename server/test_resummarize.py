"""Backfill: date bounds, version resume, failure counts, sequential timeout stop and 429 backoff."""
import datetime as dt
import io
import urllib.error

from alldayrec import resummarize as r


def conv(id="a", version=10, mark="done"):
    return {"id": id, "start": "2026-10-26T10:00:00", "state": "closed", "speech_s": 120,
            "summary": {"status": "done", "prompt_version": version, "todos": [{"text": "send", "mark": mark}]}}


def test_backfill_resume_counts_and_backoff(monkeypatch):
    state = {"a": conv(), "b": conv("b"), "current": conv("current", r.PROMPT_VERSION)}
    days, posted, sleeps = [], [], []
    fail = False

    def api(method, path, **params):
        nonlocal fail
        if path == "/api/conversations":
            days.append(params["date"])
            return list(state.values()) if len(days) == 1 else []
        if path == "/api/calls":
            return {"calls": [{"kind": "summary", "n": 2, "failed": {"429": 1} if fail else {}}]}
        id = path.split("/")[3]
        if method == "POST":
            posted.append(id)
            fail = True
            if id == "a":
                state[id] = conv(id, r.PROMPT_VERSION)
            else:
                state[id]["summary"]["error"] = "[resummarize_failed] unsafe marks"
            return {"status": "pending"}
        return state[id]

    monkeypatch.setenv("LIVE_RECORD_URL", "http://localhost")
    monkeypatch.setenv("LIVE_RECORD_TOKEN", "test")
    monkeypatch.setattr(r, "client", lambda *args: api)
    monkeypatch.setattr(r.time, "sleep", sleeps.append)
    got = r.main(["--days", "2", "--delay", "5"])
    today = dt.date.today()
    assert days == [(today - dt.timedelta(days=1)).isoformat(), today.isoformat()]
    assert posted == ["a", "b"] and sleeps == [3, 10, 3]
    assert (got["redone"], got["failed"], got["todos"], got["marked"]) == (1, 1, [2, 2], [2, 2])
    posted.clear(); days.clear()
    assert r.main(["--days", "1", "--dry-run"])["todo"] == 1
    assert posted == [], "dry run and current-version resume do not queue anything"


def test_timeout_stops_before_next_post(monkeypatch):
    posted = []
    def api(method, path, **params):
        if path == "/api/conversations":
            return [conv(), conv("b")]
        if path == "/api/calls":
            return {"calls": []}
        if method == "POST":
            posted.append(path)
        return conv()
    monkeypatch.setenv("LIVE_RECORD_URL", "http://localhost")
    monkeypatch.setenv("LIVE_RECORD_TOKEN", "test")
    monkeypatch.setattr(r, "client", lambda *args: api)
    ticks = iter([0, 0, 0, 2, 2, 2])
    monkeypatch.setattr(r.time, "monotonic", lambda: next(ticks, 2))
    got = r.main(["--days", "1", "--timeout", "1"])
    assert got["failed"] == 1 and len(posted) == 1


def test_http_429_retry(monkeypatch):
    count, sleeps = [], []
    def open(req, timeout):
        count.append(1)
        if len(count) < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "limited", {"Retry-After": "7"}, None)
        return io.BytesIO(b'{"ok": true}')
    monkeypatch.setattr(r.urllib.request, "urlopen", open)
    monkeypatch.setattr(r.time, "sleep", sleeps.append)
    assert r.client("http://localhost", "test", 5, 20)("POST", "/summarize") == {"ok": True}
    assert sleeps == [7, 10]


def test_failed_retry_waits_for_this_attempt_before_next_post(tmp_path, monkeypatch):
    import anyio
    from alldayrec import db, summaries
    from alldayrec import routes_api
    db.open_db(tmp_path / "t.db")
    for id in ("a", "b"):
        with db.tx() as c:
            db.insert_conversation(c, {"id": id, "start": "2026-10-26T10:00:00.000", "end": "2026-10-26T10:03:00.000",
                                       "participants": ["我"], "sentences": 1, "speech_s": 120})
            db.put_summary({"conversation": id, "status": "failed", "error": "old failure"}, c)
    posted, polls, finished = [], [], []
    gate = anyio.Event()
    async def fake(c, tags):
        if c["id"] == "a":
            await gate.wait()
        finished.append(c["id"])
        return {"conversation": c["id"], "status": "done", "prompt_version": r.PROMPT_VERSION, "title": "t", "todos": []}
    def api(method, path, **params):
        if path == "/api/conversations":
            return sorted(db.conversations(), key=lambda c: c["id"])
        if path == "/api/calls":
            return {"calls": []}
        id = path.split("/")[3]
        if method == "POST":
            if posted:
                assert posted[-1] in finished, "the previous model call must finish before the next POST"
            posted.append(id)
            out = anyio.from_thread.run(routes_api.resummarize, id)
            if id == "a":
                assert db.summary(id)["status"] == "pending", "an old failure is not this attempt's outcome"
            return out
        out = anyio.from_thread.run(routes_api.conversation, id)
        if id == "a" and posted and out["summary"]["status"] == "pending":
            polls.append(out["summary"]["status"])
            if len(polls) == 3:
                anyio.from_thread.run_sync(gate.set)
        return out
    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(summaries.summarize_loop)
            with anyio.fail_after(3):
                result = await anyio.to_thread.run_sync(lambda: r.main(["--days", "1", "--delay", "0"]))
            tg.cancel_scope.cancel()
        return result
    monkeypatch.setattr(summaries, "summarize", fake)
    monkeypatch.setattr(r, "client", lambda *args: api)
    monkeypatch.setattr(r.time, "sleep", lambda _: None)
    monkeypatch.setenv("LIVE_RECORD_URL", "http://localhost")
    monkeypatch.setenv("LIVE_RECORD_TOKEN", "test")
    result = anyio.run(run)
    assert result["redone"] == 2 and result["failed"] == 0 and polls[:3] == ["pending"] * 3
