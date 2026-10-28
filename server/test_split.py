"""Splitting a long conversation: the pure cut rules, the transaction with every moved reference, the walls late audio
never merges across, and the summary worker's hook; the model is faked."""
import contextlib
import json
import os
import sqlite3

import anyio
import pytest

from alldayrec import core, db, late_merge, split, summaries, summary
from alldayrec.tenant import T
from test_late_audio import A, B, D, E1, E2, E3, conv, iso, row, span, store  # noqa: F401

P2 = "c20261020101000"  # the later part: its first row is at 10:10:00
TABLES = ("conversations", "sentences", "summaries", "voices", "walls", "todo_marks", "stars", "apart", "conversation_aliases")
WHOLE = {"parts": [{"start": "10:00:00", "kind": "交谈", "scene": "one"}]}


def rows_at(times, speaker="S1", length=20):
    return [{"id": f"r{s}", "start": iso(s), "end": iso(s + length), "speaker": speaker, "text": f"line {s}", "source": D} for s in times]


def test_cut_rules():
    rows = rows_at(range(0, 300, 30)) + rows_at(range(400, 700, 30))  # a 120 s pause before 400
    i = next(k for k, r in enumerate(rows) if r["start"] == iso(400))
    reply = lambda *parts: {"parts": [{"start": iso(s)[11:19], "kind": k, "scene": "x"} for s, k in parts]}
    assert [c["i"] for c in split.plan(reply((0, "交谈"), (400, "交谈")), rows)] == [i]
    assert [c["i"] for c in split.plan(reply((0, "交谈"), (600, "交谈")), rows)] == [i], "snapped to the pause within 5 min"
    assert split.plan(reply((0, "交谈"), (400, "媒体或背景")), rows) == [], "media joins the part before"
    assert split.plan(reply((0, "独自做事"), (400, "独自做事")), rows) == [], "two solo stretches are one"
    assert [c["i"] for c in split.plan(reply((0, "媒体或背景"), (30, "交谈"), (400, "通话")), rows)] == [i], "a first media part joins the next"
    assert [c["i"] for c in split.plan(reply((0, "交谈"), (400, "什么")), rows)] == [i], "an unknown kind is a talk"
    assert split.plan(reply((0, "独自做事"), (400, "通话")), rows) == [], "a call after solo work is part of it"
    assert split.plan(reply((0, "独自做事"), (200, "通话"), (400, "独自做事")), rows) == [], "solo / call / solo is one"
    assert [c["i"] for c in split.plan(reply((0, "通话"), (400, "独自做事")), rows)] == [i], "a call before solo work stands"
    assert [c["i"] for c in split.plan(reply((0, "独自做事"), (200, "通话"), (400, "交谈")), rows)] == [i], "a talk after it stands"
    assert split.plan(reply((0, "交谈"), (100, "交谈")), rows[:4] + rows_at(range(400, 700, 30))) == [], "under 120 s before the cut"
    short = rows_at(range(0, 300, 30)) + rows_at(range(400, 460, 30))
    assert split.plan(reply((0, "交谈"), (400, "交谈")), short) == [], "under 120 s after the cut"
    tail = rows_at(range(0, 160, 20)) + rows_at(range(260, 360, 20)) + rows_at(range(460, 500, 20))  # speech 160|100|40 s
    cut = [{"i": 8, "scene": ""}, {"i": 13, "scene": ""}]
    assert [c["i"] for c in split.keep(tail, cut)] == [8], "a short tail joins the part before, the first cut stands"
    head = rows_at(range(0, 100, 20)) + rows_at(range(200, 400, 20)) + rows_at(range(500, 700, 20))  # speech 100|200|200 s
    assert [c["i"] for c in split.keep(head, [{"i": 5}, {"i": 15}])] == [15], "a short first part joins the next"
    no_pause = rows_at(range(0, 700, 30))
    assert split.plan(reply((0, "交谈"), (400, "交谈")), no_pause) == [], "no pause of 60 s: no cut"
    assert split.plan({"parts": "x"}, rows) == [] and split.plan({}, rows) == []


def test_snap_reach():
    rows = rows_at(range(0, 300, 30)) + rows_at(range(400, 1300, 30))
    i = next(k for k, r in enumerate(rows) if r["start"] == iso(400))
    at = lambda s: split.snap(rows, [{"i": next(k for k, r in enumerate(rows) if r["start"] == iso(s)), "scene": ""}])
    assert [c["i"] for c in at(700)] == [i], "300 s past the pause"
    assert at(730) == [], "330 s past the pause"


