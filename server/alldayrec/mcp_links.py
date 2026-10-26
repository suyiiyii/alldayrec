"""Revocable, MCP-only credentials. Each tenant keeps hashes in its own store."""
import datetime as dt
import hashlib
import hmac
import secrets
import time

from alldayrec import db, mcp_server, tenant

MAX_LINKS = 20
REVOKE_WAIT_S = 2.0


class LimitReached(Exception):
    """A tenant must revoke a link before making another."""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def create(label: str) -> dict:
    secret = secrets.token_urlsafe(32)
    row = dict(id=secrets.token_hex(12), label=label, prefix=secret[:8], created_at=now(), last_used_at=None)
    with db.tx() as c:
        if c.execute("SELECT count(*) FROM mcp_links").fetchone()[0] >= MAX_LINKS:
            raise LimitReached
        c.execute("INSERT INTO mcp_links VALUES (?, ?, ?, ?, ?, ?)",
                  (row["id"], label, hashlib.sha256(secret.encode()).hexdigest(), row["prefix"], row["created_at"], None))
    return {**row, "path": f"/mcp/k/{secret}"}


def listing() -> list[dict]:
    return [dict(r) for r in db.conn().execute(
        "SELECT id, label, prefix, created_at, last_used_at FROM mcp_links ORDER BY created_at DESC, id LIMIT ?", (MAX_LINKS,))]


def revoke(link_id: str) -> bool:
    return db.conn().execute("DELETE FROM mcp_links WHERE id = ?", (link_id,)).rowcount > 0


def resolve(tenants: list[tenant.Tenant], secret: str) -> tuple[tenant.Tenant, str] | None:
    digest = hashlib.sha256(secret.encode()).hexdigest()
    for t in tenants:
        def check():
            # Compare only fixed-length digests; never retain the URL or the secret.
            c = db.conn()
            row = c.execute("SELECT id, secret_hash FROM mcp_links WHERE secret_hash = ?", (digest,)).fetchone()
            if row and hmac.compare_digest(digest, row["secret_hash"]):
                return row["id"]
            return None
        if link_id := tenant.call(t, check):
            return t, link_id
    return None


def handle(link_id: str, msg, headers):
    """After the body and DB slot, lease execution with a live autocommit check.

    The condition protects checking + registration, not the whole tool: readers
    run concurrently and never take a SQLite write transaction. Revoke deletes
    first, then waits for this link's leases (including the short usage update).
    """
    t, c = tenant.T(), db.conn()
    with t.mcp_gate:
        row = c.execute("SELECT last_used_at FROM mcp_links WHERE id = ?", (link_id,)).fetchone()
        if row is None:
            return 404, None
        t.mcp_active[link_id] = t.mcp_active.get(link_id, 0) + 1
    try:
        status, out = mcp_server.handle(msg, headers)
        if 200 <= status < 300 and (out is None or ("error" not in out and not out.get("result", {}).get("isError"))):
            at = now()
            if row["last_used_at"] is None or dt.datetime.fromisoformat(at) - dt.datetime.fromisoformat(row["last_used_at"]) >= dt.timedelta(minutes=1):
                before = (dt.datetime.fromisoformat(at) - dt.timedelta(minutes=1)).isoformat(timespec="seconds")
                c.execute("UPDATE mcp_links SET last_used_at = ? WHERE id = ? AND (last_used_at IS NULL OR last_used_at <= ?)",
                          (at, link_id, before))
        return status, out
    finally:
        with t.mcp_gate:
            t.mcp_active[link_id] -= 1
            if not t.mcp_active[link_id]:
                del t.mcp_active[link_id]
            t.mcp_gate.notify_all()


def drained(link_id: str, deadline: float) -> bool:
    t = tenant.T()
    with t.mcp_gate:
        return t.mcp_gate.wait_for(lambda: not t.mcp_active.get(link_id), timeout=max(0, deadline - time.monotonic()))
