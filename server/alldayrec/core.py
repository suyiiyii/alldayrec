"""What the server's modules share: the process's tenants and state, and the current tenant's conversations as the
pages, the summaries and the speakers read them. A name a test replaces (TENANTS) is read as core.TENANTS."""
import datetime as dt
import time
from pathlib import Path

from alldayrec import db
from alldayrec import tenant
from alldayrec.tenant import T


TENANTS: list[tenant.Tenant] = []  # every tenant of this process (tenant.load at the start); the first one's data has the
                                   # firmware, the APK and the StepFun balance, shared by all


state = {"commit": "", "embed": None, "music": None}  # the process's: what runs, the voiceprint model every tenant uses
                                                     # and music.heard (None: MUSIC_DETECT=0)


NO_CACHE = {"Cache-Control": "no-cache"}  # pages change with deploys; revalidate by etag instead of heuristic caching


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="milliseconds")


def owner_npz() -> Path:
    return T().data / "owner-redimnet2.npz"


def find_conversation(conv_id: str) -> dict | None:
    return db.conversation(conv_id)


def conversation_rows(c: dict) -> list[dict]:
    """Its device's rows in its time; a conversation from before per-device pipelines (no device) takes every row."""
    return db.sentences(c["start"], c["end"], inclusive=True, source=c.get("device"))


def queue_summary(c: dict, keep: bool = False):
    """keep: the current summary stays until the new one is written. Only the newest request may replace it.
    Generations live with the tenant's queue/workers; a restart drops all of them together."""
    old = db.summary(c["id"]) if keep else None
    if not old or old["status"] != "done":
        db.put_summary({"conversation": c["id"], "status": "pending", "error": None})
    elif old.get("error"):
        db.put_summary({**old, "error": None})
    generations = T().summary_generations
    generation = generations[c["id"]] = generations.get(c["id"], 0) + 1
    T().summaries[0].send_nowait({**c, "_summary_generation": generation})


def all_conversations() -> list[dict]:
    """Every closed conversation with its summary, newest first."""
    return db.conversations()


def tag_counts() -> dict[str, int]:
    """Recounted at most once a minute: a backfill summarizes thousands in a row."""
    _tags = T().tags
    if time.time() - _tags["at"] < 60:
        return _tags["counts"]
    n = {}
    for c in all_conversations():
        for t in (c["summary"] or {}).get("tags") or []:
            n[t] = n.get(t, 0) + 1
    _tags.update(at=time.time(), counts=dict(sorted(n.items(), key=lambda kv: -kv[1])))
    return _tags["counts"]
