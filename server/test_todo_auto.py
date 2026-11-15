"""#347 todos that resolve themselves (todo_auto.py): a quote closes a todo (done / dropped), a guess only hints, a
row the transcript does not have is dropped, a mark by hand always wins, the undo holds until a later quote, todos past
due with no mention go stale, the same todo said twice is one, and the done hints of summaries before are carried over."""
import anyio
import pytest

from alldayrec import db
from alldayrec import summaries
from alldayrec import summary
from alldayrec import todo_auto
from alldayrec.routes_api import recap_get, todo_auto_undo, todo_mark, todos_auto

A, B, C = "c20261101100000", "c20261103100000", "c20261105100000"  # the todos' conversation, two later ones
SAID = {B: [("10:00:00", "S1", "报告发老王了吗"), ("10:00:01", "我", "发了。"), ("10:00:10", "S1", "那个合同还看吗"), ("10:00:11", "我", "不用看了，取消了"),
            ("10:00:20", "S1", "快递到了"), ("10:00:30", "我", "下周再改代码")],
        C: [("10:00:00", "S1", "报告呢"), ("10:00:01", "我", "刚又发了一遍")]}


def conv(cid):
    start = f"{cid[1:5]}-{cid[5:7]}-{cid[7:9]}T10:00:00.000"
    return {"id": cid, "start": start, "end": start[:11] + "10:30:00.000", "participants": ["我", "S1"], "sentences": 1, "speech_s": 600.0, "device": "mic"}


def rows(cid):
    d = conv(cid)["start"][:11]
    return [dict(id=f"{cid}-{n}", start=f"{d}{t}.000", end=f"{d}{t}.900", speaker=who, text=x, source="mic") for n, (t, who, x) in enumerate(SAID.get(cid, []))]


def todo(text, at, **kw):
    return {"owner": "我", "text": text, "at": f"2026-11-01T{at}.000", **kw}


TODOS = [todo("发报告", "10:00:01", to="老王", due="明天", due_date="2026-11-02"), todo("看合同", "10:00:02"), todo("改代码", "10:00:03"),
         todo("买咖啡", "10:00:04", due="明天", due_date="2026-11-02")]


@pytest.fixture
def store(tmp_path):
    db.open_db(tmp_path / "t.db")
    with db.tx() as t:
        for cid, todos in ((A, TODOS), (B, [todo("给老王发报告", "10:00:30", to="老王")]), (C, [])):
            db.insert_conversation(t, conv(cid))
            db.insert_sentences(t, rows(cid))
            db.put_summary({"conversation": cid, "status": "done", "prompt_version": 18, "title": "t" + cid[-6:], "todos": todos}, t)
    return dict(zip(("report", "contract", "code", "coffee"), db.todo_ids(A, TODOS))) | {"again": db.todo_ids(B, db.summary(B)["todos"])[0]}


def reply(monkeypatch, res):
    seen = []
    async def complete(messages, **kw):
        seen.append(messages)
        return res, {"model": "m1"}
    monkeypatch.setattr(summary, "complete", complete)
    return seen


def run(cid):
    anyio.run(summaries.resolve_todos, {**db.conversation(cid)})


def test_quote_closes_guess_hints_made_up_dropped(store, monkeypatch):
    seen = reply(monkeypatch, {"todos": [
        {"n": 1, "kind": "done", "s": "s2", "reason": "我说报告发了"},        # 发了。 alone: the question before names the report
        {"n": 2, "kind": "dropped", "s": "s4", "reason": "说合同不用看了"},
        {"n": 4, "kind": "done", "s": "s5", "reason": "快递到了，咖啡大概买了"},  # nothing of coffee there: only a hint
        {"n": 3, "kind": "done", "s": "s99", "reason": "编的"}, {"n": 3, "kind": "done", "s": "x"}, {"n": 3, "kind": "dropped"},  # no such row
        {"n": 9, "kind": "done", "s": "s2"}, {"n": 3, "kind": "maybe", "s": "s6"}, "x"]})
    run(B)
    user = seen[0][1]["content"]
    assert "#1 我 → 老王：发报告｜截止 明天｜11-01 10:00" in user and "#5 我 → 老王：给老王发报告" in user and "[s2 10:00:01] 我: 发了。" in user
    auto = db.auto_marks()
    assert {k: auto[i]["kind"] for k, i in store.items() if i in auto} == {"report": "done", "contract": "dropped", "coffee": "hint"}
    r = auto[store["report"]]
    assert (r["reason"], r["src"], r["sentence"], r["at_said"], r["speaker"], r["quote"], r["model"]) == (
        "我说报告发了", B, f"{B}-1", "2026-11-03T10:00:01.000", "我", "发了。", "m1"), "the row's own text is the quote, not the model's"
    due = {t["text"]: t for t in db.due_todos("2026-11-05")}
    assert list(due) == ["买咖啡"] and due["买咖啡"]["done_hint"]["quote"] == "快递到了", "closed ones leave the due list, a hint stays"
    listed = anyio.run(todos_auto, 14)
    assert sorted((t["text"], t["auto"]["kind"], t["auto"]["conv"], t["auto"]["at"]) for t in listed) == [
        ("发报告", "done", B, "2026-11-03T10:00:01.000"), ("看合同", "dropped", B, "2026-11-03T10:00:11.000")], "a hint is not listed: it closes nothing"
    assert listed[0]["owner"] == "我" and listed[0]["auto"]["judged_at"] and listed[0]["auto"]["model"] == "m1"


