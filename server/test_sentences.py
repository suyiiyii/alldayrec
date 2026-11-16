"""A long press on one sentence: a star (listed newest first with its conversation, gone when taken off), a report,
and its speaker fixed alone, which learns a named voice and is taken back whole by the rename undo."""
import datetime as dt

import anyio
import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from alldayrec import core
from alldayrec import db
from alldayrec import mcp_server
from alldayrec import routes_audio
from alldayrec import routes_sentences
from alldayrec import server
from alldayrec import split
from alldayrec import tenant
from alldayrec.conversations import Grouper
from alldayrec.routes_sentences import report, reports, sentence_speaker, star, stars, unstar
from alldayrec.routes_speakers import speaker_undo

T = "2026-10-05T10:00:"
CID = "c20261005100001"
ROWS = [dict(id="f1", start=T + "01.000", end=T + "04.000", speaker="S1", text="老张，你来一下"),
        dict(id="f2", start=T + "05.000", end=T + "05.500", speaker="S?", text="嗯"),
        dict(id="f3", start=T + "06.000", end=T + "09.000", speaker="S2", text="来了来了"),
        dict(id="f4", start=T + "10.000", end=T + "13.000", speaker="S1", text="好的老板"),
        dict(id="f5", start=T + "14.000", end=T + "15.000", speaker="我", text="走吧")]


