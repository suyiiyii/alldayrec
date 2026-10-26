"""Todos: the due list holds my dated todos of today and before, each with its id, context and how stale it is; 完成 /
不对 by id (one, many, undone) take a todo off the due list and the recap card; the marks kept as feedback lines before
todo_marks are carried over once, and onto a redone summary's todos."""
import datetime as dt

import anyio
import pytest
from fastapi import HTTPException

from alldayrec import db
from alldayrec import summaries
from alldayrec.routes_api import conversation, recap_get, resummarize, todo_mark, todo_marks, todo_unmark, todos_due

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


def test_redo_keeps_summary_and_marks(tmp_path, monkeypatch):
    """重新生成 (and resummarize.py) keeps the old summary until the new one is written; the new todos keep the old ones'
    marks through unique text or quote matches; a timestamp alone is not identity. Old marked ids stay reserved
    because a recap can name its todos by them."""
    today = dt.date.today().isoformat()
    a = "2026-10-01T10:01:00.000"
    store(tmp_path, [mine("发文档", today, at=a, quote="我把文档发你"), mine("约会议", today, at="2026-10-01T10:02:00.000", quote="周五开会"),
                     mine("买票", today), mine("没标的", today, at="2026-10-01T10:09:00.000")])
    db.conn().execute("INSERT INTO stars VALUES (?, ?, ?)", ("sentence", C1, today))
    recap = {"date": today, "headline": "before", "todos": []}
    db.put_kv(f"recap:{today}", recap)
    old = db.todo_ids(C1, db.summary(C1)["todos"])
    anyio.run(todo_marks, {"ids": old[:3], "mark": "done"})
    new = [mine("把文档发给他", today, at=a, quote="我把文档发你"), mine("约个会", today, at="2026-10-01T10:03:00.000", quote="周五开会"),
           mine("买票", today, at="2026-10-01T10:04:00.000"), mine("新的", today, at="2026-10-01T10:05:00.000")]

    async def fake(c, tags):
        return {"conversation": c["id"], "status": "done", "prompt_version": 11, "model": None, "title": "周会", "summary": "s", "todos": new}

    async def redo():
        await resummarize(C1)
        assert db.summary(C1)["prompt_version"] == 8, "the old summary stays meanwhile"
        async with anyio.create_task_group() as tg:
            tg.start_soon(summaries.summarize_loop)
            while db.summary(C1)["prompt_version"] != 11:
                await anyio.sleep(0.01)
            tg.cancel_scope.cancel()

    monkeypatch.setattr(summaries, "summarize", fake)
    anyio.run(redo)
    assert [(t["text"], t["mark"]) for t in anyio.run(conversation, C1)["summary"]["todos"]] == [
        ("把文档发给他", "done"), ("约个会", "done"), ("买票", "done"), ("新的", None)]
    assert set(old[:3]) == set(db.closed_todos()), "the original mark rows never move or duplicate"
    assert db.todo_ids(C1, db.summary(C1)["todos"])[:3] == old[:3]
    assert [tuple(x) for x in db.conn().execute("SELECT * FROM stars")] == [("sentence", C1, today)]
    assert db.kv(f"recap:{today}") == recap


