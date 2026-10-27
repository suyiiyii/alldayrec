"""The server's store: one SQLite file per tenant (tenant.py), the current tenant's. Audio (uploaded pieces, voice
clips) stays on disk; the passes trace stays a jsonl log. One connection per thread and file (the summary and speaker
work runs in threads, shared by the tenants), WAL so reads never wait for a write; every multi-step change is one `tx()`.
"""
import datetime as dt
import hashlib
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from alldayrec import tenant

SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_links (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, secret_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT);
CREATE TABLE IF NOT EXISTS sentences (       -- final rows; a finalization pass replaces the ones starting in [from, to)
    id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, speaker TEXT, speaker_confidence REAL, text TEXT,
    source TEXT, engine TEXT, shown_at TEXT, dictation TEXT,  -- dictation: the app dictated into (history rows)
    music INTEGER, late INTEGER NOT NULL DEFAULT 0, late_job INTEGER);  -- late: insert-only rows protected from a live replay
CREATE INDEX IF NOT EXISTS sentences_start ON sentences(start);
CREATE INDEX IF NOT EXISTS sentences_id_source ON sentences(id, source);  -- a row by its id (relabel); id first: on (source, id) a
                                                                          -- pass's DELETE (start range and source) took it and walked the device's history
CREATE TABLE IF NOT EXISTS drafts (          -- the draft layer's rows (only while a page watches); kept after final rows cover their time
    id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, speaker TEXT, speaker_confidence REAL, text TEXT,
    source TEXT, engine TEXT, shown_at TEXT);
CREATE INDEX IF NOT EXISTS drafts_start ON drafts(start);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL, participants TEXT NOT NULL, speech_s REAL,
    sentences INTEGER NOT NULL, source TEXT, device TEXT);  -- device: its sentences' source