def test_mark_by_hand_wins(store, monkeypatch):
    anyio.run(todo_mark, store["report"], {"mark": "wrong"})
    seen = reply(monkeypatch, {"todos": [{"n": 1, "kind": "done", "s": "s2", "reason": "r"}]})
    run(B)
    assert "：发报告｜" not in seen[0][1]["content"], "a marked todo is no candidate"
    assert db.add_auto([{"todo": store["report"], "kind": "done", "src": B, "sentence": "x", "at_said": "2026-11-03T10:00:01.000"}]) == []
    assert db.shut_todos()[store["report"]] == "wrong" and store["report"] not in db.auto_marks()
    db.mark_todos([store["report"]], None)
    db.add_auto([{"todo": store["contract"], "kind": "dropped", "src": B, "sentence": "y", "at_said": "2026-11-03T10:00:11.000"}])
    anyio.run(todo_mark, store["contract"], {"mark": "done"})
    assert db.shut_todos()[store["contract"]] == "done" and store["contract"] not in db.auto_marks(), "a later mark by hand beats an automatic one"


def test_undo_holds_until_a_later_quote(store, monkeypatch):
    reply(monkeypatch, {"todos": [{"n": 1, "kind": "done", "s": "s2", "reason": "r"}]})
    run(B)
    assert store["report"] in db.shut_todos()
    assert anyio.run(todo_auto_undo, store["report"]) == {"id": store["report"], "undone": 1}
    assert store["report"] not in db.shut_todos() and [t["text"] for t in db.due_todos("2026-11-05")][0] == "发报告"
    run(B)  # the same conversation again (a summary redone): the same quote
    assert store["report"] not in db.shut_todos(), "not closed again on the sentence undone"
    assert db.conn().execute("SELECT count(*) FROM todo_auto WHERE todo = ?", (store["report"],)).fetchone()[0] == 1, "the undone row is kept"
    reply(monkeypatch, {"todos": [{"n": 2, "kind": "done", "s": "s2", "reason": "又发了一遍"}]})  # #1 is B's, the newest
    run(C)
    assert db.shut_todos()[store["report"]] == "auto_done" and db.auto_marks()[store["report"]]["src"] == C, "a later quote closes it again"


def test_stale(store, monkeypatch):
    assert todo_auto.age_out("2026-11-09") == [], "7 days past due: not yet"
    took = todo_auto.age_out("2026-11-10")
    assert {r["todo"] for r in took} == {store["report"], store["coffee"]}
    r = db.auto_marks()[store["coffee"]]
    assert (r["kind"], r["src"], r["at_said"]) == ("stale", A, "2026-11-01T10:00:04.000") and "2026-11-02" in r["reason"], "due, last said, where"
    assert todo_auto.age_out("2026-11-11") == [], "once"
    db.undo_auto(store["coffee"])
    assert todo_auto.age_out("2026-11-12") == [] and store["coffee"] not in db.shut_todos(), "undone: never stale again"
    # a mention after the due starts the 7 days again
    db.add_auto([{"todo": store["report"], "kind": "seen", "src": C, "sentence": f"{C}-0", "at_said": "2026-11-05T10:00:00.000"}])
    db.undo_auto(store["report"])
    db.conn().execute("DELETE FROM todo_auto WHERE kind = 'stale' AND todo = ?", (store["report"],))  # as if never aged out
    assert todo_auto.age_out("2026-11-12") == [] and [r["todo"] for r in todo_auto.age_out("2026-11-13")] == [store["report"]]
    assert db.auto_marks()[store["report"]]["src"] == C, "the last mention is the evidence"