@pytest.mark.parametrize("change", ["reorder", "missing", "ambiguous", "owner", "historical"])
def test_redo_preserves_unmatched_marked_todos(tmp_path, monkeypatch, change):
    """Reorder/missing/ambiguous/different-owner outputs never block regeneration or move marks."""
    at = "2026-10-01T10:01:00.000"
    old = [mine("发文档", None, at=at, quote="发文档和约会议"), mine("约会议", None, at=at, quote="发文档和约会议")]
    store(tmp_path, old)
    ids = db.todo_ids(C1, old)
    db.mark_todos(ids[:1], "wrong")
    new = {
        "reorder": old[::-1],
        "missing": old[1:],
        "ambiguous": [{**t, "text": "改写"} for t in old],
        "owner": [{**old[0], "owner": "S1"}, old[1]],
        "historical": old + [mine("新的", None, at="historical")],
    }[change]
    if change == "historical":
        db.mark_todos([f"{C1}:historical"], "done")
    before_marks = db.closed_todos()
    before = db.summary(C1)

    async def fake(c, tags):
        return {**before, "prompt_version": 11, "summary": "new", "todos": new}

    async def run():
        await resummarize(C1)
        async with anyio.create_task_group() as tg:
            tg.start_soon(summaries.summarize_loop)
            with anyio.fail_after(2):
                while db.summary(C1).get("summary") != "new":
                    await anyio.sleep(.01)
            tg.cancel_scope.cancel()
        got = db.summary(C1)
        assert got["prompt_version"] == 11 and not got.get("error")
        assert db.closed_todos() == before_marks
        assert db.todo(ids[0])["text"] == old[0]["text"] and db.todo(ids[0])["owner"] == "我"
        current_ids = db.todo_ids(C1, got["todos"])
        assert len(current_ids) == len(set(current_ids))
        if change == "historical":
            assert f"{C1}:historical" not in current_ids

    monkeypatch.setattr(summaries, "summarize", fake)
    anyio.run(run)


def test_cached_recap_never_marks_reused_id(tmp_path):
    at = "2026-10-01T10:01:00.000"
    store(tmp_path, [mine("发文档", None, at=at), mine("约会议", None, at=at)])
    ids = db.todo_ids(C1, db.summary(C1)["todos"])
    db.put_kv("recap:2026-10-01", {"date": "2026-10-01", "todos": [
        {"conv": C1, "id": ids[0], "text": "发文档", "short": "发文档"},
        {"conv": C1, "id": ids[1], "text": "约会议", "short": "约会议"}]})
    db.put_summary({"conversation": C1, "status": "done", "todos": [mine("买票", None, at=at), mine("发文档", None, at=at)]})
    got = anyio.run(recap_get, "2026-10-01")["todos"]
    assert [(t["text"], t["id"]) for t in got] == [("发文档", ids[1]), ("约会议", None)]
    anyio.run(todo_mark, got[0]["id"], {"mark": "done"})
    assert db.todo(ids[0])["text"] == "买票" and ids[0] not in db.closed_todos()


def test_cached_recap_keeps_historical_mark_filter(tmp_path):
    at = "2026-10-01T10:01:00.000"
    store(tmp_path, [mine("发文档", None, at=at, quote="我把文档发你")])
    id = db.todo_ids(C1, db.summary(C1)["todos"])[0]
    db.put_kv("recap:2026-10-01", {"date": "2026-10-01", "todos": [{"conv": C1, "id": id, "text": "发文档"}]})
    db.mark_todos([id], "done")
    new = [mine("把文档发给他", None, at=at, quote="我把文档发你")]
    with db.tx() as c:
        new = db.preserve_todos(C1, db.summary(C1)["todos"], new, c)
        db.put_summary({"conversation": C1, "status": "done", "todos": new}, c)
    assert anyio.run(recap_get, "2026-10-01")["todos"] == [], "a completed reworded action stays out of the cached recap"