@pytest.fixture
def long_conv(store, monkeypatch):
    """10:00-10:08 我 and S1, a 100 s pause, 10:10-10:18 我 and S2; a done summary with a todo in each half, both marked;
    hints, voices, a pin, a profile, a recap and a star citing it."""
    rows = []
    for k, s in enumerate(range(0, 500, 30)):
        rows += rows_at([s], ("我", "S1")[k % 2])
    for k, s in enumerate(range(600, 1100, 30)):
        rows += rows_at([s], ("我", "S2")[k % 2])
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, dict(id=A, start=iso(0), end=rows[-1]["end"], participants=["S1", "S2", "我"],
                                       sentences=len(rows), speech_s=640, device=D))
        db.put_summary({"conversation": A, "status": "done", "title": "whole", "prompt_version": 13, "todos": [
            {"owner": "我", "to": "S1", "text": "first", "at": iso(60)}, {"owner": "我", "to": "S2", "text": "second", "at": iso(630)}]}, t)
        db.put_name_hints({"conv": A, "names": [{"who": "S1", "name": "a", "at": iso(30)}, {"who": "S2", "name": "b", "at": iso(630)}]}, t)
        db.insert_voices(t, [{"conv": A, "label": "S1", "device": "live", "e": E1, "k": 1, "level": -30.0},
                             {"conv": A, "label": "S2", "device": "live", "e": E2, "k": 1},
                             {"conv": A, "label": "我", "device": "live", "e": E3, "k": 1}])
        t.execute("INSERT INTO apart VALUES (?, ?)", (A + "|S2", "Named"))
        t.executemany("INSERT INTO stars VALUES (?, ?, ?)", [("r30", A, "now"), ("r630", A, "now")])
        db.put_timeline(A, {"conversation": A, "messages": []}, t)
    tids = db.todo_ids(A, db.summary(A)["todos"])
    db.mark_todos(tids, "done")
    db.put_profile({"name": "Named", "src": [{"conv": A, "at": iso(630)}, {"conv": A}]})
    db.put_kv("recap:2026-10-20", {"todos": [{"id": tids[1], "conv": A}], "highlights": [{"conv": A}]})
    calls = []

    async def complete(messages, timeout=300, kind="summary", **extra):
        calls.append({"kind": kind, **extra})
        if isinstance(reply := monkeypatch.reply, Exception):
            raise reply
        return reply, {}
    monkeypatch.reply = {"parts": [{"start": "10:00:00", "kind": "交谈", "scene": "one"}, {"start": "10:10:00", "kind": "交谈", "scene": "two"}]}
    monkeypatch.setattr(summary, "complete", complete)
    monkeypatch.setattr(split.ws, "broadcast", lambda msg: anyio.sleep(0))
    monkeypatch.setitem(core.state, "embed", None)
    return {"tids": tids, "calls": calls, "mp": monkeypatch}


