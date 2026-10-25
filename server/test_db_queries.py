"""Real SQLite query contracts; no server, models, or external services."""
import itertools
import json
import datetime as dt
import sqlite3
import time

import anyio
import numpy as np
import pytest

from alldayrec import audio
from alldayrec import db
from alldayrec import tenant


def add(i, *, participants=None, summary=None, device=None):
    start = f"2026-10-01T10:{i:02}:00"
    end = f"2026-10-01T10:{i:02}:59"
    with db.tx() as c:
        db.insert_conversation(c, dict(id=str(i), start=start, end=end,
                                      participants=participants or [], sentences=1, device=device))
        if summary is not None:
            db.put_summary(dict(conversation=str(i), status="done", **summary), c)
    return start, end


def legacy_search(**filters):
    out = []
    for c in db.conversations():
        s = c["summary"] or {}
        if filters.get("after") and c["start"] < filters["after"]:
            break
        if (filters.get("before") and c["start"] >= filters["before"]
                or filters.get("who") and filters["who"] not in (c.get("participants") or [])
                or filters.get("tag") and filters["tag"] not in (s.get("tags") or [])):
            continue
        q = filters.get("q", "")
        meta = json.dumps([s.get(k) for k in ("title", "summary", "tags", "todos", "decisions")], ensure_ascii=False)
        if q and q.lower() not in meta.lower() and not db.has_text(c["start"], c["end"], q, c.get("device")):
            continue
        out.append(c)
        if len(out) == filters.get("limit", 30):
            break
    return out


@pytest.fixture
def history(tmp_path):
    db.open_db(tmp_path / "one.db")
    add(0, participants=["Alice", "我"], summary={"title": "ÄPFEL", "tags": ["work"], "todos": [{"done": True, "text": "ship"}]})
    start, end = add(1, participants=["Bob"], device="phone", summary={"tags": None})
    add(2, participants=["Alice", "S1"], summary={"summary": "meeting", "tags": ["work", "x"], "decisions": ["yes"]})
    add(3, participants=["Bob", "S?", "我"])
    add(4, participants=["Alice"], summary={"title": "other"})
    # Inclusive transcript end, case-sensitive text, and device isolation.
    db.conn().executemany("INSERT INTO sentences (id, start, end, text, source) VALUES (?, ?, ?, ?, ?)", [
        ("a", end, end, "Needle", "phone"),
        ("b", start, start, "foreign", "other"),
    ])
    return tenant.T()


def test_json1_and_combined_filters(history):
    # Exercise JSON1 on the very connection used by production queries, including
    # missing fields, Unicode, booleans and the serialized punctuation contract.
    assert db.conn().execute("SELECT json_extract('{\"a\":true}', '$.a', '$.missing')").fetchone()[0] == '[true,null]'
    for q, who, tag in itertools.product(
            ["", "äpfel", "Needle", "needle", "foreign", "null", 'true, "text":', '", ["work"', "yes"],
            ["", "Alice", "Bob"], ["", "work", "absent"]):
        filters = dict(q=q, who=who, tag=tag, after="2026-10-01T10:00:00", before="2026-10-01T10:04:00", limit=2)
        assert db.search(**filters) == legacy_search(**filters), filters


def test_init_drops_the_dictation_kind(tmp_path):
    """A store from before 10-24 loses conversations.kind and dictation_share at the start, its rows intact; again: a no-op."""
    with sqlite3.connect(tmp_path / "old.db") as c:
        c.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL, participants TEXT NOT NULL,"
                  " speech_s REAL, sentences INTEGER NOT NULL, kind TEXT, source TEXT, dictation_share REAL, device TEXT)")
        c.execute("INSERT INTO conversations VALUES ('c1', '2026-09-29T10:00:00', '2026-09-29T10:01:00', '[\"我\"]', 30, 2, '口述', 'phone', 0.8, 'phone')")
    db.open_db(tmp_path / "old.db")
    db.init()
    assert [r[1] for r in db.conn().execute("PRAGMA table_info(conversations)")] == list(db.C_COLS)
    assert db.conversation("c1") == dict(id="c1", start="2026-09-29T10:00:00", end="2026-09-29T10:01:00", participants=["我"],
                                         speech_s=30, sentences=2, source="phone", device="phone", state="closed")


@pytest.mark.parametrize("limit", [0, -1, 1, 2, 30])
def test_order_pagination_and_limits(history, limit):
    assert db.search(limit=limit) == legacy_search(limit=limit)
    first = db.search(limit=2)
    rest = db.search(before=first[-1]["start"], limit=30)
    assert [c["id"] for c in first + rest] == ["4", "3", "2", "1", "0"]
    assert [c["id"] for c in db.search(q="Needle")] == ["1"]
    assert db.search(q="needle") == []
    assert db.search(q="foreign") == []
    assert [c["id"] for c in db.search(q="äpfel")] == ["0"]
    assert [c["id"] for c in db.conversations("2026-10-01T10:01:00", "2026-10-01T10:03:00")] == ["1", "2"]


def test_tenants_and_names(history, tmp_path):
    db.conn().execute("INSERT INTO people VALUES ('Unused', 1, ?)", (b"",))
    assert list(db.names().items()) == [("Alice", 3), ("Bob", 2), ("Unused", 0)]
    other = db.open_db(tmp_path / "two.db")
    add(5, participants=["Other"], summary={"title": "private"})
    assert list(db.names()) == ["Other"]
    assert [c["id"] for c in db.search()] == ["5"]
    token = tenant.current.set(history)
    try:
        assert db.search(q="private") == []
        assert "Other" not in db.names()
    finally:
        tenant.current.reset(token)
    assert tenant.T() is other


