"""summary.py's self-check: a model reply's times resolved to rows (prompt v9: each person's point too), due dates, media,
an odd reply's fields dropped; and one odd reply does not break the tags list that every later summary asks first."""
import runpy
from pathlib import Path

from alldayrec import core
from alldayrec import db
from alldayrec import summary


def test_selfcheck():
    runpy.run_path(str(Path(__file__).with_name("alldayrec") / "summary.py"), run_name="__main__")


def test_odd_reply_keeps_tags(tmp_path):
    """A dict as a tag is not stored: tag_counts (the tags list, and the known tags every summary starts with) stays up."""
    db.open_db(tmp_path / "t.db")
    c = {"id": "c20261026100000", "start": "2026-10-26T10:00:00.000", "end": "2026-10-26T10:10:00.000", "participants": ["我"],
         "sentences": 1, "speech_s": 600.0, "device": "mic"}
    rows = [dict(id="1", start=c["start"], end="2026-10-26T10:00:05.000", speaker="我", text="x", source="mic")]
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c)
    db.put_summary(summary.result(c, rows, {"title": "t", "summary": "s", "tags": [{"name": "工作"}, "工作"]}, {"model": "m"}))
    assert core.tag_counts() == {"工作": 1}


def test_odd_todo_keeps_due_todos(tmp_path):
    """A todo with a list as its text, or a number as its due, does not break the due todos (/api/todos/due); the
    others in the reply stay."""
    db.open_db(tmp_path / "t.db")
    c = {"id": "c20261026100000", "start": "2026-10-26T10:00:00.000", "end": "2026-10-26T10:10:00.000", "participants": ["我"],
         "sentences": 1, "speech_s": 600.0, "device": "mic"}
    rows = [dict(id="1", start=c["start"], end="2026-10-26T10:00:05.000", speaker="我", text="x", source="mic")]
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c)
    todo = {"owner": "我", "due": "明天", "due_date": "2026-10-27", "at": "09:00:00"}  # at: no row of the conversation
    db.put_summary(summary.result(c, rows, {"title": "t", "summary": "s", "todos": [
        {**todo, "text": ["买菜"]}, {**todo, "text": "交报告", "due": 1}, {**todo, "text": "买票"}]}, {"model": "m"}))
    assert [t["text"] for t in db.due_todos("2026-10-27")] == ["买票"]