def test_split_moves_every_reference(long_conv):
    tids = long_conv["tids"]
    c = db.conversation(A)
    assert split.due(c) and not split.checked(c, None)
    assert anyio.run(split.check, c, None) == [{"at": iso(600), "scene": "two"}]
    assert long_conv["calls"] == [{"kind": "split", "reasoning_effort": "high"}] * 3
    a, b = db.conversation(A), db.conversation(P2)
    assert (a["start"], a["end"], a["participants"]) == (iso(0), iso(500), ["S1", "我"])
    assert (b["start"], b["participants"], b["device"]) == (iso(600), ["S2", "我"], D)
    assert len(core.conversation_rows(a)) == 17 and core.conversation_rows(b)[0]["start"] == iso(600) and len(core.conversation_rows(b)) == 17
    sa, sb = db.summary(A), db.summary(P2)
    assert sa["status"] == sb["status"] == "pending" and sa["title"] == "whole" and sb["prompt_version"] == 13
    assert db.todo_ids(A, sa["todos"]) == [tids[0]] and [t["text"] for t in sb["todos"]] == ["second"]
    moved = db.todo_ids(P2, sb["todos"])[0]
    assert moved.startswith(P2 + ":split-")
    marks = {r["id"]: r["conv"] for r in db.conn().execute("SELECT id, conv FROM todo_marks")}
    assert marks == {tids[0]: A, moved: P2}
    assert [x["name"] for x in db.name_hints(A)[A]["names"]] == ["a"] and [x["name"] for x in db.name_hints(P2)[P2]["names"]] == ["b"]
    voices = {(r["conv"], r["label"]): dict(r) for r in db.conn().execute("SELECT * FROM voices")}
    assert set(voices) == {(A, "S1"), (A, "我"), (P2, "S2"), (P2, "我")}
    assert voices[(A, "S1")]["level"] == -30.0 and voices[(P2, "我")]["n"] == 9 and voices[(A, "我")]["n"] == 9
    assert db.vec(voices[(P2, "S2")]["e"]).tolist() == E2.tolist()
    assert {tuple(r) for r in db.conn().execute("SELECT voice, grp FROM apart")} == {(A + "|S2", "Named"), (P2 + "|S2", "Named")}
    assert db.profile("Named")["src"] == [{"conv": P2, "at": iso(630)}, {"conv": A}]
    recap = db.kv("recap:2026-10-20")
    assert recap["todos"] == [{"id": moved, "conv": P2}] and recap["highlights"] == [{"conv": A}] and recap["stale"]
    assert dict(db.conn().execute("SELECT id, conv FROM stars").fetchall()) == {"r30": A, "r630": P2}, "by its sentence's time"
    assert db.timeline(A) and not db.timeline(P2)
    assert [tuple(r) for r in db.conn().execute("SELECT device, at FROM walls")] == [(D, iso(600))]
    for cid in (A, P2):
        assert split.checked(db.conversation(cid), db.kv(f"conversation_revision:{cid}"))
    assert len(db.conversations()) == 2


def test_change_during_the_call_applies_nothing(long_conv):
    reply = long_conv["mp"].reply

    async def complete(messages, timeout=300, kind="summary", **extra):
        db.put_kv(f"conversation_revision:{A}", 1)  # late audio merged into it meanwhile
        return reply, {}
    long_conv["mp"].setattr(summary, "complete", complete)
    before = db.summary(A)
    assert anyio.run(split.check, db.conversation(A), None) == []
    assert len(db.conversations()) == 1 and db.kv(f"split_checked:{A}") is None and db.summary(A) == before


def test_no_split_on_error_or_one_part(long_conv):
    long_conv["mp"].reply = RuntimeError("synthetic")
    c = db.conversation(A)
    assert anyio.run(split.check, c, None) == []
    assert db.conversation(A)["end"] == c["end"] and not split.checked(c, None), "an error: checked again next time"
    long_conv["mp"].reply = {"parts": [{"start": "10:00:00", "kind": "交谈", "scene": "one"}]}
    assert anyio.run(split.check, c, None) == [] and split.checked(c, None)
    assert db.summary(A)["status"] == "done" and len(db.conversations()) == 1
    assert not split.due({**c, "speech_s": 2 * split.PART_S - 1}), "no speech for two parts: no call"
    long_conv["mp"].setattr(split, "SPLIT_MIN_S", 0)
    assert not split.due(c)


def test_summary_worker_splits_then_summarizes_each_part(long_conv):
    texts = []

    async def chat(text, *args):
        texts.append(text)
        return {"title": f"part {len(texts)}", "summary": "s", "todos": []}, {"model": "fake"}
    long_conv["mp"].setattr(summary, "chat", chat)
    long_conv["mp"].setattr(summaries.ws, "broadcast", lambda msg: anyio.sleep(0))
    long_conv["mp"].setattr(summaries, "name_hints", lambda c, media: anyio.sleep(0))

    async def run():
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(summaries.summarize_loop)
                core.queue_summary(db.conversation(A))
                while not all((db.summary(x) or {}).get("status") == "done" for x in (A, P2)):
                    await anyio.sleep(0.01)
                tg.cancel_scope.cancel()
    anyio.run(run)
    assert len(texts) == 2 and [x["kind"] for x in long_conv["calls"]] == ["split"] * 3
    assert "line 0" in texts[0] + texts[1] and not any("line 0" in x and "line 600" in x for x in texts)


