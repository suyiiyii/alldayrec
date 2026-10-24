"""Person profiles: the evidence numbers the model sees point back at conversations, todos and mentions; a line citing
nothing given is dropped; mentions find the name and its aliases said by others, a few per conversation."""
import json
import tempfile
from pathlib import Path

import anyio

from alldayrec import db
from alldayrec import profiles
from alldayrec import summary
from alldayrec.routes_speakers import alias_set, make_profile, mentions, person

T = "2026-10-05T10:00:"


def test_profile(monkeypatch):
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    rows = [dict(id="1", start=T + "01.000", end=T + "02.000", speaker="张三", text="我下周去上海出差", source="mic"),
            dict(id="2", start=T + "03.000", end=T + "04.000", speaker="S1", text="老张上次说要换工作", source="mic"),
            dict(id="3", start=T + "05.000", end=T + "06.000", speaker="我", text="张三人呢", source="mic"),
            dict(id="4", start=T + "07.000", end=T + "08.000", speaker="张三", text="张三在这", source="mic")]  # his own: no mention
    rows += [dict(id=f"x{i}", start=f"2026-10-05T11:00:{i:02d}.000", end=f"2026-10-05T11:00:{i:02d}.500", speaker="S2",
                  text="张三张三", source="mic") for i in range(5)]  # one talk saying it over and over
    c1 = {"id": "c1", "start": T + "00.000", "end": T + "09.000", "participants": ["张三", "S1", "我"], "sentences": 4,
          "speech_s": 70.0, "device": "mic"}
    c2 = {"id": "c2", "start": "2026-10-05T11:00:00.000", "end": "2026-10-05T11:00:05.000", "participants": ["S2"],
          "sentences": 5, "speech_s": 5.0, "device": "mic"}
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c1)
        db.insert_conversation(t, c2)
        db.put_summary({"conversation": "c1", "status": "done", "title": "出差", "summary": "张三说要去上海",
                        "people": [{"who": "张三", "points": ["下周去上海出差"]}],
                        "todos": [{"owner": "我", "to": "张三", "text": "订票", "at": T + "05.000"}]}, t)
    anyio.run(alias_set, {"alias": "老张", "name": "张三"})

    ms = mentions("张三")
    assert [(m["conv"], m["at"][11:19]) for m in ms] == [("c2", "11:00:04"), ("c2", "11:00:03"), ("c2", "11:00:02"),
                                                         ("c1", "10:00:05"), ("c1", "10:00:03")], "3 per talk; his own row is none"
    assert [r["id"] for r in ms[-1]["rows"]] == ["1", "2", "3", "4"]

    asked = []

    async def complete(messages, kind=None):
        asked.append(messages[1]["content"])
        return {"who": "同事", "facts": [{"text": "要去上海出差", "src": ["E1", "E99"]}, {"text": "编的", "src": ["E99"]}],
                "recent": [], "mentions": [{"text": "S1 说他要换工作", "src": ["E7"]}], "open": [{"text": "我要订票", "src": ["E2"]}]}, {"model": "m"}
    monkeypatch.setattr(summary, "complete", complete)
    pf = anyio.run(make_profile, "张三")
    text = asked[0]
    assert "[E1] 对话" in text and "下周去上海出差" in text and "[E2] 待办 2026-10-05：我答应他：订票" in text
    assert "  >> S1: 老张上次说要换工作" in text, text
    assert [x["text"] for x in pf["facts"]] == ["要去上海出差"] and [s["id"] for s in pf["facts"][0]["src"]] == ["E1"]
    assert pf["mentions"][0]["src"][0]["conv"] == "c1" and pf["open"][0]["src"][0]["at"] == T + "05.000"
    assert pf["upto"] == c1["start"] and pf["inputs"] == {"conversations": 1, "todos": 1, "mentions": 5}
    p = anyio.run(person, "张三")
    assert p["profile"]["who"] == "同事" and p["profile_newer"] == 0
    assert json.loads(db.conn().execute("SELECT body FROM profiles").fetchone()[0])["name"] == "张三"


def test_check_drops_unsourced():
    ev = {"E1": {"conv": "c1", "at": None, "start": T, "title": "x"}}
    got = profiles.check({"who": None, "facts": [{"text": "a", "src": ["E1", "E1"]}, {"text": "b"}, "x"], "open": None}, ev)
    assert got == {"who": "", "facts": [{"text": "a", "src": [{"id": "E1", **ev["E1"]}]}], "recent": [], "mentions": [], "open": []}