@pytest.fixture
def store(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")
    with db.tx() as t:
        db.insert_sentences(t, [{**r, "source": "mic"} for r in ROWS])
        db.insert_sentences(t, [{**ROWS[0], "source": "other", "text": "另一台录的"}])  # the same id on another device
        db.insert_conversation(t, {"id": CID, "start": ROWS[0]["start"], "end": ROWS[-1]["end"], "participants": ["S1", "S2", "我"],
                                   "sentences": len(ROWS), "speech_s": 12.0, "device": "mic"})
        db.insert_voices(t, [{"conv": CID, "label": l, "device": "mic", "n": 2, "e": np.ones(4) * i} for i, l in enumerate(("S1", "S2"))])
        t.execute("INSERT INTO apart VALUES (?, ?)", (f"{CID}|S2", "g1"))
    db.put_summary({"conversation": CID, "status": "done", "title": "叫老张"})
    cut = []

    async def audio(start, end, fmt, device=None):
        cut.append((start, device))
        return np.zeros(1600, np.int16)
    monkeypatch.setattr(routes_audio, "cut", audio)
    monkeypatch.setitem(core.state, "embed", lambda x: np.array([0.0, 1, 0, 0]))
    return cut


def dump():
    return [sorted(map(tuple, db.conn().execute(f"SELECT * FROM {n}"))) for n in ("sentences", "conversations", "voices", "apart", "name_hints")]


def queued():
    return tenant.T().summaries[1].statistics().current_buffer_used


def test_stars(store):
    assert anyio.run(star, "f3", {"device": "mic"}) == {"starred": True}
    db.conn().execute("UPDATE stars SET at = '2026-10-05T11:00:00'")  # one second apart, in order
    anyio.run(star, "f1", {"device": "mic"})
    anyio.run(star, "f1", {"device": "mic"})  # twice: one star
    got = anyio.run(stars)
    assert [(s["id"], s["speaker"], s["text"], s["title"], s["conv"], s["start"]) for s in got] == [
        ("f1", "S1", "老张，你来一下", "叫老张", CID, ROWS[0]["start"]), ("f3", "S2", "来了来了", "叫老张", CID, ROWS[2]["start"])], "the mic's row, newest first"
    assert anyio.run(unstar, "f1") == {"starred": False} and [s["id"] for s in anyio.run(stars)] == ["f3"]
    for sid, device in (("f9", "mic"), ("f1", "nope"), ("f1", None), ("d1", "mic")):
        with pytest.raises(HTTPException) as e:
            anyio.run(star, sid, {"device": device})
        assert e.value.status_code == 404


def test_star_while_the_conversation_goes_on(store):
    """A final row of a conversation still open: starred with no conversation, which it gets once one closes over it."""
    late = dict(id="f6", start="2026-10-05T10:20:00.000", end="2026-10-05T10:20:02.000", speaker="S1", text="还在说", source="mic")
    with db.tx() as t:
        db.insert_sentences(t, [late])
    anyio.run(star, "f6", {"device": "mic"})
    assert [(s["id"], s["conv"], s["text"], s["title"]) for s in anyio.run(stars)] == [("f6", None, "还在说", None)]
    with db.tx() as t:
        db.insert_conversation(t, {"id": "c20261005102000", "start": late["start"], "end": late["end"], "participants": ["S1"],
                                   "sentences": 1, "speech_s": 2.0, "device": "mic"})
    assert anyio.run(stars)[0]["conv"] == "c20261005102000"
    assert db.conn().execute("SELECT conv FROM stars WHERE id = 'f6'").fetchone()[0] is None, "a read writes nothing"


def test_star_migration(tmp_path):
    """Stars from before (conv NOT NULL, no device or start) are kept, their sentence's device and start filled in."""
    import sqlite3
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE stars (id TEXT PRIMARY KEY, conv TEXT NOT NULL, at TEXT NOT NULL);"
                    f"INSERT INTO stars VALUES ('f3', '{CID}', '2026-10-05T11:00:00'), ('gone', '{CID}', '2026-10-05T11:00:01');")
    c.close()
    db.open_db(path)
    with db.tx() as t:
        db.insert_sentences(t, [{**r, "source": "mic"} for r in ROWS])
        db.insert_conversation(t, {"id": CID, "start": ROWS[0]["start"], "end": ROWS[-1]["end"], "participants": ["S1", "S2", "我"],
                                   "sentences": len(ROWS), "speech_s": 12.0, "device": "mic"})
    assert [tuple(r) for r in db.conn().execute("SELECT * FROM stars ORDER BY at")] == [
        ("f3", CID, "2026-10-05T11:00:00", None, None), ("gone", CID, "2026-10-05T11:00:01", None, None)], "rows before the store's"
    db.conn().execute("DROP TABLE stars")  # again, with the rows in the store first
    db.conn().executescript("CREATE TABLE stars (id TEXT PRIMARY KEY, conv TEXT NOT NULL, at TEXT NOT NULL);"
                            f"INSERT INTO stars VALUES ('f3', '{CID}', '2026-10-05T11:00:00'), ('gone', '{CID}', '2026-10-05T11:00:01');")
    db.init()
    assert [tuple(r) for r in db.conn().execute("SELECT * FROM stars ORDER BY at")] == [
        ("f3", CID, "2026-10-05T11:00:00", "mic", ROWS[2]["start"]), ("gone", CID, "2026-10-05T11:00:01", None, None)]
    assert db.conn().execute("SELECT \"notnull\" FROM pragma_table_info('stars') WHERE name = 'conv'").fetchone()[0] == 0
    assert [s["id"] for s in anyio.run(stars)] == ["f3"]
    db.init()  # once: a store already moved stays as it is
    assert db.conn().execute("SELECT count(*) FROM stars").fetchone()[0] == 2


