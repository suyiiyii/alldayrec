"""BG_GATE (sounds.under_music): a sentence with music under it (mn10_as's Music in the sounds table) teaches no
voiceprint: not the people bank (enroll), not a conversation's voice (voices_loop, and so not 我: learn_me), not a
clip of 「这是你吗」 (selfvoice.shortlist). Off, or no scores: as before. Synthetic rows only, no models."""
import datetime as dt

import anyio
import numpy as np
import pytest

from alldayrec import core, db, routes_speakers, routes_voices, selfvoice, sound, sounds
from alldayrec.tenant import T
from test_enrollment import SR, audio_store, sample, voices_round  # noqa: F401 (the fixture)

DEVICE = "sample-recorder"


def music(start: str, seconds: float, *scores: float, device: str = DEVICE):
    """A mn10_as row from start: one 10 s frame per score, Music that score (the other classes 0.01)."""
    n = round(seconds * SR)
    v = np.full((len(sound.windows("mn10_as", n)), sound.MODELS["mn10_as"]["classes"]), 0.01, np.float32)
    assert len(v) == len(scores)
    v[:, sounds.MUSIC] = scores
    a = round(dt.datetime.fromisoformat(start).timestamp() * 1000)
    with db.tx() as c:
        sounds.write(c, device, "mn10_as", sounds.row("mn10_as", a, np.zeros(n, np.int16), v))


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    monkeypatch.setattr(sounds, "BG_GATE", True)
    T().sound = True


def test_music_max(audio_store):
    t0 = "2026-11-16T10:00:00"
    music(t0, 30, 0.5, 0.1, 0.0)
    ms = lambda s: round(dt.datetime.fromisoformat(t0).timestamp() * 1000) + s * 1000
    assert sounds.music_max(DEVICE, ms(12), ms(15)) == pytest.approx(0.1, abs=1e-3)
    assert sounds.music_max(DEVICE, ms(5), ms(12)) == pytest.approx(0.5, abs=1e-3), "every frame over it, the highest"
    assert sounds.music_max(None, ms(5), ms(12)) == pytest.approx(0.5, abs=1e-3), "no device: any"
    assert sounds.music_max(DEVICE, ms(20), ms(25)) == 0.0, "Music under the top K: 0"
    assert sounds.music_max(DEVICE, ms(30), ms(40)) is None and sounds.music_max("other", ms(0), ms(30)) is None


def renamed(c: dict, to: str) -> dict:
    return anyio.run(routes_speakers.rename, c["id"], {"from": "S1", "to": to})


@pytest.mark.parametrize("score,learnt", [(0.5, False), (0.1, True), (None, True)])
def test_enroll(audio_store, gate, score, learnt):
    c, rows = sample([6.0, 6.0])
    if score is not None:
        music(rows[0]["start"], 20, score, score)
    renamed(c, "Alex")
    assert ("Alex" in db.people()) == learnt
    stats = T().scene_stats
    assert (stats["bg_skipped", "enroll"], stats["bg_unscored", "enroll"]) == {0.5: (2, 0), 0.1: (0, 0), None: (0, 2)}[score]
    if not learnt:
        assert T().events[-1]["text"] == "Alex：记住了 0 句的声纹，2 句有背景音乐没用上"


def test_enroll_note_counts_music_apart(audio_store, gate):
    c, rows = sample([6.0, 6.0, 6.0])
    music(rows[0]["start"], 6, 0.5)  # the first sentence only
    renamed(c, "Alex")
    assert "Alex" in db.people() and T().events[-1]["text"] == "Alex：记住了 2 句的声纹，1 句有背景音乐没用上"


def test_unscored_without_sound(audio_store, gate):
    """The tenant's sound off: nothing to go by, learnt as before, counted unscored."""
    T().sound = False
    c, rows = sample([6.0, 6.0])
    music(rows[0]["start"], 20, 0.5, 0.5)
    renamed(c, "Alex")
    assert "Alex" in db.people() and T().scene_stats["bg_unscored", "enroll"] == 2 and not T().scene_stats["bg_skipped", "enroll"]