def test_small_result_decodes_only_returned_summaries(tmp_path, monkeypatch):
    db.open_db(tmp_path / "large.db")
    with db.tx() as c:
        for i in range(500):
            db.insert_conversation(c, dict(id=str(i), start=f"{i:06}", end=f"{i:06}", participants=["Alice"], sentences=1))
            db.put_summary(dict(conversation=str(i), status="done", title="payload", unused="x" * 4096), c)
    loads = json.loads
    decoded = []
    def track(value, *args, **kwargs):
        result = loads(value, *args, **kwargs)
        decoded.append(result)
        return result
    monkeypatch.setattr(db.json, "loads", track)
    statements = []
    db.conn().set_trace_callback(statements.append)
    try:
        assert [c["id"] for c in db.search(limit=2)] == ["499", "498"]
        assert sum(isinstance(x, dict) and "unused" in x for x in decoded) == 2
        decoded.clear()
        statements.clear()
        assert db.search(q="absent", limit=2) == []
        assert sum(s.lstrip().upper().startswith("SELECT") for s in statements) == 1
        assert not any(isinstance(x, dict) and "unused" in x for x in decoded)
        decoded.clear()
        assert db.names() == {"Alice": 500}
        assert all(isinstance(x, list) for x in decoded)
    finally:
        db.conn().set_trace_callback(None)


def test_search_under_an_open_statement(history):
    # metadata_contains is registered when the connection opens: redefining it while a statement is still
    # stepping (a caller iterating rows) fails with "unable to delete/modify user-function"
    assert [c["id"] for c in db.search(q="äpfel")] == ["0"]
    rows = db.conn().execute("SELECT id FROM conversations")
    next(rows)
    assert [c["id"] for c in db.search(q="äpfel")] == ["0"]


def test_init_makes_the_data_dir(tmp_path):
    """A tenant's data dir need not exist yet: the start makes it (Docker's first start, an empty volume)."""
    db.open_db(tmp_path / "main" / "live.db")
    assert (tmp_path / "main" / "live.db").is_file()


def test_pieces_by_end(tmp_path):
    """Where a device's audio ends comes from pieces_end, not a scan of all its pieces: audio.backlog (its rows and its
    end: 751 and 314 ms at 1.3 M pieces), audio.pending's end, audio.stream's first piece (307 ms)."""
    db.open_db(tmp_path / "t.db")
    db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path) VALUES ('d', 1000, 10, 'x')")

    async def stream():  # its first query, then nothing after 20 s: it waits
        with anyio.move_on_after(0.1):
            async for _ in audio.stream("d", dt.datetime.fromtimestamp(20)):
                pass
    seen = []
    db.conn().set_trace_callback(seen.append)
    audio.backlog("d", dt.datetime.fromtimestamp(5))
    audio.pending(set())
    anyio.run(stream)
    db.conn().set_trace_callback(None)
    ends = [q for q in seen if "FROM pieces WHERE device = 'd' AND start_ms + seconds * 1000 >" in q or "max(start_ms + seconds * 1000)" in q]
    assert len(ends) == 4, seen
    for q in ends:
        plan = " ".join(r[3] for r in db.conn().execute("EXPLAIN QUERY PLAN " + q))
        assert "pieces_end" in plan, (q, plan)


def test_edge_reader(tmp_path, monkeypatch):
    """The draft layer's reader (stream with edge_s) hands out the pieces that end in the device's newest edge_s, in
    start order (an import hours long that ends there first), then the ones arriving; its queries go by pieces_end:
    by start_ms, a reader with nothing handed out yet walked every piece of an idle device each second (~300 ms at
    1.3 M pieces)."""
    db.open_db(tmp_path / "t.db")
    monkeypatch.setattr(audio, "samples", lambda p: np.zeros(round(p["seconds"] * audio.SR), np.int16))
    now = int(time.time()) * 1000
    put = lambda a, s: db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path) VALUES ('d', ?, ?, 'x')", (now + a * 1000, s))
    for i in range(180):
        put(-18000 + 20 * i, 20)  # older than the edge
    put(-14400, 14150)  # an import, ending 250 s ago
    for i in range(9):
        put(-240 + 20 * i, 20)
    want = [now - 14400_000] + [now + (-240 + 20 * i) * 1000 for i in range(9)] + [now - 60_000]
    got, seen = [], []

    async def read():
        with anyio.move_on_after(10):
            async for t, _ in audio.stream("d", None, edge_s=300):
                got.append(round(t.timestamp() * 1000))
                if len(got) == 10:
                    put(-60, 20)  # one arrives
                if len(got) == len(want):
                    break
    db.conn().set_trace_callback(seen.append)
    anyio.run(read)
    db.conn().set_trace_callback(None)
    assert got == want, (got, want)
    qs = [q for q in seen if "start_ms > " in q and "ORDER BY start_ms LIMIT 1" in q]
    assert qs and all("pieces_end" in " ".join(r[3] for r in db.conn().execute("EXPLAIN QUERY PLAN " + q)) for q in qs), qs[:2]