def test_reports(store):
    """上报问题 on any row: a draft of a conversation going on (its conversation, once one closes over it, found as it is
    read: nothing written), a final row from the conversation page; read back newest first, through the API and MCP."""
    draft = {"id": "d20261005102000000", "start": "2026-10-05T10:20:00.000", "end": "2026-10-05T10:20:02.000", "speaker": "",
             "text": "还在说", "state": "draft", "source": "mic", "engine": "qwen-stream", "junk": "x", "speaker_confidence": 0.5}
    assert anyio.run(report, draft["id"], {"note": "  听错了  ", "page": "live", "row": draft}) == {"id": 1}
    db.conn().execute("UPDATE reports SET at = '2026-10-05T10:21:00'")
    anyio.run(report, "f3", {"note": "说话人不对", "page": "conversation", "row": {**ROWS[2], "state": "final", "source": "mic"}})
    got = anyio.run(reports)
    assert [(r["sentence"], r["note"], r["page"], r["conv"], r["going"]) for r in got] == [
        ("f3", "说话人不对", "conversation", CID, False), (draft["id"], "听错了", "live", None, False)]
    assert got[1]["row"] == {k: v for k, v in draft.items() if k not in ("id", "junk", "speaker", "speaker_confidence")} and got[1]["device"] == "mic"
    text, bad, _ = mcp_server.call("reports", {})
    assert not bad and "我上报的问题 2 条" in text and "在实时页上报：听错了" in text and "10:20:00–10:20:02 S?（draft，mic，qwen-stream）：还在说" in text, text
    assert "`不在任何对话里`" in text, "no conversation open on mic"
    db.put_kv("open_conversation:mic", {"start": "2026-10-05T10:19:59.000", "end": draft["end"], "sentences": [], "speech_s": 0})
    assert anyio.run(reports)[1]["going"] and "`对话还在进行`" in mcp_server.call("reports", {})[0]
    with db.tx() as t:
        db.insert_conversation(t, {"id": "c20261005102000", "start": draft["start"], "end": "2026-10-05T10:20:03.000",
                                   "participants": ["S1"], "sentences": 1, "speech_s": 2.0, "device": "mic"})
    changes = db.conn().total_changes
    assert anyio.run(reports)[1]["conv"] == "c20261005102000" and db.conn().total_changes == changes, "a read writes nothing"


def split_in_two():
    """CID as a split leaves it: 10:00:01-10:00:05.5 kept, 10:00:06-10:00:15 a part of its own."""
    with db.tx() as t:
        t.execute("UPDATE conversations SET end = ? WHERE id = ?", (T + "05.500", CID))
        db.insert_conversation(t, {"id": "c20261005100006", "start": ROWS[2]["start"], "end": ROWS[-1]["end"], "participants": ["S2", "S1", "我"],
                                   "sentences": 3, "speech_s": 8.0, "device": "mic"})


def test_report_after_a_split(store):
    """The box opened on a row of CID, which a split cut in two before it was sent: the row's part, never the page's
    conversation (whatever the page sends)."""
    split_in_two()
    anyio.run(report, "f4", {"note": "x", "page": "conversation", "conv": CID, "row": {**ROWS[3], "state": "final", "source": "mic"}})
    assert anyio.run(reports)[0]["conv"] == "c20261005100006"


def at(s):
    return "2026-10-05T10:" + s


def said(i, a, b):
    return dict(id=i, start=at(a), end=at(b), source="mock", speaker="S1", text="说了一句", state="final")


def keep(c, cid):
    with db.tx() as t:
        db.insert_sentences(t, c["sentences"])
        db.insert_conversation(t, {**c, "id": cid, "device": "mock", "sentences": len(c["sentences"])})


def tell(r):
    anyio.run(report, r["id"], {"note": "x", "page": "live", "row": r})


def test_report_and_star_after_a_stop_and_start(tmp_path):
    """Recording stopped and started again 10 s later (Grouper.end closes at once, no GAP_S between): a row of the second
    conversation is never the first's; while it goes on, none."""
    db.open_db(tmp_path / "t.db")
    g, a, b = Grouper(), said("a", "00:00.000", "00:20.000"), said("b", "00:30.000", "00:50.000")
    g.sentence(a)
    keep(g.end(dt.datetime.fromisoformat(a["end"]))[0], "c-first")
    g.sentence(b)
    db.put_kv("open_conversation:mock", g.open)
    with db.tx() as t:
        db.insert_sentences(t, [b])
    tell(b)
    anyio.run(star, "b", {"device": "mock"})
    assert [(r["conv"], r["going"]) for r in anyio.run(reports)] == [(None, True)] and anyio.run(stars)[0]["conv"] is None
    with db.tx() as t:
        t.execute("DELETE FROM sentences")
    keep(g.end(dt.datetime.fromisoformat(b["end"]))[0], "c-second")
    db.put_kv("open_conversation:mock", None)
    assert [(r["conv"], r["going"]) for r in anyio.run(reports)] == [("c-second", False)] and anyio.run(stars)[0]["conv"] == "c-second"