CREATE INDEX IF NOT EXISTS conversations_start ON conversations(start);
CREATE TABLE IF NOT EXISTS summaries (        -- body: the summary as the API returns it
    conversation TEXT PRIMARY KEY, status TEXT NOT NULL, prompt_version INTEGER, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voices (           -- one label of one conversation; label and e NULL: no recording, not retried
    conv TEXT NOT NULL, label TEXT, device TEXT, n INTEGER, speech_s REAL, k INTEGER, e BLOB,
    clip TEXT, clip_start TEXT, clip_end TEXT, clip_text TEXT, level REAL,  -- level: dBFS of its sentences (selfvoice.level)
    UNIQUE (conv, label));
CREATE TABLE IF NOT EXISTS apart (voice TEXT NOT NULL, grp TEXT NOT NULL, PRIMARY KEY (voice, grp));
CREATE TABLE IF NOT EXISTS people (name TEXT PRIMARY KEY, n INTEGER NOT NULL, e BLOB NOT NULL);  -- bank.py: the mean of enrolled
CREATE TABLE IF NOT EXISTS enrolled (          -- bank.py: what taught each people row: a sentence (row: its rowid in sentences,
    name TEXT NOT NULL, conv TEXT, row INTEGER, n INTEGER NOT NULL, e BLOB NOT NULL);  -- n 1, e its voiceprint), or with row
CREATE INDEX IF NOT EXISTS enrolled_name ON enrolled(name);  -- NULL the people row as it was before this table (n its sentences)
CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, name TEXT NOT NULL);  -- another way a person is called (A哥 = A)
CREATE TABLE IF NOT EXISTS name_hints (conv TEXT PRIMARY KEY, body TEXT NOT NULL);  -- hints.py: what each label was called; suggestions only
CREATE TABLE IF NOT EXISTS profiles (name TEXT PRIMARY KEY, body TEXT NOT NULL);  -- profiles.py: a named person's profile, every line with its sources
CREATE TABLE IF NOT EXISTS pieces (           -- uploaded audio pieces, the ingest queue; size bytes at pos of path (the
    device TEXT NOT NULL, start_ms INTEGER NOT NULL, seconds REAL NOT NULL, path TEXT NOT NULL,  -- hour file, relative
    pos INTEGER, size INTEGER, n INTEGER,                                                       -- to the recordings dir;
    received_ms INTEGER, rms REAL, hum REAL, zero_ms INTEGER, full INTEGER,                     -- n: samples the client wrote;
    PRIMARY KEY (start_ms, device));                                                            -- received_ms: when it came
                                                                                                -- (NULL before 10-07);
                                                                                                -- rms..full: audio.health()
CREATE INDEX IF NOT EXISTS pieces_device ON pieces(device, start_ms);  -- each device's stream and backlog
CREATE INDEX IF NOT EXISTS pieces_end ON pieces(device, start_ms + seconds * 1000);  -- where its audio ends: backlog, a stream's start, pending
CREATE TABLE IF NOT EXISTS late_spans (
    id INTEGER PRIMARY KEY, device TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    first_ms INTEGER NOT NULL, last_ms INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
    tries INTEGER NOT NULL DEFAULT 0, note TEXT, next_ms INTEGER NOT NULL DEFAULT 0,
    ready INTEGER NOT NULL DEFAULT 0, done_ms INTEGER, ranges TEXT NOT NULL, seam_ms INTEGER);
CREATE INDEX IF NOT EXISTS late_spans_queue ON late_spans(status, start_ms);
CREATE INDEX IF NOT EXISTS late_spans_device ON late_spans(device, status);
CREATE TABLE IF NOT EXISTS processed_audio (
    device TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, layer TEXT NOT NULL,
    PRIMARY KEY(device, start_ms, end_ms, layer));
CREATE INDEX IF NOT EXISTS processed_audio_range ON processed_audio(device, end_ms);
CREATE TABLE IF NOT EXISTS conversation_aliases (old TEXT PRIMARY KEY, new TEXT NOT NULL, labels TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS walls (device TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (device, at));  -- split.py: where a later part starts; late audio never merges across
CREATE TABLE IF NOT EXISTS timelines (conversation TEXT PRIMARY KEY, body TEXT NOT NULL);  -- the live analysis as it stood at the close
CREATE TABLE IF NOT EXISTS feedback (conv TEXT NOT NULL, at TEXT NOT NULL, text TEXT NOT NULL);  -- what the owner says a summary got wrong, read back in batches
CREATE TABLE IF NOT EXISTS todo_marks (       -- 完成 / 不对 on one todo (todo_ids), at: when; a row dropped is the undo
    id TEXT PRIMARY KEY, conv TEXT NOT NULL, mark TEXT NOT NULL CHECK (mark IN ('done', 'wrong')), at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stars (id TEXT PRIMARY KEY, conv TEXT NOT NULL, at TEXT NOT NULL);  -- 收藏: a sentence (its id in conv's device's rows), when
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);  -- per device: finalized_to:<d>, draft_to:<d>, open_conversation:<d>, end_at:<d>, live:<d>
CREATE TABLE IF NOT EXISTS device_log (       -- what happened to a device, as its phone uploads it. src 'rec': the recorder's
    device TEXT NOT NULL, src TEXT NOT NULL,  -- own event log (firmware lr_log.h), seq that log's, data its record (boot t
    seq INTEGER NOT NULL, at TEXT,            -- code x a b); any other src: an app's events, seq its own. at: when, put on
    data TEXT NOT NULL, PRIMARY KEY (device, src, seq));  -- the clock by the phone; NULL when it could not be
CREATE INDEX IF NOT EXISTS device_log_at ON device_log(device, at);
CREATE TABLE IF NOT EXISTS device_status (    -- every status a client read from a BLE recorder (battery, card, firmware)
    device TEXT NOT NULL, at TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS device_status_at ON device_status(device, at);
CREATE TABLE IF NOT EXISTS calls (            -- every ASR and LLM call: how it went and what it took, never what it said.
    at TEXT NOT NULL, kind TEXT NOT NULL,     -- kind: final, draft (ASR); summary, split, hints, profile, recap, live, ask (LLM)
    engine TEXT NOT NULL, device TEXT,        -- engine: the provider or model; device: whose audio (ASR)
    ms INTEGER NOT NULL, error TEXT,          -- error: NULL when it answered, else its class, code and first words
    audio_s REAL, sent_s REAL,                -- ASR: the window, and what was sent of it (silences squeezed)
    prompt_tokens INTEGER, completion_tokens INTEGER);
CREATE INDEX IF NOT EXISTS calls_at ON calls(at);
CREATE TABLE IF NOT EXISTS balances (at TEXT NOT NULL, provider TEXT NOT NULL, cny REAL NOT NULL);  -- a prepaid account, hourly
-- what the server spent (perf.py), kept perf.KEEP_DAYS. The process's own (stalls, samples): in the first tenant's store
CREATE TABLE IF NOT EXISTS perf_stalls (      -- the event loop held up (monitor.loop_stalls): a row per log line, at its end;
    at TEXT NOT NULL, ms INTEGER NOT NULL,    -- frames: where the loop was; suppressed: the stalls since the last row
    frames TEXT NOT NULL, suppressed INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS perf_stalls_at ON perf_stalls(at);
CREATE TABLE IF NOT EXISTS perf_samples (     -- the process each minute: s since the last row, cpu_s self + children in it;
    at TEXT NOT NULL, s REAL NOT NULL, cpu_s REAL NOT NULL,  -- rss_mb Linux only; hwm_mb: rss high-water mark; lag: the
    rss_mb REAL, hwm_mb REAL, lag_p50_ms REAL, lag_p99_ms REAL, lag_max_ms REAL,  -- event loop's beat late by;
    threads TEXT, queued INTEGER, clients INTEGER);  -- threads: per pool (threads.py) busy on average, most waiting
CREATE INDEX IF NOT EXISTS perf_samples_at ON perf_samples(at);
CREATE TABLE IF NOT EXISTS perf_requests (    -- HTTP requests: one of perf.SLOW_MS or more (n NULL; at its end), and per
    at TEXT NOT NULL, method TEXT NOT NULL,   -- minute, method, route and status all of them (n; ms the slowest, p50,
    route TEXT NOT NULL, status INTEGER NOT NULL, n INTEGER,  -- p95); bytes: of the response bodies
    ms REAL NOT NULL, p50 REAL, p95 REAL, bytes INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS perf_requests_at ON perf_requests(at);
CREATE TABLE IF NOT EXISTS perf_pieces (      -- a device's final pieces and draft passes (device_pipeline): ms each step took;
    at TEXT NOT NULL, device TEXT NOT NULL, layer TEXT NOT NULL,  -- decode, vad, fed_s: its layer's stream since the
    piece_s REAL, sentences INTEGER,          -- layer's last row (the draft layer's while no page watches: rows of its own,
    decode_ms REAL, vad_ms REAL, fed_s REAL,  -- piece_s NULL, beside the final rows)
    asr_ms REAL, embed_ms REAL, commit_ms REAL);
CREATE INDEX IF NOT EXISTS perf_pieces_at ON perf_pieces(at);
"""

_local = threading.local()  # .c: this thread's connection per file


def open_db(path: Path, recordings: Path | None = None) -> tenant.Tenant:
    """The store at path for the code that runs from here on (a script's, a test's): a tenant of its own, its
    recordings beside it unless given."""
    t = tenant.enter(tenant.Tenant("local", "", path.parent, recordings or path.parent, db=path))
    init()
    return t


def init():
    """The current tenant's store made or brought up to date (the server, for each tenant at the start), its data dir
    too (a first start: an empty volume)."""
    tenant.T().db.parent.mkdir(parents=True, exist_ok=True)
    marks_new = conn().execute("SELECT 1 FROM sqlite_master WHERE name = 'todo_marks'").fetchone() is None
    enrolled_new = conn().execute("SELECT 1 FROM sqlite_master WHERE name = 'enrolled'").fetchone() is None
    conn().executescript(SCHEMA)
    if enrolled_new:  # 10-25: the people bank was a running mean; what it learnt before stays, as one row each
        conn().execute("INSERT INTO enrolled SELECT name, NULL, NULL, n, e FROM people")
    if marks_new:  # 10-24: marks were feedback lines (TODO_MARKS) keyed by the todo's text; the lines stay, the log for the prompt
        with tx() as c:
            for conv, at, text in c.execute("SELECT conv, at, text FROM feedback").fetchall():
                for mark, prefix in zip(("done", "wrong"), TODO_MARKS):
                    if text.startswith(prefix) and (found := todo_by_text(conv, text[len(prefix):])):
                        c.execute("INSERT OR IGNORE INTO todo_marks VALUES (?, ?, ?, ?)", (found[0], conv, mark, at))
    if "device" not in {r[1] for r in conn().execute("PRAGMA table_info(conversations)")}:  # 10-02: one pipeline per device
        conn().execute("ALTER TABLE conversations ADD COLUMN device TEXT")
    if "pos" not in {r[1] for r in conn().execute("PRAGMA table_info(pieces)")}:  # 10-04: pieces in hour files
        conn().execute("ALTER TABLE pieces ADD COLUMN pos INTEGER")
        conn().execute("ALTER TABLE pieces ADD COLUMN size INTEGER")
    if "n" not in {r[1] for r in conn().execute("PRAGMA table_info(pieces)")}:  # 10-04: iPhone pieces say how many samples they hold
        conn().execute("ALTER TABLE pieces ADD COLUMN n INTEGER")
    if "received_ms" not in {r[1] for r in conn().execute("PRAGMA table_info(pieces)")}:  # 10-07: when each came, how it sounds
        for col in ("received_ms INTEGER", "rms REAL", "hum REAL", "zero_ms INTEGER", "full INTEGER"):
            conn().execute(f"ALTER TABLE pieces ADD COLUMN {col}")
    if "music" not in {r[1] for r in conn().execute("PRAGMA table_info(sentences)")}:  # 10-25: songs kept out of voiceprints
        conn().execute("ALTER TABLE sentences ADD COLUMN music INTEGER")
    if "late" not in {r[1] for r in conn().execute("PRAGMA table_info(sentences)")}:
        conn().execute("ALTER TABLE sentences ADD COLUMN late INTEGER NOT NULL DEFAULT 0")
    if "late_job" not in {r[1] for r in conn().execute("PRAGMA table_info(sentences)")}:
        conn().execute("ALTER TABLE sentences ADD COLUMN late_job INTEGER")
    if "seam_ms" not in {r[1] for r in conn().execute("PRAGMA table_info(late_spans)")}:
        conn().execute("ALTER TABLE late_spans ADD COLUMN seam_ms INTEGER")
    if "level" not in {r[1] for r in conn().execute("PRAGMA table_info(voices)")}:  # 10-14: how loud each voice is
        conn().execute("ALTER TABLE voices ADD COLUMN level REAL")
    for col in ("kind", "dictation_share"):  # 10-24: 口述 (history rows imported as dictation) no longer a kind of its own
        if col in {r[1] for r in conn().execute("PRAGMA table_info(conversations)")}:
            conn().execute(f"ALTER TABLE conversations DROP COLUMN {col}")
    if (lang := kv("lang")) in tenant.LANGS:  # PUT /api/lang's, over tenants.json's
        tenant.T().lang = lang


def conn() -> sqlite3.Connection:
    """The current tenant's store, on this thread's connection to it."""
    path = tenant.T().db
    if not hasattr(_local, "c"):
        _local.c = {}
    if (c := _local.c.get(path)) is None:
        c = _local.c[path] = sqlite3.connect(path, isolation_level=None, timeout=30)  # autocommit outside tx()
        _functions(c)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    return c


def _functions(c: sqlite3.Connection):
    """Row access and search()'s function, once per connection: redefining a function while a statement on the
    connection is still stepping fails."""
    c.row_factory = sqlite3.Row
    c.create_function("metadata_contains", 2, _metadata_contains, deterministic=True)


def read_only(path) -> sqlite3.Connection:
    """A script's connection to a copy of a store that it only reads (never open_db: it writes the schema)."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    _functions(c)
    return c


@contextmanager
def tx():
    c = conn()
    if c.in_transaction:  # inside another tx(): a part of it, committed or rolled back with it
        yield c
        return
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")


def vec(b: bytes | None) -> np.ndarray | None:
    return None if b is None else np.frombuffer(b, np.float32)


def blob(e) -> bytes:
    return np.asarray(e, np.float32).tobytes()


# sentences

S_COLS = ("id", "start", "end", "speaker", "speaker_confidence", "text", "source", "engine", "shown_at", "dictation", "music")


def _sentence(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in S_COLS if r[k] is not None} | {"state": "final"}


def sentences(lo: str, hi: str, inclusive: bool = False, source: str | None = None) -> list[dict]:
    """Rows starting in [lo, hi), or [lo, hi] with inclusive (a conversation's own rows); of one source when given."""
    op = "<=" if inclusive else "<"
    return [_sentence(r) for r in conn().execute(f"SELECT * FROM sentences WHERE start >= ? AND start {op} ? AND (? IS NULL OR source = ?) ORDER BY start",
                                                 (lo, hi, source, source))]


def replace_sentences(c: sqlite3.Connection, lo: str, hi: str, rows: list[dict], source: str):
    """A device's pass replaces only that device's rows: another device may have recorded the same minutes."""
    c.execute("DELETE FROM sentences WHERE start >= ? AND start < ? AND source = ? AND late = 0", (lo, hi, source))
    protected = c.execute("SELECT id, start, end FROM sentences WHERE source = ? AND late = 1 AND start < ? AND end > ?", (source, hi, lo)).fetchall()
    insert_sentences(c, [r for r in rows if not any(p["id"] == r["id"] or p["start"] < r["end"] and p["end"] > r["start"] for p in protected)])


def insert_sentences(c: sqlite3.Connection, rows: list[dict]):
    c.executemany(f"INSERT INTO sentences ({','.join(S_COLS)}) VALUES ({','.join('?' * len(S_COLS))})",
                  [tuple(r.get(k) for k in S_COLS) for r in rows])


def processed(device: str, start_ms: int, end_ms: int, layer: str | None = None) -> list[list[int]]:
    """Committed sample coverage, including pauses; written atomically with its final rows."""
    return [list(r) for r in conn().execute("SELECT start_ms,end_ms FROM processed_audio WHERE device=? AND start_ms < ? AND end_ms > ? "
                                           "AND (? IS NULL OR layer=?)", (device, end_ms, start_ms, layer, layer))]


def record_processed(t, device: str, ranges: list, layer: str):
    t.executemany("INSERT OR IGNORE INTO processed_audio VALUES (?,?,?,?)", [(device, a, b, layer) for a, b in ranges if b > a])


D_COLS = S_COLS[:-2]  # a draft has no dictation, no music


def drafts(lo: str, hi: str | None = None, source: str | None = None) -> list[dict]:
    """Draft rows starting in [lo, hi) (no upper bound when hi is None), of one source when given."""
    return [{k: r[k] for k in D_COLS if r[k] is not None} | {"state": "draft"}
            for r in conn().execute("SELECT * FROM drafts WHERE start >= ? AND (? IS NULL OR start < ?) AND (? IS NULL OR source = ?) ORDER BY start",
                                    (lo, hi, hi, source, source))]


def insert_drafts(c: sqlite3.Connection, rows: list[dict]):
    c.executemany(f"INSERT INTO drafts ({','.join(D_COLS)}) VALUES ({','.join('?' * len(D_COLS))})",
                  [tuple(r.get(k) for k in D_COLS) for r in rows])


def has_text(lo: str, hi: str, q: str, source: str | None = None) -> bool:
    return conn().execute("SELECT 1 FROM sentences WHERE start >= ? AND start <= ? AND (? IS NULL OR source = ?) AND instr(text, ?) LIMIT 1",
                          (lo, hi, source, source, q)).fetchone() is not None


# conversations and summaries

C_COLS = ("id", "start", "end", "participants", "speech_s", "sentences", "source", "device")


def _conversation(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in C_COLS if r[k] is not None}
    return {**d, "participants": json.loads(d["participants"]), "state": "closed"}


def insert_conversation(c: sqlite3.Connection, conv: dict):
    # OR REPLACE: a same-second id would otherwise abort the finalization transaction and stop the pipeline
    c.execute(f"INSERT OR REPLACE INTO conversations ({','.join(C_COLS)}) VALUES ({','.join('?' * len(C_COLS))})",
              tuple(json.dumps(conv[k], ensure_ascii=False) if k == "participants" else conv.get(k) for k in C_COLS))


def conversation(conv_id: str) -> dict | None:
    conv_id = resolve_conversation(conv_id)
    r = conn().execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    return _conversation(r) if r else None


def resolve_conversation(cid: str) -> str:
    """Old conversation links survive a merge, including a chain of merges."""
    return resolve_voice(cid, "")[0]


def resolve_voice(cid: str, label: str) -> tuple[str, str]:
    seen = set()
    while cid not in seen:
        seen.add(cid)
        r = conn().execute("SELECT new, labels FROM conversation_aliases WHERE old = ?", (cid,)).fetchone()
        if not r:
            break
        cid, label = r[0], json.loads(r[1]).get(label, label)
    return cid, label


def conversations(lo: str | None = None, hi: str | None = None) -> list[dict]:
    """Closed conversations with their summary (None when there is none): all newest first, or [lo, hi) by start oldest first."""
    q, args = "SELECT c.*, s.body FROM conversations c LEFT JOIN summaries s ON s.conversation = c.id", ()
    if lo:
        q, args = q + " WHERE c.start >= ? AND c.start < ?", (lo, hi)
    return [{**_conversation(r), "summary": json.loads(r["body"]) if r["body"] else None}
            for r in conn().execute(q + (" ORDER BY c.start" if lo else " ORDER BY c.start DESC"), args)]


def _metadata_contains(fields: str | None, q: str) -> bool:
    # Keep Python's Unicode lower() and JSON spacing/escaping: queries may themselves
    # contain JSON punctuation. SQLite extracts only the searchable fields, not the body.
    meta = json.loads(fields) if fields else [None] * 5
    return q in json.dumps(meta, ensure_ascii=False).lower()


def search(q: str = "", tag: str = "", who: str = "", after: str = "", before: str = "", limit: int = 30) -> list[dict]:
    """Closed conversations newest first, with metadata/transcript filters and start in [after, before).

    JSON1 handles membership/extraction; Python retains the metadata search's Unicode
    and serialization semantics. Transcript matching remains case-sensitive instr().
    """
    clauses, args = [], []
    for value, clause in (
        (after, "c.start >= ?"),
        (before, "c.start < ?"),
        (who, "EXISTS (SELECT 1 FROM json_each(c.participants) WHERE value = ?)"),
        (tag, "EXISTS (SELECT 1 FROM json_each(s.body, '$.tags') WHERE value = ?)"),
    ):
        if value:
            clauses.append(clause)
            args.append(value)
    if q:
        clauses.append("""(metadata_contains(json_extract(s.body, '$.title', '$.summary', '$.tags', '$.todos', '$.decisions'), ?)
            OR EXISTS (SELECT 1 FROM sentences t
                       WHERE t.start >= c.start AND t.start <= c.end
                       AND (c.device IS NULL OR t.source = c.device) AND instr(t.text, ?)))""")
        args.extend((q.lower(), q))
    query = "SELECT c.*, s.body FROM conversations c LEFT JOIN summaries s ON s.conversation = c.id"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    # Historically zero and negative limits meant no limit (the counter never equalled them).
    query += " ORDER BY c.start DESC LIMIT ?"
    args.append(limit if limit > 0 else -1)
    return [{**_conversation(r), "summary": json.loads(r["body"]) if r["body"] else None}
            for r in conn().execute(query, args)]


def names() -> dict[str, int]:
    """Names in use and their conversations, most first; a voice in the people bank with none yet at 0."""
    n = {}
    for r in conn().execute("SELECT participants FROM conversations ORDER BY start DESC"):
        for p in json.loads(r["participants"]) or []:
            if p != "我" and not re.fullmatch(r"S(\d+|\?)", p):
                n[p] = n.get(p, 0) + 1
    for r in conn().execute("SELECT name FROM people"):
        n.setdefault(r["name"], 0)
    return dict(sorted(n.items(), key=lambda kv: -kv[1]))


def summary(conv_id: str) -> dict | None:
    conv_id = resolve_conversation(conv_id)
    r = conn().execute("SELECT body FROM summaries WHERE conversation = ?", (conv_id,)).fetchone()
    return json.loads(r["body"]) if r else None


def put_summary(s: dict, c: sqlite3.Connection | None = None):
    (c or conn()).execute("INSERT OR REPLACE INTO summaries VALUES (?, ?, ?, ?)",
                          (s["conversation"], s["status"], s.get("prompt_version"), json.dumps(s, ensure_ascii=False)))


def timeline(conv_id: str) -> dict | None:
    conv_id = resolve_conversation(conv_id)
    r = conn().execute("SELECT body FROM timelines WHERE conversation = ?", (conv_id,)).fetchone()
    return json.loads(r["body"]) if r else None


def put_timeline(conv_id: str, body: dict, c: sqlite3.Connection | None = None):
    (c or conn()).execute("INSERT OR REPLACE INTO timelines VALUES (?, ?)", (conv_id, json.dumps(body, ensure_ascii=False)))


def feedback(conv_id: str | None = None) -> list[dict]:
    """Newest first; every conversation's when none is given."""
    conv_id = resolve_conversation(conv_id) if conv_id else conv_id
    q = "SELECT * FROM feedback" + (" WHERE conv = ?" if conv_id else "") + " ORDER BY at DESC"
    return [dict(r) for r in conn().execute(q, (conv_id,) if conv_id else ())]


def add_feedback(conv_id: str, text: str):
    conv_id = resolve_conversation(conv_id)
    conn().execute("INSERT INTO feedback VALUES (?, ?, ?)", (conv_id, dt.datetime.now().isoformat(timespec="seconds"), text))


TODO_MARKS = ("待办完成：", "待办不对：")  # before todo_marks, 完成 / 不对 were feedback lines: the mark, then the todo's text
SAME_DAY = ("待会", "等下", "一会", "今晚", "晚上", "下午", "今天", "明天")  # a due said like this is stale 3 days after, not 7


def todo_ids(conv: str, todos: list[dict]) -> list[str]:
    """The ids of a summary's todos, in its order: the conversation and the time the todo was said (else a hash of its
    text); a later one said at the same time gets #2, #3… Regeneration pins identity in each todo's id."""
    seen, out = {}, []
    for t in todos:
        base = f"{conv}:{t.get('at') or hashlib.sha1((conv + (t.get('text') or '')).encode()).hexdigest()[:16]}"
        seen[base] = n = seen.get(base, 0) + 1
        pinned = t.get("id")
        out.append(pinned if isinstance(pinned, str) and pinned.startswith(conv + ":") else base if n == 1 else f"{base}#{n}")
    return out


def _todos(conv: str) -> list[tuple[str, dict]]:
    """Id and todo of each todo of conv's summary."""
    todos = (summary(conv) or {}).get("todos") or []
    return list(zip(todo_ids(conv, todos), todos))


def todo_by_text(conv: str, text: str) -> tuple[str, dict] | None:
    """Id and todo of conv's summary with this text (the recaps before ids, the marks before todo_marks name one by it)."""
    return next(((i, t) for i, t in _todos(conv) if t.get("text") == text), None)


def todo(id: str) -> dict | None:
    """The todo with this id, from its conversation's summary."""
    id = resolve_todo(id)
    return next((t for i, t in _todos(id.split(":", 1)[0]) if i == id), None)


def resolve_todo(id: str) -> str:
    cid = id.split(":", 1)[0]
    seen = set()
    while cid not in seen:
        seen.add(cid)
        r = conn().execute("SELECT new FROM conversation_aliases WHERE old = ?", (cid,)).fetchone()
        if not r:
            break
        cid = r[0]
        id = f"{cid}:merged-{hashlib.sha1(id.encode()).hexdigest()[:24]}"
    return id


def closed_todos() -> dict[str, str]:
    """Id to mark (done, wrong) of the marked todos: they leave the due list and the recaps."""
    return {r[0]: r[1] for r in conn().execute("SELECT id, mark FROM todo_marks")}


def mark_todos(ids: list[str], mark: str | None):
    """Mark these todos done or wrong; None drops their marks (the undo)."""
    ids = [resolve_todo(i) for i in ids]
    with tx() as c:
        if mark is None:
            c.executemany("DELETE FROM todo_marks WHERE id = ?", [(i,) for i in ids])
        else:
            now = dt.datetime.now().isoformat(timespec="seconds")
            c.executemany("INSERT OR REPLACE INTO todo_marks VALUES (?, ?, ?, ?)", [(i, i.split(":", 1)[0], mark, now) for i in ids])


def preserve_todos(conv: str, old: list[dict], new: list[dict], c: sqlite3.Connection) -> list[dict]:
    """Pin a uniquely matched action to its old ID; keep unmatched marked actions unchanged. Marks never move.
    Text, else quote, must match uniquely on both sides with the same owner/recipient. New actions get fresh IDs,
    so a removed action's ID (including a cached recap's) cannot be reused by another action."""
    marks = {r[0] for r in c.execute("SELECT id FROM todo_marks WHERE conv = ?", (conv,))}
    before = list(zip(todo_ids(conv, old), old))
    after = [{k: v for k, v in t.items() if k not in ("id", "mark")} for t in new]
    used = set()
    for id, t in before:
        matches = []
        for key in ("text", "quote"):
            def same(n):
                return t.get(key) and n.get(key) == t[key] and all(n.get(k) == t.get(k) for k in ("owner", "to"))
            matches = [i for i, n in enumerate(new) if same(n)]
            if len(matches) == 1 and sum(bool(same(n)) for n in old) == 1:
                break
            matches = []
        if matches and matches[0] not in used:
            after[matches[0]]["id"] = id
            used.add(matches[0])
        elif id in marks:
            after.append({**t, "id": id})
    reserved = marks | {i for i, _ in before}
    for t in after:
        if "id" not in t:
            id = f"{conv}:{secrets.token_hex(16)}"
            while id in reserved:
                id = f"{conv}:{secrets.token_hex(16)}"
            t["id"] = id
        reserved.add(t["id"])
    return after


def rename_todos(conv: str, frm: str, to: str, c: sqlite3.Connection) -> dict:
    """Apply an authorized full-label rename immediately; record changed fields by pinned ID for merge-safe undo."""
    s, changed = summary(conv), {}
    if not s or not s.get("todos"):
        return changed
    for id, t in zip(todo_ids(conv, s["todos"]), s["todos"]):
        t["id"] = id
        fields = {k: t[k] for k in ("owner", "to") if t.get(k) == frm}
        if fields:
            changed[id] = fields
            t.update({k: to for k in fields})
    put_summary(s, c)
    return changed


def undo_todo_rename(conv: str, changed: dict, to: str, c: sqlite3.Connection):
    s = summary(conv)
    if not s or not changed:
        return
    for id, t in zip(todo_ids(conv, s.get("todos") or []), s.get("todos") or []):
        for k, value in changed.get(id, {}).items():
            if t.get(k) == to:
                t[k] = value
    put_summary(s, c)


def todo_view(conv: dict, s: dict, i: str, t: dict, closed: dict, al: dict) -> dict:
    """A todo as the pages show it: with its id and mark, the sentence it came from, whom it is to (an alias as its
    person; none given: the others in the conversation), the conversation's title and start."""
    to = al.get(t.get("to"), t.get("to"))
    return {"id": i, "mark": closed.get(i), "conv": conv["id"], "text": t.get("text"), "to": t.get("to"),
            **{k: t.get(k) for k in ("due", "due_date", "at", "quote")}, "title": s.get("title"), "start": conv["start"],
            "with": [to] if to else [al.get(p, p) for p in conv["participants"] if p not in ("我", "S?")]}


def due_todos(day: str) -> list[dict]:
    """My todos due on day (YYYY-MM-DD) or before and not closed, oldest due first, each with its age_days past due and
    stale: past due over 7 days, over 3 for a due said as the same or the next day (SAME_DAY). Only summaries from prompt
    v8 on date them."""
    closed, al, out = closed_todos(), aliases(), []
    for r in conn().execute("SELECT c.*, s.body FROM summaries s JOIN conversations c ON c.id = s.conversation"
                            " WHERE s.prompt_version >= 8 AND s.status = 'done'"):
        conv, s = _conversation(r), json.loads(r["body"])
        todos = s.get("todos") or []
        for i, t in zip(todo_ids(conv["id"], todos), todos):
            if t.get("owner") == "我" and t.get("due_date") and t["due_date"] <= day and i not in closed:
                age = (dt.date.fromisoformat(day) - dt.date.fromisoformat(t["due_date"])).days
                out.append({**todo_view(conv, s, i, t, closed, al), "age_days": age,
                            "stale": age > (3 if any(w in (t.get("due") or "") for w in SAME_DAY) else 7)})
    return sorted(out, key=lambda t: (t["due_date"], t["conv"]))


# voices, apart pins, people bank, aliases, name hints

V_COLS = ("conv", "label", "device", "n", "speech_s", "k", "e", "clip", "clip_start", "clip_end", "clip_text", "level")


def voices() -> list[dict]:
    return [{k: r[k] for k in V_COLS if r[k] is not None} | {"e": vec(r["e"])}
            for r in conn().execute("SELECT * FROM voices WHERE e IS NOT NULL")]


def voiced_conversations() -> set[str]:
    return {r[0] for r in conn().execute("SELECT DISTINCT conv FROM voices")}


def insert_voices(c: sqlite3.Connection, vs: list[dict]):
    c.executemany(f"INSERT OR REPLACE INTO voices ({','.join(V_COLS)}) VALUES ({','.join('?' * len(V_COLS))})",
                  [tuple(blob(v[k]) if k == "e" and v.get(k) is not None else v.get(k) for k in V_COLS) for v in vs])


def apart() -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in conn().execute("SELECT voice, grp FROM apart")}


def people() -> dict[str, tuple[int, np.ndarray]]:
    return {r["name"]: (r["n"], vec(r["e"])) for r in conn().execute("SELECT * FROM people")}


def aliases() -> dict[str, str]:
    return {r[0]: r[1] for r in conn().execute("SELECT alias, name FROM aliases")}


def name_hints(conv_id: str | None = None) -> dict[str, dict]:
    """By conversation: one's, or every one's."""
    q = "SELECT conv, body FROM name_hints" + (" WHERE conv = ?" if conv_id else "")
    return {r[0]: json.loads(r[1]) for r in conn().execute(q, (conv_id,) if conv_id else ())}


def put_name_hints(h: dict, c: sqlite3.Connection | None = None):
    (c or conn()).execute("INSERT OR REPLACE INTO name_hints VALUES (?, ?)", (h["conv"], json.dumps(h, ensure_ascii=False)))


def profile(name: str) -> dict | None:
    r = conn().execute("SELECT body FROM profiles WHERE name = ?", (name,)).fetchone()
    return json.loads(r[0]) if r else None


def put_profile(p: dict):
    p = resolve_references(p)
    conn().execute("INSERT OR REPLACE INTO profiles VALUES (?, ?)", (p["name"], json.dumps(p, ensure_ascii=False)))


def resolve_references(value):
    """A profile call can finish after a merge; canonicalize its structured source citations on write."""
    if isinstance(value, list):
        return [resolve_references(x) for x in value]
    if isinstance(value, dict):
        return {k: resolve_conversation(v) if k in ("conv", "conversation") and isinstance(v, str)
                else resolve_references(v) for k, v in value.items()}
    return value


# pieces and state

def kv(key: str):
    r = conn().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(r[0]) if r else None


def put_kv(key: str, value, c: sqlite3.Connection | None = None):
    (c or conn()).execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))


