"""Todos: the due list holds my dated todos of today and before, each with its id, context and how stale it is; 完成 /
不对 by id (one, many, undone) take a todo off the due list and the recap card; the marks kept as feedback lines before
todo_marks are carried over once."""
import datetime as dt

import anyio
import pytest
from fastapi import HTTPException

from alldayrec import db
from alldayrec.routes_api import conversation, recap_get, todo_mark, todo_marks, todo_unmark, todos_due

C1, C2 = "c20261001100000", "c20261001110000"


def mine(text, due_date, due="某天", **kw):
    return {"owner": "我", "text": text, "due": due, "due_date": due_date, **kw}


def store(tmp_path, todos1, todos2=(), v1=8):
    db.open_db(tmp_path / "t.db")
    with db.tx() as c:
        db.insert_conversation(c, dict(id=C1, start="2026-10-01T10:00:00.000", end="2026-10-01T10:30:00.000", participants=["我", "S1", "王哥"],
                                       sentences=1))
        db.insert_conversation(c, dict(id=C2, start="2026-10-01T11:00:00.000", end="2026-10-01T11:30:00.000", participants=["我"], sentences=1))
        db.put_summary({"conversation": C1, "status": "done", "prompt_version": v1, "title": "周会", "todos": list(todos1)}, c)
        db.put_summary({"conversation": C2, "status": "done", "prompt_version": 8, "todos": list(todos2)}, c)
        c.execute("INSERT INTO aliases VALUES ('王哥', '老王')")