def test_report_and_star_in_too_few_words(tmp_path):
    """A stop 3 s into the next recording: too short to keep (MIN_S), so in no conversation, not the one before."""
    db.open_db(tmp_path / "t.db")
    g, a, r = Grouper(), said("a", "00:00.000", "00:20.000"), said("short", "00:30.000", "00:33.000")
    g.sentence(a)
    keep(g.end(dt.datetime.fromisoformat(a["end"]))[0], "c-first")
    g.sentence(r)
    assert g.end(dt.datetime.fromisoformat(r["end"])) == []
    with db.tx() as t:
        db.insert_sentences(t, [r])
    tell(r)
    anyio.run(star, "short", {"device": "mock"})
    assert anyio.run(reports)[0]["conv"] is None and anyio.run(stars)[0]["conv"] is None


def test_report_in_a_long_split_pause(tmp_path):
    """A live draft reported in a 220 s pause (under GAP_S: one conversation) that a split cut at: the conversation's
    while it is one, none once split (neither part holds it), never the wrong part."""
    db.open_db(tmp_path / "t.db")
    r1, r2 = said("g1", "00:00.000", "00:20.000"), said("g2", "04:00.000", "04:20.000")
    tell({**said("draft-gap", "02:00.000", "02:03.000"), "state": "draft"})
    tell({**said("in-2", "04:10.000", "04:12.000"), "state": "draft"})
    keep(dict(start=r1["start"], end=r2["end"], participants=["S1"], sentences=[r1, r2], speech_s=40), "c-gap")
    assert {r["sentence"]: r["conv"] for r in anyio.run(reports)} == {"draft-gap": "c-gap", "in-2": "c-gap"}
    split.apply(db.conversation("c-gap"), [r2["start"]], None)
    got = {r["sentence"]: r["conv"] for r in anyio.run(reports)}
    assert got["draft-gap"] is None and got["in-2"] not in (None, "c-gap") and db.conversation(got["in-2"])["start"] == r2["start"], got


def test_report_bad_input(store):
    row = {"start": T + "01.000", "source": "mic"}
    for sid, body in [("f1", {"note": n, "page": "live", "row": row}) for n in (123, True, ["x"], {"x": "y"}, None, "", "  ", "x" * 2001)] + [
            ("f1", {"note": "x", "page": "elsewhere", "row": row}), ("f1", {"note": "x", "page": "live"}),
            ("f1", {"note": "x", "page": "live", "row": {"text": "no start"}}), ("f1", {"note": "x", "page": "live", "row": {"start": "10:00"}}),
            *[("f1", {"note": "x", "page": "live", "row": {**row, "start": v}}) for v in ("", " ", None, 123)],
            ("f1", {"note": "x", "page": "live", "row": {**row, "end": "later"}}), ("f1", {"note": "x", "page": "live", "row": {**row, "text": 5}}),
            ("f1", {"note": "x", "page": "live", "row": {**row, "text": "x" * 2001}}), ("f1", {"note": "x", "page": "live", "row": {**row, "source": "m" * 81}}),
            ("f" * 81, {"note": "x", "page": "live", "row": row})]:
        with pytest.raises(HTTPException) as e:
            anyio.run(report, sid, body)
        assert e.value.status_code == 400, body
    anyio.run(report, "f1", {"note": "x", "page": "live", "conv": "c" * 100_000, "row": row})
    assert anyio.run(reports)[0]["conv"] == CID, "the page's conv is not taken"


