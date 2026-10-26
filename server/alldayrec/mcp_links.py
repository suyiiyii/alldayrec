"""Revocable, MCP-only credentials. Each tenant keeps hashes in its own store."""
import datetime as dt
import hashlib
import hmac
import secrets

from alldayrec import db, tenant


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def create(label: str) -> dict:
    secret = secrets.token_urlsafe(32)
    row = dict(id=secrets.token_hex(12), label=label, prefix=secret[:8], created_at=now(), last_used_at=None)
    db.conn().execute("INSERT INTO mcp_links VALUES (?, ?, ?, ?, ?, ?)",
                      (row["id"], label, hashlib.sha256(secret.encode()).hexdigest(), row["prefix"], row["created_at"], None))
    return {**row, "path": f"/mcp/k/{secret}"}


def listing() -> list[dict]:
    return [dict(r) for r in db.conn().execute(
        "SELECT id, label, prefix, created_at, last_used_at FROM mcp_links ORDER BY created_at DESC, id")]


def revoke(link_id: str) -> bool:
    return db.conn().execute("DELETE FROM mcp_links WHERE id = ?", (link_id,)).rowcount > 0


def resolve(tenants: list[tenant.Tenant], secret: str) -> tenant.Tenant | None:
    digest = hashlib.sha256(secret.encode()).hexdigest()
    for t in tenants:
        def check():
            # Compare only fixed-length digests; never retain the URL or the secret.
            c = db.conn()
            row = c.execute("SELECT id, secret_hash FROM mcp_links WHERE secret_hash = ?", (digest,)).fetchone()
            if row and hmac.compare_digest(digest, row["secret_hash"]):
                # An atomic update also checks that a concurrent revocation has
                # not deleted the link. Invalid attempts never take a write lock.
                return c.execute("UPDATE mcp_links SET last_used_at = ? WHERE id = ? AND secret_hash = ?",
                                 (now(), row["id"], digest)).rowcount > 0
            return False
        if tenant.call(t, check):
            return t
    return None
