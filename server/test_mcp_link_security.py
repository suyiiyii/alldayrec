"""Security boundaries through real loopback HTTP, with deterministic revocation barriers."""
import functools
import json
import logging
import os
import socket
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import anyio
import httpx2
import pytest
import uvicorn

from alldayrec import db, mcp_links, mcp_server, perf, server, tenant, threads
from test_mcp_links import RPC, create, request, tenants  # shared synthetic tenant fixture

CIDS = ("c20261001100000", "c20261001110000")


def tool(name, **arguments):
    return {**RPC, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


@pytest.fixture
def seeded(tenants):
    for t, cid in zip(tenants, CIDS):
        def seed():
            start = "2026-10-01T10:00:00.000" if t.id == "a" else "2026-10-01T11:00:00.000"
            with db.tx() as c:
                db.insert_conversation(c, dict(id=cid, start=start, end=start[:14] + "01:00.000", participants=["我"],
                                               speech_s=1, sentences=1, device="test"))
                db.insert_sentences(c, [dict(id="row-" + t.id, start=start, end=start, speaker="我", text="PRIVATE-" + t.id, source="test")])
                db.put_summary(dict(conversation=cid, status="done", prompt_version=8, title="PRIVATE-" + t.id,
                                    summary="PRIVATE-" + t.id, todos=[dict(owner="我", text="TASK-" + t.id, at=None)]), c)
        tenant.call(t, seed)
    return tenants


@asynccontextmanager
async def live_http():
    """The real app and HTTP parser, no lifespan/model downloads or external instances."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        host, port = sock.getsockname()
        runner = uvicorn.Server(uvicorn.Config(server.app, lifespan="off", access_log=False, log_config=None,
                                              timeout_graceful_shutdown=1))
        async with anyio.create_task_group() as tg:
            tg.start_soon(functools.partial(runner.serve, sockets=[sock]))
            with anyio.fail_after(5):
                while not runner.started:
                    await anyio.sleep(0.001)
            try:
                async with httpx2.AsyncClient(base_url=f"http://{host}:{port}", timeout=5, trust_env=False) as client:
                    yield client
            finally:
                runner.should_exit = True


async def new_link(c, token="tenant-a"):
    r = await c.post("/api/mcp-links", json={"label": "security check"}, headers={"authorization": "Bearer " + token})
    assert r.status_code == 201
    return r.json()


async def revoke(c, link, token="tenant-a"):
    return await c.delete("/api/mcp-links/" + link["id"], headers={"authorization": "Bearer " + token})


async def wire_start(c, path, method="POST", body=RPC):
    u = urlsplit(str(c.base_url))
    stream = await anyio.connect_tcp(u.hostname, u.port)
    raw = json.dumps(body).encode()
    head = (f"{method} {path} HTTP/1.1\r\nHost: {u.netloc}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n").encode()
    return stream, head, raw


async def wire_reply(stream):
    raw = b""
    with anyio.fail_after(5):
        try:
            while True:
                raw += await stream.receive()
        except (anyio.EndOfStream, anyio.BrokenResourceError):
            pass
    await stream.aclose()
    if not raw:
        return 0, b"", b""  # oversized HTTP request rejected by closing the transport
    head, body = raw.split(b"\r\n\r\n", 1)
    return int(head.split(b" ", 2)[1]), body, head.lower()


async def until(predicate):
    with anyio.fail_after(5):
        while not predicate():
            await anyio.sleep(0.001)


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_http_delayed_body_revocation(seeded, monkeypatch, write):
    """Headers authenticate first; after a 204, finishing the body cannot read or write."""
    async def run():
        authenticated = anyio.Event()
        original = mcp_links.resolve

        def observed(*args):
            result = original(*args)
            if result:
                anyio.from_thread.run_sync(authenticated.set)
            return result
        monkeypatch.setattr(mcp_links, "resolve", observed)
        async with live_http() as c:
            link = await new_link(c)
            msg = tool("mark_todo", id=CIDS[0], text="TASK-a", mark="done") if write else tool("grep", q="PRIVATE-a")
            stream, head, raw = await wire_start(c, link["path"], body=msg)
            async with stream:
                await stream.send(head + raw[:1])
                with anyio.fail_after(5):
                    await authenticated.wait()
                assert (await revoke(c, link)).status_code == 204
                await stream.send(raw[1:])
                status, body, headers = await wire_reply(stream)
            assert status == 404 and body == b"" and b"cache-control: no-store" in headers
            assert tenant.call(seeded[0], db.closed_todos) == {}
    anyio.run(run)


@pytest.mark.parametrize("queued", [False, True], ids=["running-tool", "db-queue"])
def test_http_revoke_waits_for_execution_and_response(seeded, monkeypatch, queued):
    """Only an executing lease delays 204; DB-queued calls hold no lease and fail later."""
    async def run():
        entered, release, deleted, drained = (anyio.Event() for _ in range(4))
        original_call, original_resolve = mcp_server.call, mcp_links.resolve
        original_revoke, original_drained = mcp_links.revoke, mcp_links.drained
        def observed_revoke(*args):
            result = original_revoke(*args)
            anyio.from_thread.run_sync(deleted.set)
            return result
        def observed_drained(*args):
            anyio.from_thread.run_sync(drained.set)
            return original_drained(*args)
        monkeypatch.setattr(mcp_links, "revoke", observed_revoke)
        monkeypatch.setattr(mcp_links, "drained", observed_drained)
        if not queued:
            def blocked(name, args):
                anyio.from_thread.run_sync(entered.set)
                anyio.from_thread.run(release.wait)
                return original_call(name, args)
            monkeypatch.setattr(mcp_server, "call", blocked)
        else:
            monkeypatch.setattr(threads, "DB", anyio.CapacityLimiter(1))
            def observed(*args):
                result = original_resolve(*args)
                if result:
                    anyio.from_thread.run_sync(entered.set)
                return result
            monkeypatch.setattr(mcp_links, "resolve", observed)
        async with live_http() as c:
            link = await new_link(c)
            stream, head, raw = await wire_start(c, link["path"], body=tool("mark_todo", id=CIDS[0], text="TASK-a", mark="done"))
            await stream.send(head + raw[:1])
            if queued:
                await entered.wait()
                await threads.DB.acquire()
                try:
                    await stream.send(raw[1:])
                    await until(lambda: threads.DB.statistics().tasks_waiting == 1)
                    with anyio.fail_after(1):
                        assert (await revoke(c, link)).status_code == 204
                finally:
                    threads.DB.release()
                status, body, _ = await wire_reply(stream)
                assert status == 404 and body == b""
            else:
                await stream.send(raw[1:])
                with anyio.fail_after(5):
                    await entered.wait()
                completed = anyio.Event()
                async def revoking():
                    assert (await revoke(c, link)).status_code == 204
                    completed.set()
                async with anyio.create_task_group() as tg:
                    tg.start_soon(revoking)
                    try:
                        with anyio.fail_after(5):
                            await deleted.wait()  # deletion is not held behind a tool transaction
                            await drained.wait()
                        assert not completed.is_set()
                    finally:
                        release.set()
                    status, body, _ = await wire_reply(stream)
                    assert status == 200 and not json.loads(body)["result"]["isError"]
                assert completed.is_set()
            assert list(tenant.call(seeded[0], db.closed_todos).values()) == ([] if queued else ["done"])
            assert (await c.post(link["path"], json=RPC)).status_code == 404
    anyio.run(run)


def test_http_initialized_session_revoked_and_uniform_404(tenants):
    async def run():
        async with live_http() as c:
            link = await new_link(c)
            headers = {"Mcp-Session-Id": "same-initialized-client"}
            r = await c.post(link["path"], json={**RPC, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}, headers=headers)
            assert r.status_code == 200 and "mcp-session-id" not in r.headers
            assert (await revoke(c, link)).status_code == 204
            unknown = "/mcp/k/" + "z" * 43
            # Bounded completion check, no fragile microsecond/ratio assertion.
            with anyio.fail_after(10):
                for _ in range(5):
                    replies = [await c.post(path, json=RPC, headers=headers) for path in (link["path"], unknown)]
                    for r in replies:
                        assert r.status_code == 404 and r.content == b"" and r.headers["cache-control"] == "no-store"
                        assert "set-cookie" not in r.headers and "location" not in r.headers
                    assert replies[0].headers.get("content-length") == replies[1].headers.get("content-length")
    anyio.run(run)


def test_http_raw_path_and_method_matrix(tenants):
    async def run():
        async with live_http() as c:
            link = await new_link(c)
            path = link["path"]
            secret = path.rsplit("/", 1)[1]
            variants = [path + "/../api/app-setup", path + "/%2e%2e/api/app-setup", path + "%2f..%2fapi/app-setup",
                        path + "/%252e%252e%252fapi/app-setup", path + "/", path + ";x=1", path + "/;x=1",
                        path.replace("/k/", "/k//"), path.replace("/mcp/", "//mcp/"), path.replace("/mcp/", "/MCP/"),
                        path.replace("/k/", "/K/"), path + "界", path + "%00", "/mcp/k/" + "a" * 65536,
                        "/mcp/k/%" + format(ord(secret[0]), "02x") + secret[1:], path.replace("/mcp/", "/%6dcp/"),
                        path.replace("/k/", "/k%2f"), "/mcp/api/app-setup", "/api/app-setup"]
            for method in ("POST", "OPTIONS", "HEAD", "GET", "DELETE"):
                for variant in variants:
                    stream, head, raw = await wire_start(c, variant, method)
                    await stream.send(head + raw)
                    status, body, _ = await wire_reply(stream)
                    assert status in (400, 401, 404) or (status == 0 and len(variant) > 16384), (method, status)
                    assert b"PRIVATE-" not in body and path.encode() not in body
            for method in ("OPTIONS", "HEAD", "GET", "DELETE"):
                assert (await c.request(method, path)).status_code == 405
            # httpx normalizes dot segments before the server sees them.
            assert (await c.post(path + "/../api/app-setup", json=RPC)).status_code == 404
    anyio.run(run)


def test_http_websocket_origin_cookie_and_no_login(tenants):
    async def run():
        async with live_http() as c:
            link = await new_link(c)
            for path in (link["path"], link["path"] + "/..", "/ws"):
                r = await c.get(path, headers={"connection": "Upgrade", "upgrade": "websocket", "sec-websocket-version": "13",
                                              "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="})
                assert r.status_code == 403
            cookie = {"cookie": "token=tenant-a", "origin": "https://evil.example"}
            assert (await c.post("/api/mcp-links", json={"label": "blocked"}, headers=cookie)).status_code == 403
            assert (await c.delete("/api/mcp-links/" + link["id"], headers=cookie)).status_code == 403
            for path in ("/api/mcp-links", link["path"]):
                r = await c.options(path, headers={**cookie, "access-control-request-method": "POST", "access-control-request-headers": "content-type"})
                assert r.status_code == 405 and "access-control-allow-origin" not in r.headers
            r = await c.post(link["path"] + "?token=tenant-b", json=RPC)
            assert r.status_code == 200 and "set-cookie" not in r.headers and "location" not in r.headers
            assert (await c.get("/api/app-setup")).status_code == 401
            secret = link["path"].rsplit("/", 1)[1]
            assert (await c.get("/api/app-setup", params={"token": secret})).status_code == 401
    anyio.run(run)


def test_http_cross_tenant_conversation_transcript_and_write(seeded):
    async def run():
        async with live_http() as c:
            for index, token in enumerate(("tenant-a", "tenant-b")):
                link = await new_link(c, token)
                other = 1 - index
                conflicting = {"cookie": "token=" + ("tenant-b" if index == 0 else "tenant-a")}
                for name in ("conversation", "transcript", "mark_todo"):
                    args = {"id": CIDS[other]}
                    if name == "mark_todo":
                        args.update(text="TASK-" + ("b" if other else "a"), mark="done")
                    r = await c.post(link["path"], json=tool(name, **args), headers=conflicting)
                    assert r.status_code == 200 and r.json()["result"]["isError"]
                    assert "PRIVATE-" + ("b" if other else "a") not in r.text
                r = await c.post(link["path"], json=tool("grep", q="PRIVATE-"), headers=conflicting)
                assert "PRIVATE-" + ("a" if index == 0 else "b") in r.text
                assert "PRIVATE-" + ("b" if index == 0 else "a") not in r.text
            assert all(tenant.call(t, db.closed_todos) == {} for t in seeded)
    anyio.run(run)


def test_link_quota_atomic_and_list_bounded(tenants):
    async def run():
        async with live_http() as c:
            codes = []
            async def add():
                r = await c.post("/api/mcp-links", json={"label": "quota"}, headers={"authorization": "Bearer tenant-a"})
                if r.status_code == 409:
                    assert r.json()["detail"].startswith("[mcp_link_limit]")
                codes.append(r.status_code)
            async with anyio.create_task_group() as tg:
                for _ in range(23):
                    tg.start_soon(add)
            assert sorted(codes) == [201] * 20 + [409] * 3
            rows = (await c.get("/api/mcp-links", headers={"authorization": "Bearer tenant-a"})).json()
            assert len(rows) == 20
            assert (await revoke(c, rows[0])).status_code == 204
            await new_link(c)
            await new_link(c, "tenant-b")  # the other tenant has its own quota
    anyio.run(run)
    # An older store can contain more than the new quota; the response is still bounded.
    def legacy():
        db.conn().execute("INSERT INTO mcp_links VALUES ('legacy', 'legacy', 'legacy-hash', 'prefix', '2000-01-01', NULL)")
        assert len(mcp_links.listing()) == 20
    tenant.call(tenants[0], legacy)


def test_last_used_success_only_and_once_per_minute(tenants, monkeypatch):
    a = tenants[0]
    link = create()
    for method in ("GET", "DELETE", "HEAD", "OPTIONS"):
        assert request(link["path"], method)[0] == 405
    assert request(link["path"], "POST", RPC, headers={"origin": "https://evil.example"})[0] == 403
    for bad in ({**RPC, "method": "unknown"}, tool("unknown"), {**RPC, "params": {"_meta": []}}):
        request(link["path"], "POST", bad)
    assert request("/api/mcp-links", token=a.token)[1][0]["last_used_at"] is None
    statements = []
    def check():
        c = db.conn()
        c.set_trace_callback(statements.append)
        try:
            for at in ("2026-10-01T10:00:00+00:00", "2026-10-01T10:00:59+00:00", "2026-10-01T10:01:00+00:00"):
                monkeypatch.setattr(mcp_links, "now", lambda: at)
                assert mcp_links.handle(link["id"], RPC, {})[0] == 200
            assert mcp_links.listing()[0]["last_used_at"] == "2026-10-01T10:01:00+00:00"
        finally:
            c.set_trace_callback(None)
    tenant.call(a, check)
    assert len([s for s in statements if s.startswith("UPDATE mcp_links SET last_used_at")]) == 2


def test_http_errors_logs_metrics_and_secret_lifecycle(seeded, monkeypatch, caplog):
    async def run():
        caplog.set_level(logging.INFO, logger="live")
        monkeypatch.setattr(perf, "SLOW_MS", 0)
        async with live_http() as c:
            link = await new_link(c)
            path = link["path"]
            secret = path.rsplit("/", 1)[1]
            rows = (await c.get("/api/mcp-links", headers={"authorization": "Bearer tenant-a"})).json()
            assert secret not in json.dumps(rows) and all("path" not in r and "secret_hash" not in r for r in rows)
            for payload in (RPC, {**RPC, "params": {"_meta": ["x"]}}, {**RPC, "params": {"_meta": None}}):
                r = await c.post(path, json=payload)
                assert r.status_code in (200, 400) and path not in r.text
                assert r.headers["cache-control"] == "no-store" and "set-cookie" not in r.headers
            r = await c.post(path, content=b"{broken", headers={"content-type": "application/json"})
            assert r.status_code == 400 and path not in r.text
            original = mcp_server.TOOLS["names"]
            def broken():
                raise RuntimeError("synthetic tool failure")
            monkeypatch.setitem(mcp_server.TOOLS, "names", (broken, *original[1:]))
            r = await c.post(path, json=tool("names"))
            assert r.status_code == 500 and path not in r.text
            assert r.headers["cache-control"] == "no-store"
            assert secret not in caplog.text and path not in caplog.text
            def stored():
                rows = list(db.conn().execute("SELECT route FROM perf_requests"))
                assert rows and all(secret not in r[0] for r in rows)
                assert secret not in "\n".join(db.conn().iterdump())
            tenant.call(seeded[0], stored)
            assert all(secret not in str(key) for key in seeded[0].requests)
            assert (await revoke(c, link)).status_code == 204
    anyio.run(run)


def test_http_revoke_waits_for_response_send(tenants, monkeypatch):
    """A stalled transport cannot delay revocation: execution has already finished."""
    async def run():
        ready, release = anyio.Event(), anyio.Event()
        app = server.app
        async def stalled_send(scope, receive, send):
            async def sending(message):
                if scope["path"].startswith("/mcp/k/") and message["type"] == "http.response.body":
                    ready.set()
                    await release.wait()
                await send(message)
            await app(scope, receive, sending)
        monkeypatch.setattr(server, "app", stalled_send)
        async with live_http() as c:
            link = await new_link(c)
            async def calling():
                assert (await c.post(link["path"], json=RPC)).status_code == 200
            async with anyio.create_task_group() as tg:
                tg.start_soon(calling)
                with anyio.fail_after(5):
                    await ready.wait()
                try:
                    with anyio.fail_after(1):
                        assert (await revoke(c, link)).status_code == 204
                    assert not release.is_set()
                finally:
                    release.set()
    anyio.run(run)


def test_link_read_does_not_block_autocommit_write(seeded, monkeypatch):
    """A real grep plus a controlled slow-read interval must not own SQLite's write lock."""
    async def run():
        entered = anyio.Event()
        original = mcp_server.call
        def slow(name, args):
            anyio.from_thread.run_sync(entered.set)
            time.sleep(0.8)  # a coarse, deterministic slow read; no microsecond timing assertion
            return original(name, args)
        monkeypatch.setattr(mcp_server, "call", slow)
        async with live_http() as c:
            link = await new_link(c)
            elapsed = []
            async def reading():
                start = time.monotonic()
                r = await c.post(link["path"], json=tool("grep", q="not present"))
                elapsed.append(time.monotonic() - start)
                assert r.status_code == 200 and not r.json()["result"]["isError"]
            async with anyio.create_task_group() as tg:
                tg.start_soon(reading)
                with anyio.fail_after(5):
                    await entered.wait()
                start = time.monotonic()
                # Match the real failure: an autocommit write on the event loop thread.
                tenant.call(seeded[0], db.put_kv, "concurrent-write", "ok")
                waited = time.monotonic() - start
            assert waited < elapsed[0] / 3, (waited, elapsed[0])
    anyio.run(run)


def test_http_link_reads_run_concurrently_and_revoke_is_per_link(tenants, monkeypatch):
    async def run():
        entered, release = anyio.Event(), anyio.Event()
        original = mcp_server.call
        def slow(name, args):
            if name == "grep":
                anyio.from_thread.run_sync(entered.set)
                anyio.from_thread.run(release.wait)
            return original(name, args)
        monkeypatch.setattr(mcp_server, "call", slow)
        async with live_http() as c:
            link, other = await new_link(c), await new_link(c)
            async def reading():
                assert (await c.post(link["path"], json=tool("grep", q="not present"))).status_code == 200
            async with anyio.create_task_group() as tg:
                tg.start_soon(reading)
                with anyio.fail_after(5):
                    await entered.wait()
                try:
                    with anyio.fail_after(1):
                        assert (await c.post(link["path"], json=tool("names"))).status_code == 200
                        assert (await revoke(c, other)).status_code == 204
                    assert not release.is_set()
                finally:
                    release.set()
    anyio.run(run)


def test_http_revoke_timeout_reports_pending(tenants, monkeypatch):
    async def run():
        entered, release = anyio.Event(), anyio.Event()
        monkeypatch.setattr(mcp_links, "REVOKE_WAIT_S", 0.1)
        original = mcp_server.call
        def slow(*args):
            anyio.from_thread.run_sync(entered.set)
            anyio.from_thread.run(release.wait)
            return original(*args)
        monkeypatch.setattr(mcp_server, "call", slow)
        async with live_http() as c:
            link = await new_link(c)
            async def calling():
                assert (await c.post(link["path"], json=tool("names"))).status_code == 200
            async with anyio.create_task_group() as tg:
                tg.start_soon(calling)
                with anyio.fail_after(5):
                    await entered.wait()
                try:
                    with anyio.fail_after(1):
                        r = await revoke(c, link)
                    assert r.status_code == 202 and r.json() == {"pending": True}
                    assert r.headers["cache-control"] == "no-store"
                    assert (await c.post(link["path"], json=RPC)).status_code == 404
                finally:
                    release.set()
            assert not tenants[0].mcp_active
    anyio.run(run)


@pytest.mark.skipif(os.environ.get("MCP_BROWSER") != "1", reason="browser regression runs in the mcp-browser CI job")
@pytest.mark.parametrize("language", ["en", "zh"])
def test_browser_secret_once_not_in_storage(tenants, monkeypatch, language):
    """Real /connect UI; all browser output is booleans, never the generated credential."""
    async def run():
        session = "mcp-security-" + str(os.getpid()) + "-" + language
        async def browser(*args, script=None):
            r = await anyio.run_process(["agent-browser", "--session", session, *args],
                                        input=script.encode() if script else None)
            return r.stdout.decode()
        async with live_http() as c:
            origin = str(c.base_url).rstrip("/")
            try:
                await browser("open", origin + "/api/health")
                await browser("eval", "--stdin", script="(async()=>{await fetch('/?token=tenant-a');localStorage.lang=" + json.dumps(language) + ";localStorage.setItem('connect.address','https://alldayrec.example.com');return true})()")
                await browser("open", origin + "/connect#ai")
                await browser("wait", "#link-label")
                await browser("fill", "#link-label", "Browser security check")
                await browser("click", ".link-controls button")
                await browser("wait", "#fresh-link")
                result = await browser("eval", "--stdin", script=r"""(()=>{
                  const value=document.querySelector('#fresh-link').value;
                  const secret=value.split('/').pop();
                  return {shown:/\/mcp\/k\/[A-Za-z0-9_-]{43}$/.test(value),
                    local:JSON.stringify(localStorage).includes(secret),session:JSON.stringify(sessionStorage).includes(secret)};
                })()""")
                assert json.loads(result) == {"shown": True, "local": False, "session": False}
                await browser("click", ".fresh-link button:last-child")
                await browser("eval", "location.reload()")
                await browser("wait", ".link-list li")
                result = await browser("eval", "--stdin", script=r"""(async()=>{
                  const rows=await(await fetch('/api/mcp-links')).json();
                  return {shown:!!document.querySelector('#fresh-link'),
                    leaked:/\/mcp\/k\/[A-Za-z0-9_-]{43}/.test(document.body.textContent+JSON.stringify(localStorage)+JSON.stringify(sessionStorage)),
                    metadataOnly:rows.length===1 && !('path' in rows[0]) && !('secret_hash' in rows[0])};
                })()""")
                assert json.loads(result) == {"shown": False, "leaked": False, "metadataOnly": True}
                rows = (await c.get("/api/mcp-links", headers={"authorization": "Bearer tenant-a"})).json()
                pending = await new_link(c)
                await browser("eval", "location.reload()")
                await browser("wait", ".link-list li:nth-child(2)")
                entered, release = anyio.Event(), anyio.Event()
                original = mcp_server.call
                def held(*args):
                    anyio.from_thread.run_sync(entered.set)
                    anyio.from_thread.run(release.wait)
                    return original(*args)
                monkeypatch.setattr(mcp_server, "call", held)
                monkeypatch.setattr(mcp_links, "REVOKE_WAIT_S", 0.1)
                async def calling():
                    assert (await c.post(pending["path"], json=tool("names"), timeout=None)).status_code == 200
                async with anyio.create_task_group() as tg:
                    tg.start_soon(calling)
                    try:
                        with anyio.fail_after(5):
                            await entered.wait()
                        await browser("eval", "[...document.querySelectorAll('.link-list li')].find(x=>x.querySelector('b').textContent==='security check').querySelector('button').id='pending-revoke'")
                        await browser("click", "#pending-revoke")
                        await browser("dialog", "accept")
                        await browser("wait", "#link-notice")
                        result = json.loads(await browser("eval", "({notice:!!document.querySelector('#link-notice').textContent, remaining:document.querySelectorAll('.link-list li').length})"))
                        assert result == {"notice": True, "remaining": 1}
                        if directory := os.environ.get("MCP_SCREENSHOTS"):
                            await browser("set", "viewport", "390", "844")
                            await browser("eval", "document.querySelector('.links').scrollIntoView({block:'start'})")
                            await browser("screenshot", directory + "/connect-revoke-pending-" + language + "-20261026.png")
                    finally:
                        release.set()
                assert (await revoke(c, rows[0])).status_code == 204
            finally:
                await browser("close")
    anyio.run(run)