def test_report_dates_over_http(tmp_path):
    """A start or end shaped like a time but none (month 13, hour 25, 30 February) is a 400; a report row that does not
    read (left by other code) is skipped, the list still answers."""
    t = db.open_db(tmp_path / "t.db")
    app = FastAPI()
    app.include_router(routes_sentences.router)

    @app.middleware("http")
    async def as_tenant(req, call_next):
        tenant.enter(t)
        return await call_next(req)
    c = TestClient(app, raise_server_exceptions=False)
    db.put_kv("open_conversation:mock", {"start": at("00:00.000")})
    for bad in ("2026-13-05T10:00:00.000", "2026-10-05T25:00:00.000", "2026-02-30T10:00:00.000"):
        for row in ({"start": bad}, {"start": at("00:01.000"), "end": bad}):
            assert c.post("/api/sentences/x/report", json={"note": "x", "page": "live", "row": {**row, "source": "mock"}}).status_code == 400
    assert c.post("/api/sentences/ok/report", json={"note": "x", "page": "live", "row": {"start": at("00:01.000"), "source": "mock"}}).status_code == 200
    db.conn().execute("INSERT INTO reports (at, note, page, sentence, device, start, row) VALUES ('2026-10-05T11:00:00', 'x', 'live', 'bad', 'mock', ?, '{}')",
                      ("2026-02-30T10:00:00.000",))
    db.conn().execute("INSERT INTO reports (at, note, page, sentence, device, start, row) VALUES ('2026-10-05T11:00:01', 'x', 'live', 'bad2', 'mock', ?, 'no json')",
                      (at("00:02.000"),))
    res = c.get("/api/reports")
    assert res.status_code == 200 and [r["sentence"] for r in res.json()] == ["ok"] and res.json()[0]["going"]


def test_report_and_star_by_device(tmp_path):
    """Only a conversation of the row's own device: one of another device, or a NULL device from before devices were
    kept, never holds it; start and end the page sent at any precision kept to the ms, both ends of a span held."""
    db.open_db(tmp_path / "t.db")
    with db.tx() as t:
        for cid, d in (("c-a", "mock-a"), ("c-b", "mock-b"), ("c-legacy", None)):
            db.insert_conversation(t, dict(id=cid, start=at("00:00.000"), end=at("00:20.000"), device=d, participants=["S1"], sentences=2, speech_s=20))
        db.insert_sentences(t, [said("c-only", "00:10.000", "00:12.000") | {"source": "mock-c"}])
    want = {}
    for i, (stamp, d, cid) in enumerate([("00:00", "mock-a", "c-a"), ("00:00.0", "mock-a", "c-a"), ("00:00.000000", "mock-a", "c-a"),
                                         ("00:20", "mock-a", "c-a"), ("00:20.000000", "mock-a", "c-a"), ("00:20.001", "mock-a", None),
                                         ("00:10.000", "mock-b", "c-b"), ("00:10.000", "mock-c", None)]):
        tell({**said(f"r{i}", stamp, stamp), "source": d})
        want[f"r{i}"] = (cid, dt.datetime.fromisoformat(at(stamp)).isoformat(timespec="milliseconds"))
    got = {r["sentence"]: (r["conv"], r["start"]) for r in anyio.run(reports)}
    assert got == want and all(r["row"]["start"] == r["start"] == r["row"]["end"] for r in anyio.run(reports))
    anyio.run(star, "c-only", {"device": "mock-c"})
    assert anyio.run(stars)[0]["conv"] is None, "a legacy conversation of no device is not mock-c's"