@pytest.mark.parametrize("score", [0.5, 0.1, None])
def test_voices_pick(audio_store, gate, monkeypatch, score):
    c, rows = sample([8.0, 7.0, 6.0, 5.0, 4.0])
    if score is not None:
        music(rows[0]["start"], 8, score)  # the longest sentence only
    voices_round(monkeypatch)
    v, = db.voices()
    assert v["k"] == 4 and v["n"] == 5 and v["speech_s"] == 30.0, "n and speech_s count every sentence"
    assert len(audio_store) == 4 and (rows[0]["start"] not in [a for a, _, _ in audio_store]) == (score == 0.5)


@pytest.mark.parametrize("score,learned", [(0.5, False), (0.1, True), (None, True)])
def test_learn_me(audio_store, gate, monkeypatch, score, learned):
    """我 is learnt from the conversation's voice (voices_loop's): none when all its sentences have music under them."""
    c, rows = sample([6.0, 6.0])
    if score is not None:
        music(rows[0]["start"], 20, score, score)
    voices_round(monkeypatch)
    assert renamed(c, "我")["learned"] == learned and core.owner_npz().exists() == learned


def cached(monkeypatch, durations, built_on: bool):
    """A conversation whose voices row is made first: BG_GATE off (before it was turned on) or on (before any score
    came in); the gate on after."""
    monkeypatch.setattr(sounds, "BG_GATE", built_on)
    c, rows = sample(durations)
    voices_round(monkeypatch)
    used, = db.conn().execute("SELECT used FROM voices").fetchone()
    assert (used is not None) == built_on, "what it is made of: written only while the gate is on"
    monkeypatch.setattr(sounds, "BG_GATE", True)
    return c, rows


@pytest.mark.parametrize("built_on", [False, True])
@pytest.mark.parametrize("score,learned", [(0.5, False), (0.1, True)])
def test_learn_me_cached_voice(audio_store, gate, monkeypatch, built_on, score, learned):
    """The voices row made before the scores or the gate: 我 learns from it only once shown free of music under it."""
    c, rows = cached(monkeypatch, [6.0, 6.0], built_on)
    music(rows[0]["start"], 20, score, score)
    assert renamed(c, "我")["learned"] == learned and core.owner_npz().exists() == learned


@pytest.mark.parametrize("built_on", [False, True])
def test_voice_of_cached_voice(audio_store, gate, monkeypatch, built_on):
    """voice_of holds back a cached voice not shown free of music; 我's is made again without the sentence under it."""
    c, rows = cached(monkeypatch, [6.0, 6.0], built_on)
    assert (routes_speakers.voice_of(c["id"], "S1") is not None) == built_on, "made before the gate: not known"
    music(rows[0]["start"], 6, 0.5)  # the first sentence only, scored after the voice was made
    assert routes_speakers.voice_of(c["id"], "S1") is None
    n = len(audio_store)
    e, speech = anyio.run(routes_speakers.voice_of_me, c["id"], "S1")
    assert speech == 12.0 and [a for a, _, _ in audio_store[n:]] == [rows[1]["start"]], "made again from the second only"
    assert anyio.run(routes_speakers.voice_of_me, c["id"], "S2") is None, "no voice at all: none made"


@pytest.mark.parametrize("built_on,score", [(False, 0.1), (True, 0.1), (False, 0.5), (True, 0.5)])
def test_late_cached_voice(audio_store, gate, monkeypatch, built_on, score):
    """late.conversation_vectors takes a cached voice only once shown free of music; else makes it again, gated."""
    from alldayrec import late
    c, rows = cached(monkeypatch, [6.0, 6.0], built_on)
    music(rows[1]["start"], 6, score)
    read = []
    monkeypatch.setattr(late, "read_exact", lambda device, a, b: read.append(a) or np.zeros(b - a, np.int16))
    ms = lambda t: round(dt.datetime.fromisoformat(t).timestamp() * 1000)
    span = {"device": DEVICE, "start_ms": ms(rows[0]["start"]), "end_ms": ms(rows[1]["end"])}
    assert set(anyio.run(late.conversation_vectors, span)[c["id"]]) == {"S1"}
    assert read == {(True, 0.1): [], (False, 0.1): [ms(rows[1]["start"]), ms(rows[0]["start"])]}.get((built_on, score), [ms(rows[0]["start"])])