# device log and status history

def put_device_log(device: str, events: list[dict]):
    """A record sent again replaces the one kept: the same one from a retry, or a new one after the recorder's log was
    erased and its seq started over."""
    with tx() as c:
        c.executemany("INSERT OR REPLACE INTO device_log VALUES (?, ?, ?, ?, ?)",
                      [(device, e["src"], e["seq"], e["at"], json.dumps(e["data"], ensure_ascii=False)) for e in events])


def device_log(device: str, lo: str, hi: str | None = None) -> list[dict]:
    """Events at [lo, hi) (no upper bound when hi is None), and the recorder's undated records between its dated ones
    before and after the range (a boot that ended before any phone set its clock), unsorted."""
    q = """SELECT * FROM device_log WHERE device = :d AND (at >= :lo AND (:hi IS NULL OR at < :hi) OR at IS NULL AND src = 'rec'
             AND seq > coalesce((SELECT max(seq) FROM device_log WHERE device = :d AND src = 'rec' AND at < :lo), -1)
             AND seq < coalesce((SELECT min(seq) FROM device_log WHERE device = :d AND src = 'rec' AND at >= :hi), 1 << 62))"""
    return [{**dict(r), "data": json.loads(r["data"])} for r in conn().execute(q, {"d": device, "lo": lo, "hi": hi})]


