"""BG_GATE (sounds.under_music): a sentence with music under it (mn10_as's Music in the sounds table) teaches no
voiceprint: not the people bank (enroll), not a conversation's voice (voices_loop, and so not 我: learn_me), not a
clip of 「这是你吗」 (selfvoice.shortlist), not 我 picked on 「哪个是你」 (self_answer), not bank.relearn. Off, or no scores: as
before. Synthetic rows only, no models."""
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


def self_card() -> dict | None:
    """The 「哪个是你」 card of the voices there are, as self_round pops it (its fallback: a day after the first voice)."""
    db.put_kv("selfvoice", {"first_at": "2020-01-01T00:00:00"})
    anyio.run(routes_voices.self_round)
    return db.kv("selfvoice").get("card")


@pytest.mark.parametrize("built_on,score,popped", [(False, None, False), (True, 0.5, False), (True, None, True)])
def test_self_card_of_voices_shown_free(audio_store, gate, monkeypatch, built_on, score, popped):
    """The card's candidates: only voices shown free of music under them now (a voice made before the gate: not
    known), each with what its bank is made of for the answer to check again."""
    c, rows = cached(monkeypatch, [6.0, 6.0], built_on)
    if score:
        music(rows[0]["start"], 6, score)  # scored after the voice was made, before the card
    card = self_card()
    assert (card is not None) == popped
    if popped:
        assert card["candidates"][0]["members"] == [[c["id"], "S1"]]
        assert "members" not in anyio.run(routes_voices.self_get)["card"]["candidates"][0]


def test_self_answer_scores_after_card(audio_store, gate, monkeypatch):
    """Music scored after the card popped, before the pick: nothing learnt, said so; the card comes again without it."""
    c, rows = cached(monkeypatch, [6.0, 6.0], True)
    assert self_card()
    music(rows[0]["start"], 6, 0.5)
    assert anyio.run(routes_voices.self_answer, {"answer": 0}) == {"ok": True, "learned": 0, "reason": "background_music"}
    st = db.kv("selfvoice")
    assert not core.owner_npz().exists() and st["card"] is None and "confirmed" not in st and st["log"][-1]["learned"] == 0
    anyio.run(routes_voices.self_round)
    assert db.kv("selfvoice")["card"] is None, "not made again of the voice now under music"


@pytest.mark.parametrize("score", [None, 0.5])
def test_self_answer_card_from_before_gate(audio_store, gate, monkeypatch, score):
    """A card popped while the gate was off: what its bank is made of is not known, so nothing is learnt from it."""
    monkeypatch.setattr(sounds, "BG_GATE", False)
    c, rows = sample([6.0, 6.0])
    voices_round(monkeypatch)
    assert self_card()
    monkeypatch.setattr(sounds, "BG_GATE", True)
    if score:
        music(rows[0]["start"], 20, score, score)
    r = anyio.run(routes_voices.self_answer, {"answer": 0})
    assert not core.owner_npz().exists() and r == {"ok": True, "learned": 0, "reason": "background_music"}


def test_self_answer_drops_a_voice_scored_after_card(audio_store, gate, monkeypatch):
    """One candidate of two conversations' voices, music scored under one after the card popped: 我 is the other's
    voice alone, not the bank the card was made with."""
    clean, dirty = selfvoice.unit([1, 0.3, 0, 0]), selfvoice.unit([1, -0.3, 0, 0])  # 0.83 alike: one candidate
    (c1, _), (c2, rows2) = sample([6.0, 6.0], hour=10), sample([6.0, 6.0], hour=11)
    voices_round(monkeypatch)
    with db.tx() as t:
        t.executemany("UPDATE voices SET e = ? WHERE conv = ?", [(db.blob(clean), c1["id"]), (db.blob(dirty), c2["id"])])
    card = self_card()
    music(rows2[0]["start"], 6, 0.5)
    assert anyio.run(routes_voices.self_answer, {"answer": 0}) == {"ok": True}
    assert not np.allclose(routes_speakers.owner_bank()["self"], selfvoice.unit(card["candidates"][0]["bank"]), atol=1e-3)
    assert np.allclose(routes_speakers.owner_bank()["self"], clean, atol=1e-6)
    assert sorted(card["candidates"][0]["members"]) == [[c1["id"], "S1"], [c2["id"], "S1"]], "one candidate of both"