def test_same_merged(store, monkeypatch):
    reply(monkeypatch, {"todos": [], "same": [[1, 5], [2, 4], [3, 3], "x", 7]})
    run(B)
    same = db.same_todos()
    assert same == {store["again"]: store["report"]}, "看合同 and 买咖啡 share no word: not merged on the model's say alone"
    assert db.conn().execute("SELECT model FROM todo_same").fetchone()[0] == "m1"
    db.merge_same([store["again"], store["code"]])
    assert db.group_of(store["code"]) == {store["report"], store["again"], store["code"]}, "groups join"
    # one due todo in the list for the group, the newest said, with every source
    with db.tx() as t:
        s = db.summary(B)
        s["todos"][0] |= {"due": "明天", "due_date": "2026-11-04"}
        db.put_summary(s, t)
    due = [t for t in db.due_todos("2026-11-05") if t["text"] in ("发报告", "给老王发报告")]
    assert [t["text"] for t in due] == ["给老王发报告"] and [x["conv"] for x in due[0]["sources"]] == [A, A, B]
    db.add_auto([{"todo": store["again"], "kind": "done", "src": C, "sentence": f"{C}-1", "at_said": "2026-11-05T10:00:01.000"}])
    assert {store["report"], store["code"], store["again"]} <= db.shut_todos().keys(), "one closed: the group closed"
    assert len([t for t in db.auto_todos("0") if t["id"] in db.group_of(store["report"])]) == 1
    db.undo_auto(store["report"])
    assert store["again"] not in db.shut_todos(), "the undo is the group's"


def test_other_owners_not_merged(store, monkeypatch):
    with db.tx() as t:
        s = db.summary(B)
        s["todos"].append({"owner": "S1", "text": "发报告", "at": "2026-11-03T10:00:00.000"})
        db.put_summary(s, t)
    reply(monkeypatch, {"todos": [], "same": [[1, 6]]})
    run(B)
    assert db.same_todos() == {}, "someone else doing it is another todo"


def test_no_call_without_open_todos(store, monkeypatch):
    db.mark_todos(list(store.values()), "done")
    seen = reply(monkeypatch, {})
    run(B)
    assert seen == []


def test_failure_and_bad_reply_keep_nothing(store, monkeypatch):
    async def boom(messages, **kw):
        raise TimeoutError
    monkeypatch.setattr(summary, "complete", boom)
    run(B)
    for res in (["not", "an", "object"], {"todos": 3, "same": 5}, {"todos": [{"n": 1, "kind": "done", "s": 2}], "same": [[1, "x", None]]}):
        reply(monkeypatch, res)
        run(B)  # the worker goes on
    assert [tuple(r) for r in db.conn().execute("SELECT kind FROM todo_auto")] == [("done",)], "only the last, which checks out (s as a number)"


def test_recap_counts(store, monkeypatch):
    import datetime as dt
    today = dt.date.today()
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    db.add_auto([{"todo": store["report"], "kind": "done", "src": B, "sentence": f"{B}-1", "at_said": "2026-11-03T10:00:01.000",
                  "at": f"{yesterday}T09:00:00"}])
    with db.tx() as t:
        s = db.summary(A)
        s["todos"][3]["due_date"] = today.isoformat()
        db.put_summary(s, t)
    for d in (yesterday, "2026-11-01"):
        db.put_kv(f"recap:{d}", {"date": d, "todos": [], "highlights": []})
    assert anyio.run(recap_get, yesterday)["auto"] == {"closed": 1, "due": 1}, "closed that day, mine due today"
    assert anyio.run(recap_get, "2026-11-01")["auto"] == {"closed": 0, "due": 0}, "an older recap: no due count"


@pytest.mark.parametrize("cut_off", [False, True])
def test_carries_over_old_done_hints(tmp_path, monkeypatch, cut_off):
    """Summaries of prompt v15-v17 kept done_hints in their bodies: they become hint rows, once each, an undone one too.
    cut_off: a start that stopped after the tables were made, before the rows were copied, copies them the next time."""
    db.open_db(tmp_path / "t.db")
    with db.tx() as t:
        db.insert_conversation(t, conv(A))
        db.insert_conversation(t, conv(B))
        db.insert_sentences(t, rows(B))
        db.put_summary({"conversation": A, "status": "done", "prompt_version": 17, "todos": TODOS[:1]}, t)
        report = db.todo_ids(A, TODOS[:1])[0]
        db.put_summary({"conversation": B, "status": "done", "prompt_version": 17, "model": "old", "created_at": "2026-11-03T11:00:00",
                        "done_hints": [{"id": report, "quote": "发了。", "at": "2026-11-03T10:00:01.000"}]}, t)
    db.conn().execute("DROP TABLE todo_auto")
    if cut_off:
        with monkeypatch.context() as m:
            m.setattr(db, "resolve_todo", lambda i: (_ for _ in ()).throw(KeyboardInterrupt))  # stopped inside the copy
            with pytest.raises(KeyboardInterrupt):
                db.init()
        assert db.conn().execute("SELECT count(*) FROM todo_auto").fetchone()[0] == 0
    db.init()
    assert db.done_hints()[report] == {"kind": "hint", "reason": "", "conv": B, "at": "2026-11-03T10:00:01.000", "sentence": f"{B}-1",
                                           "speaker": "我", "quote": "发了。", "model": "old", "judged_at": "2026-11-03T11:00:00"}
    assert db.summary(B)["done_hints"], "the summary keeps its own"
    db.init()
    assert db.conn().execute("SELECT count(*) FROM todo_auto").fetchone()[0] == 1, "once"
    db.undo_auto(report)
    db.init()
    assert db.conn().execute("SELECT count(*) FROM todo_auto").fetchone()[0] == 1 and report not in db.done_hints(), "an undo holds"