def upgrades(device: str, lo: str) -> list[dict]:
    """The firmware updates begun since lo, oldest first: an app's "dfu" events (DfuLog in the apps: start, boot, send,
    progress, done or fail, back) joined with the recorder's own DFU record (lr_log.h 21) and the BOOT records around it
    (b: the firmware's hash), or a DFU no app logged (lr_dfu.py, lr.sh flash, apps before 1.4.1). from/to: the app's
    revisions, else the BOOTs' hashes; ms: start to done or fail, else to the next BOOT; via: the bootloader the recorder
    went to (ota, usb); ok None: no end and no BOOT yet, or with no app, a BOOT on the same firmware (a failed update or
    the same one again: the recorder's records cannot tell)."""
    def boot(seq, before):  # the recorder's BOOT before or after its record seq: when, and the firmware's hash
        r = conn().execute(f"""SELECT at, json_extract(data, '$.b') FROM device_log WHERE device = ? AND src = 'rec'
                                 AND json_extract(data, '$.code') & 127 = 1 AND seq {'<' if before else '>'} ?
                               ORDER BY seq {'DESC' if before else ''} LIMIT 1""", (device, seq)).fetchone()
        return r and (r[0], f"{r[1] & 0xFFFFFFF:07x}{'+' if r[1] >> 28 & 1 else ''}" if r[1] & 0xFFFFFFF else None)
    ms = lambda a, b: round((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() * 1000)
    rows = conn().execute("""SELECT src, seq, at, data FROM device_log WHERE device = ? AND at >= ?
                               AND (src = 'rec' AND json_extract(data, '$.code') & 127 = 21
                                    OR src != 'rec' AND json_extract(data, '$.kind') = 'dfu') ORDER BY at, seq""", (device, lo))
    out = []
    for src, seq, at, data in rows:
        d = json.loads(data)
        if src == "rec":  # the update an app began up to 2 min before, else one no app logged
            u = next((u for u in reversed(out) if u["app"] and "dfu" not in u and ms(u["at"], at) < 120_000), None)
            if not u:
                out.append(u := {"at": at, "app": False, "from": None, "to": None, "ms": None, "ok": None, "why": None})
            old, new = boot(seq, True), boot(seq, False)
            u.update({"dfu": at, "via": {0xA8: "ota", 0x57: "usb"}.get(d["x"]), "boot": new and new[0],
                      "from": u["from"] or old and old[1], "to": u["to"] or new and new[1]})
            if new and new[0] and u["ms"] is None:  # a BOOT no phone could date: no time
                u["ms"] = ms(u["at"], new[0])
            if new and u["ok"] is None and u["app"]:  # no word from the app yet: the revision it booted
                u["ok"] = new[1] == (u["to"] or "").rsplit("-", 1)[-1]
            elif new and not u["app"] and new[1] != (old and old[1]):
                u["ok"] = True
            elif new and not u["app"]:
                u["why"] = "重启后版本没变"
        elif d.get("step") == "start":
            out.append({"at": at, "app": True, "src": src, "from": d.get("from"), "to": d.get("to"), "ms": None, "ok": None,
                        "why": None})
        elif u := next((u for u in reversed(out) if u.get("src") == src), None):  # none: begun before lo
            if d.get("step") in ("done", "fail"):
                u.update(ms=d.get("ms"), ok=d["step"] == "done")
            if d.get("step") == "fail":
                u.update(why=d.get("error"), stage=d.get("stage"), pct=d.get("pct"))
            if d.get("step") == "back":
                u.update(back_ms=d.get("ms"), fw=d.get("fw"))
                if u["ok"] and not d.get("ok"):
                    u.update(ok=False, why=f"升级后固件是 {d.get('fw')}，不是 {u['to']}")
    return out


def add_device_status(device: str, at: str, body: dict):
    conn().execute("INSERT INTO device_status VALUES (?, ?, ?)", (device, at, json.dumps(body, ensure_ascii=False)))


def device_statuses(device: str, lo: str, hi: str | None = None) -> list[dict]:
    return [{**json.loads(r[1]), "at": r[0]} for r in conn().execute(
        "SELECT at, body FROM device_status WHERE device = ? AND at >= ? AND (? IS NULL OR at < ?) ORDER BY at", (device, lo, hi, hi))]


def device_status(device: str) -> dict | None:
    """The newest one."""
    r = conn().execute("SELECT at, body FROM device_status WHERE device = ? ORDER BY at DESC LIMIT 1", (device,)).fetchone()
    return {**json.loads(r[1]), "at": r[0]} if r else None


# what the ASR and LLM calls took

def add_call(kind: str, engine: str, device: str | None, ms: int, error: str | None = None, audio_s: float | None = None,
             sent_s: float | None = None, prompt_tokens: int | None = None, completion_tokens: int | None = None):
    if tenant.current.get(None) is None:  # a script on a read-only copy (db.conn replaced), a test without a store: no ledger
        return
    conn().execute("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (dt.datetime.now().isoformat(timespec="milliseconds"), kind, engine, device, ms, error, audio_s, sent_s,
                    prompt_tokens, completion_tokens))


