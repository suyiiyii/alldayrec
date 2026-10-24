"""Conversation grouping: a sentence nobody could be named for (S?) still opens a conversation, but stays out of
its participants and speech time."""
from alldayrec.conversations import split

T = "2026-10-05T10:00:"


def test_unnamed_first_sentence_opens_the_conversation():
    rows = [dict(start=T + "00.000", end=T + "05.000", speaker="S?", text="喂，在吗"),
            dict(start=T + "10.000", end=T + "30.000", speaker="我", text="在的，你说")]
    [c] = split(rows)
    assert [r["speaker"] for r in c["sentences"]] == ["S?", "我"] and c["start"] == T + "00.000", c
    assert c["participants"] == ["我"] and c["speech_s"] == 20, c


def test_only_unnamed_sentences_make_no_conversation():
    assert split([dict(start=T + "00.000", end=T + "30.000", speaker="S?", text="嗯")]) == []