def test_merged_voice_not_known(audio_store, gate, monkeypatch):
    """A voice merged into (late_merge.put_voice, as late, split and unsplit do): its used gone, so made again."""
    from alldayrec import late_merge
    c, rows = cached(monkeypatch, [6.0, 6.0], True)
    assert routes_speakers.voice_of(c["id"], "S1") is not None
    with db.tx() as t:
        late_merge.put_voice(t, {"conv": c["id"], "label": "S1", "e": db.blob(np.ones(4, np.float32))})
    assert db.conn().execute("SELECT used FROM voices").fetchone()[0] is None and routes_speakers.voice_of(c["id"], "S1") is None


def test_shortlist(audio_store, gate):
    c, rows = sample([3.0, 3.0])
    music(rows[0]["start"], 3, 0.5)
    assert [r["id"] for r in selfvoice.shortlist(rows, "S1")] == [rows[1]["id"]]


def test_late_conversation_vectors(audio_store, gate, monkeypatch):
    """A closed conversation's voice for the late pass (no voices row yet): not from a sentence with music under it."""
    from alldayrec import late
    c, rows = sample([6.0, 6.0])
    music(rows[1]["start"], 6, 0.5)
    read = []
    monkeypatch.setattr(late, "read_exact", lambda device, a, b: read.append(a) or np.zeros(b - a, np.int16))
    span = {"device": DEVICE, "start_ms": round(dt.datetime.fromisoformat(rows[0]["start"]).timestamp() * 1000),
            "end_ms": round(dt.datetime.fromisoformat(rows[1]["end"]).timestamp() * 1000)}
    assert set(anyio.run(late.conversation_vectors, span)[c["id"]]) == {"S1"}
    assert read == [round(dt.datetime.fromisoformat(rows[0]["start"]).timestamp() * 1000)], "the second, under music: not read"


def test_voice_check_yes_all_under_music(audio_store, gate, monkeypatch):
    """「是」 on a c card whose sentences all have music under them: ok, but learned 0 and why; nothing learnt."""
    c, rows = sample([6.0, 6.0])
    with db.tx() as t:
        t.execute("UPDATE sentences SET speaker = 'P' WHERE speaker = 'S1'")
    music(rows[0]["start"], 20, 0.5, 0.5)
    db.put_kv("voicecheck", {"card": {"kind": "c", "conv": c["id"], "label": "P", "name": "P", "score": 0.2,
                                      "at": dt.datetime.now().isoformat(timespec="seconds")}})
    assert anyio.run(routes_voices.check_answer, {"answer": "yes"}) == {"ok": True, "learned": 0, "reason": "background_music"}
    assert "P" not in db.people() and db.kv("voicecheck")["log"][-1]["answer"] == "yes"


def test_off_is_as_before(audio_store, monkeypatch):
    """BG_GATE=0 (the default; anything but 1): music under every sentence changes nothing and counts nothing."""
    import importlib, os
    for v in ("", "0", "true", "1"):
        monkeypatch.setenv("BG_GATE", v)
        assert importlib.reload(sounds).BG_GATE is (v == "1")
    monkeypatch.delenv("BG_GATE")
    assert importlib.reload(sounds).BG_GATE is False
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    T().sound = True
    c, rows = sample([8.0, 7.0, 6.0, 5.0, 4.0])
    music(rows[0]["start"], 31, 0.9, 0.9, 0.9)
    voices_round(monkeypatch)
    v, = db.voices()
    assert v["k"] == 4 and rows[0]["start"] in [a for a, _, _ in audio_store]
    assert db.conn().execute("SELECT used FROM voices").fetchone()[0] is None
    assert renamed(c, "Alex") and "Alex" in db.people()
    assert selfvoice.shortlist(rows, "S1", per=5) and not any(k.startswith("bg_") for k, _ in T().scene_stats)