def test_report_and_star_where_two_parts_touch(tmp_path):
    """A split cuts at a row's start, so the earlier part ends where the later one starts: both hold that moment, the
    row is the later part's (it starts there)."""
    db.open_db(tmp_path / "t.db")
    rows = [said("f-first", "00:00.000", "00:20.000"), said("f-second", "00:20.000", "00:40.000")]
    keep(dict(start=rows[0]["start"], end=rows[1]["end"], participants=["S1"], sentences=rows, speech_s=40), "c-original")
    anyio.run(star, "f-second", {"device": "mock"})
    tell(rows[1])
    parts = split.apply(db.conversation("c-original"), [rows[1]["start"]], None)
    assert parts[0]["end"] == parts[1]["start"] == rows[1]["start"]
    assert anyio.run(stars)[0]["conv"] == anyio.run(reports)[0]["conv"] == parts[1]["id"]


def test_bad_report_rows_over_mcp(tmp_path, monkeypatch):
    """Report rows that do not read (left by other code: no JSON, not an object, fields of the wrong type, a date that
    is none) are skipped one by one: the page's list and MCP's reports still answer with the rest, no error."""
    t = db.open_db(tmp_path / "t.db")
    monkeypatch.setattr(core, "TENANTS", [t])  # no token: no auth
    c = TestClient(server.app, raise_server_exceptions=False)
    ok = {"note": "x", "page": "live", "row": {"start": at("00:01.000"), "source": "mock", "text": "好的一句"}}
    assert c.post("/api/sentences/good/report", json=ok).status_code == 200
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "reports", "arguments": {}}}
    for bad in ("no json", "null", "[]", "123", '{"state": 7, "text": ["x"]}', '{"text": "x"}',
                f'{{"start": "{at("00:03.000")}", "end": "2026-02-30T10:00:04.000"}}'):
        db.conn().execute("INSERT INTO reports (at, note, page, sentence, device, start, row) VALUES ('2026-11-16T12:00:00', 'x', 'live', 'bad', 'mock', ?, ?)",
                          (at("00:02.000"), bad))
        listed, res = c.get("/api/reports"), c.post("/mcp", json=rpc).json()["result"]
        assert [r["sentence"] for r in listed.json()] == ["good"] and not res["isError"] and "我上报的问题 1 条" in res["content"][0]["text"], (bad, res)
        db.conn().execute("DELETE FROM reports WHERE sentence = 'bad'")


def test_one_sentence_and_undo(store):
    was, n0 = dump(), queued()
    fix = lambda sid, to: anyio.run(sentence_speaker, sid, {"conv": CID, "to": to})

    r = fix("f4", "张三")  # one of S1's two: the row alone, a new participant, its voice learnt
    assert {**r, "undo": None} == {"speaker": "张三", "participants": ["S1", "S2", "我", "张三"], "voiceprints": 1, "undo": None}
    assert [x["speaker"] for x in db.sentences(ROWS[0]["start"], "~", source="mic")] == ["S1", "S?", "S2", "张三", "我"]
    assert db.sentences(ROWS[0]["start"], "~", source="other")[0]["speaker"] == "S1", "the other device's row of that id stays"
    assert dump()[2:] == was[2:] and queued() == n0 + 1, "voices, pins and hints untouched; the summary redone"
    assert db.people()["张三"][0] == 1 and store == [(ROWS[3]["start"], "mic")]
    assert anyio.run(speaker_undo, {"undo": r["undo"]}) == {"undone": 1} and dump() == was

    n1 = queued()
    r = fix("f2", "我")  # S? is no participant, 我 one already: summary redone, nothing learnt
    assert r["participants"] == ["S1", "S2", "我"] and r["voiceprints"] == 0 and queued() == n1 + 1 and len(store) == 1
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert dump() == was

    r = fix("f3", "S1")  # S2's only sentence: S2 merged into S1, its voice and pin with it
    assert r["participants"] == ["S1", "我"] and not db.conn().execute("SELECT 1 FROM voices WHERE label = 'S2'").fetchone()
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert dump() == was

    for to in ("", "S?", "S1", "名" * 21, "张\n三"):
        with pytest.raises(HTTPException) as e:
            fix("f1", to)
        assert e.value.status_code == 400
    assert dump() == was