@pytest.mark.parametrize("to", ["Bob", "Alice"])
def test_marked_todos_survive_rename_merge_and_undo(tmp_path, monkeypatch, to):
    import json
    from alldayrec.routes_speakers import apply_rename, keep_undo, speaker_undo
    old = [dict(owner="S1", to="我", text="发文档", quote="文档给你", at="2026-10-01T10:01:00.000"),
           dict(owner="我", to="S1", text="买票", quote="票给你", at="2026-10-01T10:02:00.000"),
           dict(owner="Alice", to="我", text="约会议", quote="约个会议", at="2026-10-01T10:03:00.000")]
    store(tmp_path, old)
    db.conn().execute("UPDATE conversations SET participants=? WHERE id=?", (json.dumps(["我", "S1", "Alice"]), C1))
    with db.tx() as c:
        db.insert_sentences(c, [dict(id="one", start=old[0]["at"], end="2026-10-01T10:01:05.000", speaker="S1", text="文档给你", source="mic")])
    ids = db.todo_ids(C1, old)
    db.mark_todos(ids, "done")
    rounds = 0
    async def fake(c, tags):
        nonlocal rounds
        rounds += 1
        ts = db.summary(C1)["todos"]
        return {"conversation": C1, "status": "done", "prompt_version": 11, "model": None, "title": "t", "summary": f"round{rounds}",
                "todos": [{**t, "text": t["text"] + "!", "at": None} for t in ts if rounds != 1 or t["id"] != ids[0]]}
    async def wait(n):
        with anyio.fail_after(2):
            while db.summary(C1).get("summary") != f"round{n}":
                await anyio.sleep(.01)
    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(summaries.summarize_loop)
            _, _, before = apply_rename(C1, "S1", to)
            await wait(1)
            assert db.todo(ids[0])["owner"] == to and db.todo(ids[1])["to"] == to
            assert db.todo(ids[2])["owner"] == "Alice"
            await speaker_undo({"undo": keep_undo([before])})
            await wait(2)
            assert db.todo(ids[0])["owner"] == "S1" and db.todo(ids[1])["to"] == "S1"
            assert db.todo(ids[2])["owner"] == "Alice", "undo a merge must not rename the original Alice's action"
            await resummarize(C1)
            await wait(3)
            tg.cancel_scope.cancel()
    monkeypatch.setattr(summaries, "summarize", fake)
    anyio.run(run)
    assert db.closed_todos() == {i: "done" for i in ids}
    assert set(db.todo_ids(C1, db.summary(C1)["todos"])) == set(ids)


def test_pinned_todo_regeneration_undo_and_cached_recap(tmp_path):
    old = [mine("发文档", None, at="2026-10-01T10:01:00.000", quote="文档给你")]
    store(tmp_path, old)
    id = db.todo_ids(C1, old)[0]
    db.put_kv("recap:2026-10-01", {"todos": [{"conv": C1, "id": id, "text": "发文档"}]})
    db.mark_todos([id], "done")
    for n in range(3):
        with db.tx() as c:
            ts = db.preserve_todos(C1, db.summary(C1)["todos"], [mine(f"发出文档{n}", None, at=None, quote="文档给你")], c)
            db.put_summary({"conversation": C1, "status": "done", "todos": ts}, c)
        assert db.todo_ids(C1, ts) == [id] and db.closed_todos() == {id: "done"}
        assert anyio.run(recap_get, "2026-10-01")["todos"] == []
    anyio.run(todo_unmark, id)  # current ID and an old toast's ID are identical
    got = anyio.run(recap_get, "2026-10-01")["todos"]
    assert [(t["id"], t["text"], t["mark"]) for t in got] == [(id, "发出文档2", None)]
    anyio.run(todo_marks, {"ids": [id], "mark": "wrong"})
    assert anyio.run(recap_get, "2026-10-01")["todos"] == []
    anyio.run(todo_marks, {"ids": [id], "mark": None})
    assert len(anyio.run(recap_get, "2026-10-01")["todos"]) == 1 and db.closed_todos() == {}


def test_recap_supplies_linked_participants(tmp_path):
    store(tmp_path, [])
    db.put_kv("recap:2026-10-01", {"todos": [], "highlights": [{"conv": C1, "text": "S1 uses S3"}, {"conv": None, "text": "Galaxy S24"}]})
    h = anyio.run(recap_get, "2026-10-01")["highlights"]
    assert h[0]["participants"] == ["我", "S1", "王哥"] and "participants" not in h[1]


@pytest.mark.parametrize("mark", [None, "done", "wrong"])
@pytest.mark.parametrize("cached_id", [False, True])
def test_legacy_recap_scopes_match_to_my_todos(tmp_path, mark, cached_id):
    old = [mine("发文档", "2026-10-01", at="2026-10-01T10:01:00.000", to="S2"),
           mine("发文档", "2026-10-01", owner="S1", at="2026-10-01T10:02:00.000", to="S2")]
    store(tmp_path, old)
    id = db.todo_ids(C1, old)[0]
    entry = {"conv": C1, "text": "发文档", "to": "S2", "due": "某天", **({"id": id} if cached_id else {})}
    db.put_kv("recap:2026-10-01", {"todos": [entry]})
    db.mark_todos([id], mark)
    before = db.summary(C1), db.closed_todos()
    got = anyio.run(recap_get, "2026-10-01")["todos"]
    if mark:
        assert got == [], "completed/wrong legacy actions must stay hidden despite another owner's identical text"
    else:
        assert [(t["id"], t["mark"]) for t in got] == [(id, None)], "the unique own action keeps its buttons"
    assert (db.summary(C1), db.closed_todos()) == before, "reading legacy data does not rewrite it"


