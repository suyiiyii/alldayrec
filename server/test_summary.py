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