def test_wall_keeps_split_parts_apart(store):
    with db.tx() as t:
        conv(t, A, 0, 600)
        conv(t, B, 660, 1200)
        t.execute("INSERT INTO walls VALUES (?, ?)", (D, iso(660)))
    vectors = {A: {"S1": E1}, B: {"S1": E1}}
    changed, removed = late_merge.attach(span(640, 655), [row(640, 655)], {"S1": E1}, vectors)
    assert not removed and [c["id"] for c in changed] == [B], "a late row in the gap goes to the nearer part"
    assert db.conversation(B)["start"] == iso(640)
    changed, removed = late_merge.attach(span(605, 620), [row(605, 620)], {"S1": E1}, vectors)
    assert not removed and [c["id"] for c in changed] == [A], "the wall holds after a part grew toward it"
    assert {c["id"] for c in db.conversations()} == {A, B}


def test_recheck_merges_back_a_cut_that_no_longer_stands(long_conv, tmp_path):
    tids = long_conv["tids"]
    anyio.run(split.check, db.conversation(A), None)
    moved = db.todo_ids(P2, db.summary(P2)["todos"])[0]
    with db.tx() as t:  # a third part, its wall kept
        db.insert_sentences(t, rows_at(range(1200, 1500, 30), "S3"))
        db.insert_conversation(t, dict(id="c20261020102000", start=iso(1200), end=iso(1500), participants=["S3"], sentences=10,
                                       speech_s=200, device=D))
        t.execute("INSERT INTO walls VALUES (?, ?)", (D, iso(1200)))
    two = {"start": "10:20:00", "kind": "交谈", "scene": "three"}
    long_conv["mp"].reply = {"parts": [{"start": "10:00:00", "kind": "独自做事", "scene": "solo"},
                                       {"start": "10:10:00", "kind": "通话", "scene": "call"}, two]}
    res = anyio.run(split.recheck, "2026-10-20")
    assert res == {"backup": None, "conversations": [{"id": A, "start": iso(0), "walls": [iso(600), iso(1200)], "undone": [iso(600)]}]}
    assert db.conversation(P2) and len(db.conversations()) == 3, "a dry run writes nothing"

    counts = {x: db.conn().execute(f"SELECT count(*) FROM {x}").fetchone()[0] for x in TABLES}
    with db.tx() as t:  # its summary and the day's recap were done again since the split
        db.put_summary({**db.summary(A), "status": "done"}, t)
        db.put_kv("recap:2026-10-20", {**db.kv("recap:2026-10-20"), "stale": False}, t)
    recap_rev = db.kv("recap_revision:2026-10-20")
    T().undo.update(a=[{"conv": A}], b=[{"conv": P2}], c=[{"conv": "c20261020102000"}])
    res = anyio.run(split.recheck, "2026-10-20", True)
    assert res["conversations"][0]["undone"] == [iso(600)] and list(tmp_path.glob("live.db.before-recheck-*")) == [tmp_path / res["backup"].split("/")[-1]]
    assert os.stat(res["backup"]).st_mode & 0o777 == 0o600
    with contextlib.closing(sqlite3.connect(res["backup"])) as b:
        assert b.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert {x: b.execute(f"SELECT count(*) FROM {x}").fetchone()[0] for x in TABLES} == counts, "the store before the merge"
    assert set(T().undo) == {"c"}, "an undo on either side of the merge would restore voices from before it"
    assert db.kv("recap:2026-10-20")["stale"] and db.kv("recap_revision:2026-10-20") == recap_rev + 1
    a = db.conversation(A)
    assert (a["start"], a["end"], a["participants"], a["sentences"]) == (iso(0), iso(1100), ["S1", "S2", "我"], 34)
    assert db.resolve_conversation(P2) == A and db.conversation(P2)["id"] == A, "the later part's id is an alias"
    assert [tuple(r) for r in db.conn().execute("SELECT at FROM walls")] == [(iso(1200),)], "the third part's wall stays"
    s = db.summary(A)
    assert s["status"] == "pending" and db.todo_ids(A, s["todos"])[0] == tids[0] and len(s["todos"]) == 2
    assert {r["conv"] for r in db.conn().execute("SELECT conv FROM todo_marks")} == {A} and moved not in {
        r["id"] for r in db.conn().execute("SELECT id FROM todo_marks")}
    assert dict(db.conn().execute("SELECT id, conv FROM stars").fetchall()) == {"r30": A, "r630": A}
    assert {(r["conv"], r["label"]) for r in db.conn().execute("SELECT conv, label FROM voices WHERE conv = ?", (A,))} == {
        (A, "S1"), (A, "S2"), (A, "我")}
    assert not split.checked(a, db.kv(f"conversation_revision:{A}")), "the summary worker checks it again, as a new one"
    assert anyio.run(split.recheck, "2026-10-20", True)["conversations"][0]["undone"] == [], "the remaining cut stands"