def test_self_answer_learns_voices_shown_free(audio_store, gate, monkeypatch):
    """The gate on, no music: the pick is 我's voiceprint, the card's bank made again from the same voices."""
    c, rows = cached(monkeypatch, [6.0, 6.0], True)
    card = self_card()
    assert anyio.run(routes_voices.self_answer, {"answer": 0}) == {"ok": True}
    assert np.allclose(routes_speakers.owner_bank()["self"], selfvoice.unit(card["candidates"][0]["bank"]))


def relearn_store(monkeypatch, built_on: bool, score: float | None) -> list:
    """Alex's NULL row (voiceprint B) and 5 live voices of Alex (A), made with the gate as built_on, then each
    conversation's first sentence scored score; the gate on after."""
    from alldayrec import bank
    monkeypatch.setattr(sounds, "BG_GATE", built_on)
    firsts = [sample([6.0, 6.0], hour=h)[1][0] for h in range(9, 14)]
    voices_round(monkeypatch)
    monkeypatch.setattr(sounds, "BG_GATE", True)
    with db.tx() as t:
        t.execute("UPDATE voices SET label = 'Alex'")
        t.execute("INSERT INTO enrolled VALUES ('Alex', NULL, NULL, 1, ?)", (db.blob(np.eye(4)[1]),))
        bank.recompute(t, "Alex")
    for r in firsts:
        if score:
            music(r["start"], 6, score)
    return firsts


@pytest.mark.parametrize("built_on,score,learnt", [(False, None, False), (True, 0.5, False), (True, 0.1, True), (True, None, True)])
def test_relearn(audio_store, gate, monkeypatch, built_on, score, learnt):
    """bank.relearn learns a name's NULL row again only from voices shown free of music under them; too few: kept."""
    from alldayrec import bank
    relearn_store(monkeypatch, built_on, score)
    with db.tx() as t:
        r, = bank.relearn(t)
    assert ("new_n" in r) == learnt and r.get("unverified", 0) == (0 if learnt else 5)
    assert np.allclose(bank.unit(db.people()["Alex"][1][0]), np.eye(4)[0] if learnt else np.eye(4)[1])


@pytest.mark.parametrize("gate_env,scored,kept", [("1", True, True), ("0", True, False), ("1", False, False)])
def test_relearn_cli(audio_store, monkeypatch, tmp_path, gate_env, scored, kept):
    """python -m alldayrec.bank relearn with the instance's BG_GATE: the tenant's sound read as scene scores in the
    store (none: sound off, as before); the dry run writes nothing, --apply what the dry run said. Off: as before."""
    import os, subprocess, sys
    T().sound = True
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    relearn_store(monkeypatch, scored, 0.5 if scored else None)
    env = {**os.environ, "BG_GATE": gate_env}
    run = lambda *a: subprocess.run([sys.executable, "-m", "alldayrec.bank", "relearn", str(tmp_path / "t.db"), *a],
                                    capture_output=True, text=True, check=True, env=env).stdout
    files = lambda: [p.read_bytes() for p in sorted(tmp_path.glob("t.db*")) if p.suffix != ".db-shm"]  # shm: readers mark it
    was = files()
    out = run()
    assert files() == was, "a dry run"
    assert out.splitlines()[0] == ("Alex: kept (n 1, 0 voices), 5 not shown free of music (BG_GATE)" if kept else
                                   "Alex: n 1 -> 10, voices 5 used / 0 dropped, cosine old/new 0.0")
    assert run("--apply").splitlines()[0] == out.splitlines()[0]
    assert np.allclose(selfvoice.unit(db.people()["Alex"][1][0]), np.eye(4)[1 if kept else 0])