def calls(lo: str) -> list[dict]:
    """Since lo, by kind and engine: calls, failures (by HTTP code or exception class), ms (median, 90th percentile of the
    answered ones), audio hours sent, tokens."""
    out = {}
    for r in conn().execute("SELECT * FROM calls WHERE at >= ? ORDER BY at", (lo,)):
        c = out.setdefault((r["kind"], r["engine"]), {"kind": r["kind"], "engine": r["engine"], "n": 0, "failed": {}, "ms": [],
                                                      "sent_h": 0.0, "prompt_tokens": 0, "completion_tokens": 0})
        c["n"] += 1
        if r["error"]:
            why = re.search(r"\b[45]\d\d\b", r["error"])
            why = why[0] if why else r["error"].split(":")[0]
            c["failed"][why] = c["failed"].get(why, 0) + 1
        else:
            c["ms"].append(r["ms"])
        c["sent_h"] += (r["sent_s"] or 0) / 3600
        c["prompt_tokens"] += r["prompt_tokens"] or 0
        c["completion_tokens"] += r["completion_tokens"] or 0
    for c in out.values():
        ms = sorted(c.pop("ms"))
        c.update(ms50=ms[len(ms) // 2] if ms else None, ms90=ms[int(len(ms) * 0.9)] if ms else None, sent_h=round(c["sent_h"], 2))
    return sorted(out.values(), key=lambda c: (c["kind"], c["engine"]))


def add_balance(provider: str, cny: float):
    conn().execute("INSERT INTO balances VALUES (?, ?, ?)", (dt.datetime.now().isoformat(timespec="seconds"), provider, cny))


def balance(provider: str, days: int = 7) -> dict | None:
    """The newest balance, what went out a day over the last `days` (top-ups left out) and the days it lasts at that."""
    lo = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
    rs = conn().execute("SELECT at, cny FROM balances WHERE provider = ? AND at >= ? ORDER BY at", (provider, lo)).fetchall()
    if not rs:
        return None
    spent = sum(max(0.0, a[1] - b[1]) for a, b in zip(rs, rs[1:]))
    span = (dt.datetime.fromisoformat(rs[-1][0]) - dt.datetime.fromisoformat(rs[0][0])).total_seconds() / 86400
    per_day = spent / span if span >= 0.5 else None  # half a day at least: an hour's step says little
    return {"at": rs[-1][0], "cny": rs[-1][1], "per_day": per_day and round(per_day, 2),
            "days_left": round(rs[-1][1] / per_day, 1) if per_day else None}