@pytest.mark.parametrize("mark", ["done", "wrong"])
def test_undo_this_is_me_omits_carried_other_person_action(tmp_path, monkeypatch, mark):
    from alldayrec.routes_speakers import apply_rename, keep_undo, speaker_undo
    old = [mine("发送资料", "2026-10-01", owner="S1", at="2026-10-01T10:01:00.000", quote="我发送资料")]
    store(tmp_path, old)
    id = db.todo_ids(C1, old)[0]
    with db.tx() as c:
        db.insert_sentences(c, [dict(id="r", start=old[0]["at"], end="2026-10-01T10:01:05.000", speaker="S1", text="我发送资料")])
    rounds = 0
    async def model(c, tags):
        nonlocal rounds
        rounds += 1
        return {**db.summary(C1), "summary": f"round{rounds}", "prompt_version": 11, "todos": []}
    async def run():
        _, _, before = apply_rename(C1, "S1", "我")
        db.put_kv("recap:2026-10-01", {"todos": [{"conv": C1, "id": id, "text": "发送资料"}]})
        db.mark_todos([id], mark)
        assert (await recap_get("2026-10-01"))["todos"] == []
        await speaker_undo({"undo": keep_undo([before])})
        async with anyio.create_task_group() as tg:
            tg.start_soon(summaries.summarize_loop)
            with anyio.fail_after(2):
                while db.summary(C1).get("summary") != "round1":
                    await anyio.sleep(.01)
            tg.cancel_scope.cancel()
        assert rounds == 1, "undo supersedes the rename before its queued model call starts"
        assert db.todo(id)["id"] == id and db.todo(id)["owner"] == "S1"
        assert db.closed_todos() == {id: mark}
        assert (await recap_get("2026-10-01"))["todos"] == []
    monkeypatch.setattr(summaries, "summarize", model)
    anyio.run(run)


@pytest.mark.parametrize("mark", [None, "done", "wrong"])
def test_pinned_recap_does_not_fall_back_to_same_name_owned_by_me(tmp_path, mark):
    old = [mine("发文档", None, owner="S1", at="2026-10-01T10:01:00.000"),
           mine("发文档", None, at="2026-10-01T10:02:00.000")]
    store(tmp_path, old)
    ids = db.todo_ids(C1, old)
    with db.tx() as c:
        ts = db.preserve_todos(C1, old, old, c)
        db.put_summary({**db.summary(C1), "todos": ts}, c)
    db.put_kv("recap:2026-10-01", {"todos": [{"conv": C1, "id": ids[0], "text": "发文档"}]})
    db.mark_todos([ids[0]], mark)
    assert anyio.run(recap_get, "2026-10-01")["todos"] == [], "a resolved other-owner action never borrows my same-name action"