def test_due_context_and_stale(tmp_path):
    day = "2026-10-20"
    d = lambda n: (dt.date.fromisoformat(day) - dt.timedelta(days=n)).isoformat()
    store(tmp_path, [mine("今天的", d(0), at="2026-10-01T10:05:00.000", quote="我今天把文档发你", to="S1"),
                     mine("明天的", (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()), mine("没日期", None),
                     {"owner": "S1", "text": "别人的", "due": "今天", "due_date": d(0)},
                     mine("过了七天", d(7)), mine("过了八天", d(8)), mine("说明天的过了三天", d(3), "明天"), mine("说待会儿的过了四天", d(4), "待会儿")],
          [mine("没标题的", d(1))])
    got = {t["text"]: t for t in db.due_todos(day)}
    assert list(got) == ["过了八天", "过了七天", "说待会儿的过了四天", "说明天的过了三天", "没标题的", "今天的"], "oldest due first, mine, dated, not later"
    assert {k: (t["age_days"], t["stale"]) for k, t in got.items()} == {
        "过了八天": (8, True), "过了七天": (7, False), "说待会儿的过了四天": (4, True), "说明天的过了三天": (3, False), "没标题的": (1, False),
        "今天的": (0, False)}
    assert got["今天的"] == {"id": f"{C1}:2026-10-01T10:05:00.000", "mark": None, "conv": C1, "text": "今天的", "to": "S1", "due": "某天",
                           "due_date": day, "at": "2026-10-01T10:05:00.000", "quote": "我今天把文档发你", "title": "周会",
                           "start": "2026-10-01T10:00:00.000", "with": ["S1"], "age_days": 0, "stale": False}
    assert got["过了八天"]["with"] == ["S1", "老王"] and got["过了八天"]["id"].startswith(f"{C1}:") and "#" not in got["过了八天"]["id"], "no to: the others, aliases as their person"
    assert got["没标题的"]["title"] is None and got["没标题的"]["with"] == []
    store(tmp_path / "v7", [mine("旧版", d(0))], v1=7)
    assert db.due_todos(day) == [], "before prompt v8 a due_date is not trusted"


def test_marks_undo_and_bulk(tmp_path):
    today = dt.date.today().isoformat()
    store(tmp_path, [mine("一", today, at="2026-10-01T10:01:00.000"), mine("二", today, at="2026-10-01T10:02:00.000"), mine("三", today)])
    ids = [t["id"] for t in anyio.run(todos_due)]
    assert len(ids) == 3 and len(set(ids)) == 3
    assert anyio.run(todo_mark, ids[0], {"mark": "done"}) == {"id": ids[0], "mark": "done"}
    assert [t["id"] for t in anyio.run(todos_due)] == ids[1:]
    assert [(t["text"], t["mark"]) for t in anyio.run(conversation, C1)["summary"]["todos"]] == [("一", "done"), ("二", None), ("三", None)]
    anyio.run(todo_mark, ids[0], {"mark": "wrong"})
    assert db.closed_todos() == {ids[0]: "wrong"}, "a mark again replaces it"
    anyio.run(todo_unmark, ids[0])
    assert [t["id"] for t in anyio.run(todos_due)] == ids, "undone: open again"
    anyio.run(todo_marks, {"ids": ids, "mark": "done"})
    assert anyio.run(todos_due) == []
    anyio.run(todo_marks, {"ids": ids[1:], "mark": None})
    assert [t["id"] for t in anyio.run(todos_due)] == ids[1:], "the bulk undo"
    for f, args, code in [(todo_mark, (ids[0], {"mark": "maybe"}), 400), (todo_mark, (f"{C1}:nope", {"mark": "done"}), 404),
                          (todo_marks, ({"ids": [], "mark": "done"},), 400), (todo_marks, ({"ids": ids[:1], "mark": "x"},), 400),
                          (todo_marks, ({"ids": [ids[0], "c1:x"], "mark": "done"},), 404)]:
        with pytest.raises(HTTPException) as e:
            anyio.run(f, *args)
        assert e.value.status_code == code, (args, e.value.detail)
    assert db.closed_todos() == {ids[0]: "done"}, "a refused request marks nothing"


def test_recap_by_id(tmp_path):
    today = dt.date.today().isoformat()
    store(tmp_path, [mine("做完了", today, at="2026-10-01T10:01:00.000"), mine("今天的", today, at="2026-10-01T10:02:00.000", quote="原话")])
    db.put_kv("recap:2026-10-01", {"date": "2026-10-01", "todos": [
        {"id": f"{C1}:2026-10-01T10:01:00.000", "conv": C1, "text": "做完了", "short": "做完"},
        {"conv": C1, "text": "今天的", "short": "今天"},  # a recap written before ids: found by its text
        {"conv": C1, "text": "改写过的"}]})  # before short, text held the recap's wording: no todo, kept as it is
    anyio.run(todo_mark, f"{C1}:2026-10-01T10:01:00.000", {"mark": "done"})
    got = anyio.run(recap_get)["todos"]
    assert [(t.get("id"), t["text"], t.get("short"), t.get("quote")) for t in got] == [
        (f"{C1}:2026-10-01T10:02:00.000", "今天的", "今天", "原话"), (None, "改写过的", None, None)]
    anyio.run(todo_unmark, f"{C1}:2026-10-01T10:01:00.000")
    assert len(anyio.run(recap_get, "2026-10-01")["todos"]) == 3


def test_feedback_marks_carried_over(tmp_path):
    today = dt.date.today().isoformat()
    store(tmp_path, [mine("做完了", today, at="2026-10-01T10:01:00.000"), mine("记错了", today), mine("还开着", today)])
    db.add_feedback(C1, db.TODO_MARKS[0] + "做完了")
    db.add_feedback(C1, db.TODO_MARKS[1] + "记错了")
    db.add_feedback(C1, db.TODO_MARKS[0] + "改写前的说法")  # the summary was redone since: no todo by that text
    db.add_feedback(C1, "S2 其实是我")
    db.conn().execute("DROP TABLE todo_marks")  # a store from before todo_marks
    db.init()
    assert db.closed_todos() == {f"{C1}:2026-10-01T10:01:00.000": "done", db.todo_by_text(C1, "记错了")[0]: "wrong"}
    assert [t["text"] for t in anyio.run(todos_due)] == ["还开着"]
    assert len(db.feedback(C1)) == 4, "the feedback lines stay"
    anyio.run(todo_unmark, f"{C1}:2026-10-01T10:01:00.000")
    db.init()
    assert len(db.closed_todos()) == 1, "once: an undone mark is not carried over again"


def test_ids_unique_per_summary(tmp_path):
    """Two todos said in one sentence share their at: the later gets #2 (in the summary's order), and each is marked on
    its own, the carried-over feedback marks too."""
    today, at = dt.date.today().isoformat(), "2026-10-01T10:05:00.000"
    store(tmp_path, [mine("发文档", today, at=at), mine("约会议", today, at=at), mine("没时间", today), mine("没时间", today), mine("别的", today)])
    ids = db.todo_ids(C1, db.summary(C1)["todos"])
    assert ids[:2] == [f"{C1}:{at}", f"{C1}:{at}#2"] and ids[3] == ids[2] + "#2" and len(set(ids)) == 5, ids
    assert [t["id"] for t in anyio.run(todos_due)] == ids and db.todo(ids[1])["text"] == "约会议"
    anyio.run(todo_mark, ids[1], {"mark": "done"})
    assert [t["text"] for t in anyio.run(todos_due)] == ["发文档", "没时间", "没时间", "别的"], "the sibling stays open"
    assert [t["mark"] for t in anyio.run(conversation, C1)["summary"]["todos"]] == [None, "done", None, None, None]
    db.add_feedback(C1, db.TODO_MARKS[1] + "约会议")
    db.conn().execute("DROP TABLE todo_marks")
    db.init()
    assert db.closed_todos() == {ids[1]: "wrong"}, "the migration names todos the same way"