def test_redone_summary_keeps_auto_closed_todo(store, monkeypatch):
    """A summary written again that leaves out (or rewords) a todo closed automatically keeps it: its evidence stays
    shown and its undo works; the same for a todo of a group."""
    db.add_auto([{"todo": store["report"], "kind": "done", "src": B, "sentence": f"{B}-1", "at_said": "2026-11-03T10:00:01.000"}])
    db.merge_same([store["contract"], store["coffee"]])
    with db.tx() as t:
        s = db.summary(A)
        s["todos"] = db.preserve_todos(A, s["todos"], [todo("改代码", "10:00:03")], t)
        db.put_summary(s, t)
    assert {store["report"], store["contract"], store["coffee"]} <= set(db.todo_ids(A, db.summary(A)["todos"]))
    assert [t["text"] for t in anyio.run(todos_auto, 14)] == ["发报告"]
    assert anyio.run(todo_auto_undo, store["report"])["undone"] == 1


def test_same_needs_one_task(store, monkeypatch):
    """Merged only when every two are one task: the same owner and recipient, a word shared by each pair (no chains),
    and an anonymous label only within one conversation."""
    with db.tx() as t:
        s = db.summary(B)
        s["todos"] = [todo("发报告", "10:00:30", to="客户乙", due_date="2026-11-20"), todo("咖啡买好", "10:00:31"), todo("合同审阅", "10:00:32"),
                      {"owner": "S1", "text": "订会议室", "at": "2026-11-03T10:00:33.000"}]
        db.put_summary(s, t)
        s = db.summary(A)
        s["todos"].append({"owner": "S1", "text": "订会议室", "at": "2026-11-01T10:00:05.000"})
        db.put_summary(s, t)
    # numbers: 1-5 A's open todos (发报告 → 老王, 看合同, 改代码, 买咖啡, S1 订会议室), 6-9 B's
    reply(monkeypatch, {"todos": [], "same": [[1, 6], [2, 4, 7, 8], [5, 9]]})
    run(B)
    assert db.same_todos() == {}, "another recipient; coffee and contract chained; S1 of two conversations"
    reply(monkeypatch, {"todos": [], "same": [[4, 7], [2, 8]]})
    run(B)
    assert len(db.same_todos()) == 2
    reply(monkeypatch, {"todos": [], "same": [[2, 4]]})  # the two groups: 看合同 and 买咖啡 share nothing
    run(B)
    assert len(db.same_todos()) == 2


def test_put_off_due_counts(store, monkeypatch):
    """The same todo said again with a later due: the new due counts, in the due list and for stale."""
    with db.tx() as t:
        s = db.summary(B)
        s["todos"][0] |= {"due": "20 号", "due_date": "2026-11-20"}
        db.put_summary(s, t)
    db.merge_same([store["report"], store["again"]])
    assert [t["text"] for t in db.due_todos("2026-11-10")] == ["买咖啡"], "not due yet: put off to 11-20"
    assert [r["todo"] for r in todo_auto.age_out("2026-11-11")] == [store["coffee"]]
    assert store["report"] not in db.shut_todos()
    assert [(t["text"], t["due_date"], len(t["sources"])) for t in db.due_todos("2026-11-21")] == [("给老王发报告", "2026-11-20", 2)]


@pytest.mark.parametrize("said,kind", [("报告应该已经发了吧，我也不确定", "hint"), ("报告发了吗？", "hint"), ("报告还没发", "hint"),
                                       ("报告昨天发给老王了", "done")])
def test_hedged_quote_only_hints(said, kind):
    """A done the model gives on a sentence that hedges, asks, denies or plans is only a hint."""
    rows = [dict(id="r0", start="2026-11-03T10:00:00.000", speaker="我", text=said)]
    cands = [({"id": A}, "t1", {"owner": "我", "text": "发报告", "to": "老王"})]
    found, _ = todo_auto.judged({"id": B}, rows, cands, [], {"todos": [{"n": 1, "kind": "done", "s": "s1", "reason": "r"}]}, "m")
    assert [x["kind"] for x in found] == [kind]