@pytest.mark.parametrize("case", ["two_new_matches", "text_quote_competition", "timestamp_reuse"])
def test_preserve_todo_identity_edge_cases(tmp_path, case):
    at, later = "2026-10-01T10:01:00.000", "2026-10-01T10:02:00.000"
    old, new = {
        "two_new_matches": ([mine("A", None, quote="Q", at=at)],
                            [mine("A", None, quote="Q", at=at), mine("A", None, quote="Q", at=at)]),
        "text_quote_competition": ([mine("A", None, quote="Q1", at=at), mine("B", None, quote="Q2", at=at)],
                                   [mine("A", None, quote="Q2", at=at)]),
        "timestamp_reuse": ([mine("A", None, at=at)], [mine("fresh", None, at=at), mine("A", None, at=later)]),
    }[case]
    store(tmp_path, old)
    ids = db.todo_ids(C1, old)
    db.mark_todos(ids, "done")
    mark_rows = [tuple(r) for r in db.conn().execute("SELECT * FROM todo_marks ORDER BY id")]
    # Incoming model IDs are not authoritative, even when they collide with an old marked action.
    new = [{**t, "id": ids[0]} for t in new]
    with db.tx() as c:
        ts = db.preserve_todos(C1, old, new, c)
        db.put_summary({**db.summary(C1), "todos": ts}, c)
    actual = db.todo_ids(C1, ts)
    assert len(actual) == len(set(actual)) and set(ids) <= set(actual)
    assert [tuple(r) for r in db.conn().execute("SELECT * FROM todo_marks ORDER BY id")] == mark_rows
    assert [db.todo(id)["text"] for id in ids] == [t["text"] for t in old]
    if case == "two_new_matches":
        assert actual[-1] == ids[0] and ids[0] not in actual[:2], "ambiguous marked action is appended with its original ID"
    elif case == "text_quote_competition":
        assert actual == ids, "the used candidate is not reassigned; the competing marked action is appended"
    else:
        assert actual[1] == ids[0] and actual[0] != ids[0], "a fresh action at the old timestamp cannot reuse the old ID"


@pytest.mark.parametrize("mark", ["done", "wrong"])
def test_unmark_carried_todo_reopens_then_next_regeneration_omits_it(tmp_path, mark):
    from alldayrec import mcp_server, resummarize as backfill
    old = [mine("carried A", "2026-10-01", at="2026-10-01T10:01:00.000", quote="quote A"),
           mine("carried B", "2026-10-01", at="2026-10-01T10:02:00.000", quote="quote B")]
    store(tmp_path, old)
    ids = db.todo_ids(C1, old)
    db.mark_todos(ids, mark)
    db.put_kv("recap:2026-10-01", {"todos": [{"conv": C1, "id": id, "text": t["text"]} for id, t in zip(ids, old)]})
    marks_before = [tuple(r) for r in db.conn().execute("SELECT * FROM todo_marks ORDER BY id")]
    def replace():
        with db.tx() as c:
            ts = db.preserve_todos(C1, db.summary(C1)["todos"], [mine("fresh", "2026-10-01")], c)
            db.put_summary({**db.summary(C1), "todos": ts, "prompt_version": 11}, c)
        return ts
    for _ in range(3):
        ts = replace()
        assert [t["text"] for t in ts] == ["fresh", "carried A", "carried B"]
        assert db.todo_ids(C1, ts)[1:] == ids
        assert [tuple(r) for r in db.conn().execute("SELECT * FROM todo_marks ORDER BY id")] == marks_before
        assert [t["text"] for t in db.due_todos("2026-10-26")] == ["fresh"]
        assert anyio.run(recap_get, "2026-10-01")["todos"] == []
        assert backfill.todos(anyio.run(conversation, C1)["summary"]) == (3, 2)
    anyio.run(todo_unmark, ids[0])
    assert {t["text"] for t in db.due_todos("2026-10-26")} == {"fresh", "carried A"}
    reopened = anyio.run(recap_get, "2026-10-01")["todos"]
    assert [(t["id"], t["mark"]) for t in reopened] == [(ids[0], None)]
    assert "carried A" in mcp_server.todos("2026-10-01", "2026-10-02", "我")
    for _ in range(2):
        ts = replace()
        assert [t["text"] for t in ts] == ["fresh", "carried B"] and db.todo(ids[0]) is None
        assert [t["text"] for t in db.due_todos("2026-10-26")] == ["fresh"]
        assert "carried A" not in mcp_server.todos("2026-10-01", "2026-10-02", "我")
        assert backfill.todos(anyio.run(conversation, C1)["summary"]) == (2, 1)
    orphan = anyio.run(recap_get, "2026-10-01")["todos"]
    assert [(t.get("id"), t["text"]) for t in orphan] == [(None, "carried A")], "genuinely removed cached text stays read-only until recap refresh"