def test_a_cut_stands_only_when_every_call_names_it(long_conv):
    cut, whole = ({"parts": [{"start": "10:00:00", "kind": "交谈", "scene": "one"}] + extra} for extra in
                  ([{"start": "10:10:00", "kind": "交谈", "scene": "two"}], []))
    replies = iter([cut, cut, whole])

    async def complete(messages, timeout=300, kind="summary", **extra):
        return next(replies), {}
    long_conv["mp"].setattr(summary, "complete", complete)
    c = db.conversation(A)
    assert anyio.run(split.check, c, None) == [] and split.checked(c, None) and len(db.conversations()) == 1


def replies(long_conv, *rs):
    """The model's replies in turn (an exception: that call fails); each call can run a side effect first (call)."""
    rs, n = list(rs), []

    async def complete(messages, timeout=300, kind="summary", **extra):
        r = rs[len(n) % len(rs)]
        n.append(1)
        if callable(r):
            r = r()
        if isinstance(r, Exception):
            raise r
        return r, {}
    long_conv["mp"].setattr(summary, "complete", complete)


def dump():
    return {x: sorted(map(tuple, db.conn().execute(f"SELECT * FROM {x}")), key=repr) for x in TABLES}


def test_one_failed_call_means_no_split(long_conv):
    replies(long_conv, long_conv["mp"].reply, RuntimeError("synthetic"), long_conv["mp"].reply)
    c = db.conversation(A)
    assert anyio.run(split.check, c, None) == []
    assert len(db.conversations()) == 1 and not split.checked(c, None), "checked again next time"


def test_recheck_keeps_a_chain_when_a_call_fails(long_conv):
    anyio.run(split.check, db.conversation(A), None)
    before = dump()
    replies(long_conv, WHOLE, RuntimeError("502 Bad Gateway"), WHOLE)
    res = anyio.run(split.recheck, "2026-10-20", True)
    assert res["backup"] is None and res["conversations"][0]["undone"] == [] and "502" in res["conversations"][0]["error"]
    assert dump() == before and db.resolve_conversation(P2) == P2


def test_recheck_undoes_nothing_when_a_part_changed_during_the_call(long_conv):
    anyio.run(split.check, db.conversation(A), None)
    before = dump()
    replies(long_conv, lambda: db.put_kv(f"conversation_revision:{P2}", 99) or WHOLE)  # late audio merged into it meanwhile
    res = anyio.run(split.recheck, "2026-10-20", True)
    assert res["conversations"][0]["undone"] == [] and dump() == before


def test_recheck_keeps_two_people_with_one_label_apart(long_conv):
    """Late audio gave the first part an S2 of its own, another voice than the later part's S2: merged, they stay two."""
    anyio.run(split.check, db.conversation(A), None)
    with db.tx() as t:
        db.insert_sentences(t, [{"id": "late", "start": iso(21), "end": iso(29), "speaker": "S2", "text": "late", "source": D}])
        db.insert_voices(t, [{"conv": A, "label": "S2", "device": "live", "e": E1, "k": 1}])
    long_conv["mp"].reply = WHOLE
    anyio.run(split.recheck, "2026-10-20", True)
    speakers = {r["id"]: r["speaker"] for r in db.sentences(iso(0), iso(1100), True, D)}
    assert speakers["late"] == "S2" and speakers["r630"] == "S3" and speakers["r600"] == "我"
    voices = {r["label"]: db.vec(r["e"]).tolist() for r in db.conn().execute("SELECT label, e FROM voices WHERE conv = ?", (A,))}
    assert voices["S2"] == E1.tolist() and voices["S3"] == E2.tolist(), "neither voiceprint averaged with the other"
    assert db.conversation(A)["participants"] == ["S1", "S2", "S3", "我"]
    assert json.loads(db.conn().execute("SELECT labels FROM conversation_aliases WHERE old = ?", (P2,)).fetchone()[0])["S2"] == "S3"
