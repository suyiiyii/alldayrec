"""Connection links: lifecycle, scope, tenant boundaries and canonical paths."""
import hashlib
import json
from urllib.parse import unquote

import anyio
import pytest

from alldayrec import core, db, mcp_links, server, tenant

RPC = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def request(path, method="GET", body=None, token=None, headers=None):
    out = []

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def send(message):
        out.append(message)

    h = {"host": "live.test", **({"authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
    anyio.run(server.app, {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                          "scheme": "https", "path": unquote(path), "raw_path": path.encode(), "query_string": b"",
                          "root_path": "", "headers": [(k.encode(), v.encode()) for k, v in h.items()],
                          "client": ("127.0.0.1", 1), "server": ("test", 443)}, receive, send)
    start = next(x for x in out if x["type"] == "http.response.start")
    raw = b"".join(x.get("body", b"") for x in out)
    return start["status"], json.loads(raw) if raw.startswith((b"{", b"[")) else raw.decode(), dict(start["headers"])


@pytest.fixture
def tenants(tmp_path, monkeypatch):
    ts = [db.open_db(tmp_path / f"{name}.db") for name in ("a", "b")]
    for t, name in zip(ts, ("a", "b")):
        t.id, t.token = name, f"tenant-{name}"
        tenant.call(t, lambda: db.insert_sentences(db.conn(), [dict(id="s1", start="2026-10-01T10:00:00.000",
                        end="2026-10-01T10:00:01.000", speaker="S1", text=f"MARK{t.id} only", source="test")]))
    monkeypatch.setattr(core, "TENANTS", ts)
    return ts


def create(token="tenant-a", label="Grok Bot"):
    status, row, headers = request("/api/mcp-links", "POST", {"label": label}, token)
    assert status == 201 and headers[b"cache-control"] == b"no-store"
    return row


def test_lifecycle_and_storage(tenants, monkeypatch):
    a, _ = tenants
    row, second = create(), create(label="ChatGPT")
    secret = row["path"].rsplit("/", 1)[1]
    assert len(secret) == 43 and secret != a.token
    stored = tenant.call(a, lambda: dict(db.conn().execute("SELECT * FROM mcp_links WHERE id = ?", (row["id"],)).fetchone()))
    assert stored["secret_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in json.dumps(stored) and stored["prefix"] == secret[:8]
    assert stored["last_used_at"] is None
    compare, calls = mcp_links.hmac.compare_digest, []

    def compared(x, y):
        calls.append((x, y))
        return compare(x, y)
    monkeypatch.setattr(mcp_links.hmac, "compare_digest", compared)
    status, result, headers = request(row["path"], "POST", RPC)
    assert status == 200 and len(result["result"]["tools"]) == 12
    assert (stored["secret_hash"], stored["secret_hash"]) in calls
    assert headers[b"cache-control"] == b"no-store"
    listing = request("/api/mcp-links", token=a.token)[1]
    assert len(listing) == 2 and secret not in json.dumps(listing)
    assert next(r for r in listing if r["id"] == row["id"])["last_used_at"]
    for method in ("GET", "DELETE", "PUT", "HEAD"):
        assert request(row["path"], method)[0] == 405
    assert request(row["path"], "POST", RPC, headers={"origin": "https://evil.example"})[0] == 403
    assert request(row["path"], "POST", RPC, headers={"origin": "https://live.test"})[0] == 200
    assert request("/api/mcp-links/" + row["id"], "DELETE", token=a.token)[0] == 204
    invalid = "/mcp/k/" + "x" * 43
    assert request(row["path"], "POST", RPC) == request(invalid, "POST", RPC)
    assert request(row["path"], "POST", RPC, a.token)[0] == 404
    assert request(second["path"], "POST", RPC)[0] == 200
    assert all(secret not in str(k) for k in a.requests)
    assert any(k[1] == "/mcp" for k in a.requests)


def test_scope_and_isolation(tenants):
    a, b = tenants
    row, other = create(), create(b.token)
    call = {**RPC, "method": "tools/call", "params": {"name": "grep", "arguments": {"q": "MARK"}}}
    for link, bearer, yes, no in ((row, b.token, "MARKa", "MARKb"), (other, a.token, "MARKb", "MARKa")):
        status, result, _ = request(link["path"], "POST", call, bearer)
        assert status == 200 and not result["result"]["isError"]
        text = result["result"]["content"][0]["text"]
        assert yes in text and no not in text
    assert [r["id"] for r in request("/api/mcp-links", token=b.token)[1]] == [other["id"]]
    assert request("/api/mcp-links/" + row["id"], "DELETE", token=b.token)[0] == 404
    secret = row["path"].rsplit("/", 1)[1]
    for path in ("/api/mcp-links", "/api/app-setup", "/api/days", "/connect", "/assets/test.js", "/mcp"):
        assert request(path, "POST" if path == "/mcp" else "GET", RPC, secret)[0] == 401
        assert request(path, "POST" if path == "/mcp" else "GET", RPC, headers={"cookie": f"token={secret}"})[0] == 401
    assert request("/api/mcp-links", "POST", {"label": "anonymous"})[0] == 401
    assert request("/api/mcp-links/" + row["id"], "DELETE")[0] == 401


@pytest.mark.parametrize("suffix", ["/../api/app-setup", "/../../api/app-setup", "/%2e%2e/api/app-setup", "%2f..%2fapi/app-setup",
                                    "/%252e%252e%252fapi/app-setup", "/", "/api/days", "\\..\\api\\app-setup"])
def test_path_tricks(tenants, suffix):
    row = create()
    assert request(row["path"] + suffix, "POST", RPC)[0] == 404
    encoded = row["path"].replace("/k/", "/k%2f")
    assert request(encoded, "POST", RPC)[0] == 404
    assert request("/mcp/api/app-setup", "POST", RPC)[0] == 401  # client-normalized ../


def test_management_validation_and_origin(tenants):
    for body in ({}, [], {"label": " "}, {"label": 2}, {"label": "x" * 81}):
        assert request("/api/mcp-links", "POST", body, "tenant-a")[0] == 400
    assert request("/api/mcp-links", "POST", {"label": "ok"}, "tenant-a", {"origin": "https://evil.example"})[0] == 403
    row = create()
    assert request("/api/mcp-links/" + row["id"], "DELETE", token="tenant-a", headers={"origin": "https://evil.example"})[0] == 403
