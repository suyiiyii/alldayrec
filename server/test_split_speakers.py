"""#419: an engine sentence two people said is cut where the voice changes (A), and a sentence ends at its last word (E).
Fake words and fake voiceprints: a sample's sign says who spoke it, a window's voiceprint is the mix it holds."""
import numpy as np

from alldayrec import qwen_asr, transcript
from alldayrec.segmenter import SR
from alldayrec.transcript import final_rows, label_sentences

A, B = np.array([1.0, 0.0]), np.array([0.0, 1.0])


def embed(y):
    v = (y > 0).mean() * A + (y < 0).mean() * B
    return v / np.linalg.norm(v)


class Dia:
    """Labels by the voice a voiceprint is closer to."""
    def __init__(self, same=False):
        self.n, self.same = 0, same

    def add(self, e, dur):
        self.n += 1
        return ("S1" if self.same or e[0] >= e[1] else "S2"), 0.9

    def mark(self):
        return (self.n,)


def audio(*turns):
    """turns: (who, seconds); the audio, and a word every 0.5 s with its text."""
    x, words, t = [], [], 0
    for who, s in turns:
        x.append(np.full(int(s * SR), 1000 if who is A else -1000, np.int16))
        words += [{"start_ms": int((t + k * 0.5) * 1000), "end_ms": int((t + k * 0.5 + 0.4) * 1000), "text": "甲" if who is A else "乙"}
                  for k in range(int(s / 0.5))]
        t += s
    words[-1]["text"] += "。"
    return np.concatenate(x), {"start_ms": 0, "end_ms": int(t * 1000), "text": "".join(w["text"] for w in words), "speaker": "SPEAKER_0", "words": words}


def test_two_people_cut():
    x, g = audio((A, 3), (B, 3))
    segs = [g]
    marks = label_sentences(Dia(), embed, segs, x)
    assert [(s["speaker"], s["text"], s["split"]) for s in segs] == [("S1", "甲" * 6, 1), ("S2", "乙" * 6 + "。", 2)], segs
    assert segs[0]["end_ms"] <= 3000 <= segs[1]["start_ms"] and len(marks) == 2
    assert all(s["engine_speaker"] == "SPEAKER_0" for s in segs)
    _, _, rows = final_rows(segs, {s["speaker"]: s["speaker"] for s in segs}, __import__("datetime").datetime(2026, 11, 1, 9), 10.0, since=0.0, edge=0.0)
    assert [r["speaker"] for r in rows] == ["S1", "S2"], rows


def test_back_and_forth_cut_each_turn():
    x, g = audio((A, 3), (B, 3), (A, 3))
    segs = [g]
    label_sentences(Dia(), embed, segs, x)
    assert [(s["speaker"], s["split"]) for s in segs] == [("S1", 1), ("S2", 2), ("S1", 3)], segs
    assert "".join(s["text"] for s in segs) == g["text"]


def test_one_person_not_cut():
    x, g = audio((A, 9))
    segs = [g]
    label_sentences(Dia(), embed, segs, x)
    assert len(segs) == 1 and "split" not in segs[0] and segs[0]["text"] == g["text"]


def test_parts_the_diarist_hears_as_one_rejoin():
    x, g = audio((A, 3), (B, 3))
    segs = [g]
    marks = label_sentences(Dia(same=True), embed, segs, x)
    assert len(segs) == 1 and "split" not in segs[0] and segs[0]["text"] == g["text"] and len(marks) == 1, segs


def test_off_and_without_words(monkeypatch):
    x, g = audio((A, 3), (B, 3))
    segs = [{k: v for k, v in g.items() if k != "words"}]
    label_sentences(Dia(), embed, segs, x)
    assert len(segs) == 1
    monkeypatch.setattr(transcript, "SPLIT_COS", 0.0)
    segs = [g]
    label_sentences(Dia(), embed, segs, x)
    assert len(segs) == 1


def test_sentence_ends_at_its_last_word():
    tr = [{"sentences": [
        {"begin_time": 0, "end_time": 9000, "text": "你好吗？", "speaker_id": 0, "words": [
            {"begin_time": 100, "end_time": 400, "text": "你好", "punctuation": ""}, {"begin_time": 400, "end_time": 700, "text": "吗", "punctuation": "？"}]},
        {"begin_time": 9500, "end_time": 9900, "text": "嗯。", "speaker_id": 1},
        {"begin_time": 9900, "end_time": 9950, "text": " "}]}]
    got = qwen_asr.sentences(tr)
    assert [(s["start_ms"], s["end_ms"], s["speaker"]) for s in got] == [(0, 700, "SPEAKER_0"), (9500, 9900, "SPEAKER_1")], got
    assert "".join(w["text"] for w in got[0]["words"]) == "你好吗？" and "words" not in got[1]
