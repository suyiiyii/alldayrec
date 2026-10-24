"""Tenants: each person's own store, recordings and in-memory state, side by side in one process.

The tenant of whatever runs now is in a context variable with no default: a request takes its token's (server.py's
middleware), a background task its own (run_tenant), a thread the caller's (anyio.to_thread copies the context). Code
running outside any tenant gets a LookupError instead of someone else's data. Everything a tenant has lives on its
Tenant; nothing per person stays in a module.

The tenants are in tenants.json beside the server dir (/opt/<instance>/tenants.json, next to its .env), mode 600: it
holds every token, and nothing else does (server/tenants.sh made it from each instance's .env):
    [{"id": "main", "token": "...", "data": "/opt/alldayrec/data", "recordings": "/opt/alldayrec/recordings"}, ...]
A process with one tenant whose token is empty runs without auth (loopback only). "lang" (optional): the language the
models write that tenant's summaries, recaps and answers in, zh or en; without it UI_LANG, else zh.
"""
import contextvars
import hmac
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from pipeline_state import PipelineState

current: contextvars.ContextVar["Tenant"] = contextvars.ContextVar("tenant")
FILE = Path(__file__).resolve().parent.parent / "tenants.json"
LANGS = ("zh", "en")


@dataclass(eq=False)
class Tenant:
    id: str     # the metrics' instance label, the logs' tag
    token: str  # what its apps and pages send (Bearer or cookie); empty: no auth, loopback only, the only tenant
    data: Path  # live.db, the passes trace, owner-redimnet2.npz, voice clips
    recordings: Path
    lang: str = "zh"  # what the models write in (summary.lang_line); PUT /api/lang changes it until a restart
    db: Path = None  # data / live.db unless given (a test's own file)
    # its state in memory: what its pages see and what its pipelines run on
    clients: set = field(default_factory=set)       # its WebSockets
    events: list = field(default_factory=list)      # its system log, the last 300
    devices: dict[str, "PipelineState"] = field(default_factory=dict)
    pipelines: set = field(default_factory=set)     # the devices whose pipeline runs (server.lasting)
    dias: list = field(default_factory=list)        # its pipelines' diarists: a rename teaches them a name
    watch_until: float = 0.0                        # a page of its watches until then: drafts, the live analysis
    wake_at: float = 0.0                            # ... and came to the front then
    chunks: dict | None = None                      # its newest upload
    owner: dict | None = None                       # 我's voiceprint picked on its card (selfvoice), for the next conversations
    read_to: dict = field(default_factory=dict)     # audio.stream: per device, the last piece its reader has finished with
    tags: dict = field(default_factory=lambda: {"at": 0.0, "counts": {}})
    speakers: dict = field(default_factory=lambda: {"at": 0.0, "groups": None, "vec": {}})
    summaries: tuple = None                         # (send, receive): its conversations waiting for a summary
    tg: "anyio.abc.TaskGroup | None" = None         # its tasks (run_tenant): pipelines, live analyses, loops
    scope: anyio.CancelScope | None = None          # cancelled: the tenant stops, every task of it with it

    def __post_init__(self):
        self.data, self.recordings = Path(self.data), Path(self.recordings)
        self.db = Path(self.db) if self.db else self.data / "live.db"
        self.summaries = anyio.create_memory_object_stream(math.inf)

    def __repr__(self):
        return f"Tenant({self.id})"  # never the token

    def __del__(self):  # a test's or a script's tenant, gone: its queue with it (an unclosed stream warns)
        for x in self.summaries or ():
            x.close()


def T() -> Tenant:
    """The tenant of what runs now; LookupError outside any."""
    return current.get()


def enter(t: Tenant) -> Tenant:
    """t is the tenant of the code that runs from here on in this context (and of the tasks and threads it starts)."""
    current.set(t)
    return t


def call(t: Tenant, f, *args):
    """f(*args) as t, here: a global loop's turn for each tenant (metrics)."""
    return contextvars.copy_context().run(lambda: (current.set(t), f(*args))[1])


def load(path: Path = FILE) -> list[Tenant]:
    """The tenants file's tenants: one id and one token each (a token may be empty only for the only tenant)."""
    if not path.is_file():
        raise SystemExit(f"{path}: no tenants file (see tenant.py)")
    if path.stat().st_mode & 0o077:
        raise SystemExit(f"{path}: others can read it, and it holds every token: chmod 600")
    ts = [Tenant(x["id"], x["token"], x["data"], x["recordings"], x.get("lang") or os.environ.get("UI_LANG") or "zh")
          for x in json.loads(path.read_text())]
    if bad := [t.id for t in ts if t.lang not in LANGS]:
        raise SystemExit(f"{path}: lang (or UI_LANG) of {', '.join(bad)} is not one of {', '.join(LANGS)}")
    each = [[getattr(t, k) for t in ts] for k in ("id", "token", "data", "recordings")]  # two sharing a store would see each other's
    if not ts or any(len(set(x)) < len(x) for x in each) or (len(ts) > 1 and not all(each[1])):
        raise SystemExit(f"{path}: at least one tenant, ids, tokens and dirs one each, and every tenant a token when there are several")
    return ts


def by_token(ts: list[Tenant], token: str | None) -> Tenant | None:
    """The tenant this token is; the only one when it has none (no auth)."""
    if len(ts) == 1 and not ts[0].token:
        return ts[0]
    return next((t for t in ts if token and hmac.compare_digest(token, t.token)), None)
