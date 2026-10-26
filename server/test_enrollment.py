"""Enrollment uses speech duration, independent of ASR line merging and summary media classification.

Only synthetic PCM and fixed embeddings: exercise the real voices loop, store and naming handlers without models.
"""
import datetime as dt
import threading

import anyio
import numpy as np
import pytest

from alldayrec import core, db, routes_audio, routes_speakers, selfvoice, summaries, summary, tenant, ws

SR = 16000
VOICE = np.array([1.0, 0, 0, 0])


class RoundDone(Exception):
    pass


@pytest.fixture
def audio_store(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")
    cuts = []

    async def cut(start, end, fmt, device=None):
        cuts.append((start, end, device))
        n = round((dt.datetime.fromisoformat(end) - dt.datetime.fromisoformat(start)).total_seconds() * SR)
        return (3000 * np.sin(2 * np.pi * 180 * np.arange(n) / SR)).astype(np.int16).tobytes()

    monkeypatch.setattr(routes_audio, "cut", cut)
    monkeypatch.setitem(core.state, "embed", lambda x: VOICE.copy())
    return cuts


def sample(durations, status="done", hour=10):
    start = dt.datetime.combine(dt.date.today(), dt.time(hour))
    rows = []
    for i, seconds in enumerate(durations):
        end = start + dt.timedelta(seconds=seconds)
        rows.append(dict(id=f"sample-{hour}-{i}", start=core.iso(start), end=core.iso(end), speaker="S1",
                         text="Synthetic enrollment sample.", source="sample-recorder"))
        start = end + dt.timedelta(seconds=0.2)
    c = dict(id="c" + dt.datetime.fromisoformat(rows[0]["start"]).strftime("%Y%m%d%H%M%S"),
             start=rows[0]["start"], end=rows[-1]["end"], device="sample-recorder", participants=["S1"],
             sentences=len(rows), speech_s=sum(durations))
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c)
        if status:
            db.put_summary({"conversation": c["id"], "status": "done" if status == "media" else status,
                            "media_spans": [{"from": rows[0]["start"], "to": rows[-1]["start"]}] if status == "media" else []}, t)
    return c, rows


def test_two_real_conversations_make_an_automatic_person(audio_store, monkeypatch):
    """Review probe: the embedding cap must not reduce the automatic-person speech total."""
    for hour in (9, 11):
        sample([15.0] * 8, "done", hour)
    with db.tx() as t:
        db.insert_voices(t, [{"conv": f"other-{i}", "label": "S1", "device": "live", "n": 5,
                              "speech_s": 40.0, "e": np.eye(4)[i + 1]} for i in range(3)])
    voices_round(monkeypatch)
    cards = anyio.run(routes_speakers.speaker_list)["cards"]
    assert [(g["count"], g["speech_s"]) for g in cards] == [(2, 240.0)]


@pytest.mark.parametrize("hours", [(9, 11), (9, 11, 13, 15)])
def test_recurring_media_voice_is_not_an_automatic_person(audio_store, monkeypatch, hours):
    """Review probe: recurring TV must stay in Other voices even above the automatic-person threshold."""
    for hour in hours:
        c, rows = sample([15.0] * 8, "media", hour)  # 120 seconds of the same TV voice
        assert summary.media_labels(rows, db.summary(c["id"])["media_spans"]) == {"S1"}
    with db.tx() as t:  # other live voices make device centring meaningful
        db.insert_voices(t, [{"conv": f"other-{i}", "label": "S1", "device": "live", "n": 5,
                              "speech_s": 40.0, "e": np.eye(4)[i + 1]} for i in range(3)])
    voices_round(monkeypatch)
    assert anyio.run(routes_speakers.speaker_list)["cards"] == [], "a media voice became an automatic person card"
    media = next(g for g in anyio.run(routes_speakers.speaker_list, True)["cards"] if g["count"] == len(hours))
    assert media["speech_s"] == len(hours) * 120, "the media test must exceed the automatic-person speech threshold"
    assert len(media["samples"]) == len(hours)
    assert len(db.voices()) == len(hours) + 3, "media voiceprints remain available for manual confirmation"


def test_media_is_excluded_from_self_candidates():
    vs = [{"conv": f"media-{i}", "label": "S1", "e": VOICE, "speech_s": 120, "media": True} for i in range(4)]
    assert selfvoice.candidates(vs) == ([], 0)
    vs.append({"conv": "live", "label": "S2", "e": np.eye(4)[1], "speech_s": 20})
    candidates, n = selfvoice.candidates(vs)
    assert n == 1 and len(candidates) == 1
    assert candidates[0]["members"] == [{"conv": "live", "label": "S2", "speech_s": 20.0}]


def test_media_does_not_pop_self_card(audio_store, monkeypatch):
    for hour in (9, 11, 13, 15):
        sample([15.0] * 8, "media", hour)
    db.put_kv("selfvoice", {"first_at": (dt.datetime.now() - dt.timedelta(days=2)).isoformat()})
    voices_round(monkeypatch)
    assert anyio.run(routes_speakers.self_get) == {"card": None}
    assert not core.owner_npz().exists()


def voices_round(monkeypatch):
    """Run one real loop iteration; replace only its five-minute timer, stopping at the next tick."""
    sleep, ticks = anyio.sleep, []

    async def tick(seconds):
        if seconds == 300:
            if ticks:
                raise RoundDone
            ticks.append(seconds)
        await sleep(0)

    with monkeypatch.context() as patch:
        patch.setattr(anyio, "sleep", tick)
        with pytest.raises(RoundDone):
            anyio.run(routes_speakers.voices_loop)


@pytest.mark.parametrize("durations,status", [
    ([2.36, 46.08], "done"),  # about 49 seconds merged into two lines, as in #165
    ([2.36, 46.08], "media"),
    ([12.0] * 4, "media"),  # isolates the media veto from the former three-line gate
    ([30.0], "done"),
    ([2.36, 46.08], None),
    ([2.36, 46.08], "pending"),
    ([2.36, 46.08], "failed"),
])
def test_single_sample_can_be_confirmed_as_me(audio_store, monkeypatch, durations, status):
    c, rows = sample(durations, status)
    if status == "media":
        assert summary.media_labels(rows, db.summary(c["id"])["media_spans"]) == {"S1"}
    voices_round(monkeypatch)
    other = anyio.run(routes_speakers.speaker_list, True)["cards"]
    assert len(other) == 1, "a single sample must offer a voice card to confirm"
    assert anyio.run(routes_speakers.speaker_list)["cards"] == [], "no automatic person from one conversation"
    v, = db.voices()
    assert v["n"] == len(rows) and v["speech_s"] == round(sum(durations), 1)
    assert audio_store and all(device == "sample-recorder" for _, _, device in audio_store)
    assert not core.owner_npz().exists() and db.people() == {}, "only a user choice may teach an identity"
    result = anyio.run(routes_speakers.speaker_name, {"group": other[0]["id"], "name": "我", "all": True})
    assert result["learned"] and result["renamed"] == 1
    assert [g["name"] for g in anyio.run(routes_speakers.speaker_list)["cards"]] == ["我"]
    assert np.allclose(next(iter(routes_speakers.owner_bank().values())), VOICE)


@pytest.mark.parametrize("durations,learned", [([4.9, 4.9], False), ([2.0] * 4, False), ([5.0, 5.0], True),
                                             ([1.5] + [1.0] * 20, True)])
def test_total_sentence_seconds_gate_learning_me(audio_store, monkeypatch, durations, learned):
    sample(durations)
    voices_round(monkeypatch)
    v, = db.voices()
    assert v["speech_s"] == round(sum(durations), 1)
    assert anyio.run(routes_speakers.speaker_list)["cards"] == []
    assert db.people() == {} and not core.owner_npz().exists()
    g, = anyio.run(routes_speakers.speaker_list, True)["cards"]
    result = anyio.run(routes_speakers.speaker_name, {"group": g["id"], "name": "我", "all": True})
    assert result["learned"] == learned and core.owner_npz().exists() == learned


def test_missing_audio_does_not_reduce_total_sentence_seconds(audio_store, monkeypatch):
    from fastapi import HTTPException

    c, rows = sample([8.0, 7.0, 6.0, 5.0, 4.0])
    cut = routes_audio.cut

    async def partial(start, end, fmt, device=None):
        if start == rows[0]["start"]:
            raise HTTPException(404, "synthetic missing audio")
        return (await cut(start, end, fmt, device))[:2 * SR]  # each available cut returns only one second

    monkeypatch.setattr(routes_audio, "cut", partial)
    voices_round(monkeypatch)
    v, = db.voices()
    assert v["k"] == 3 and v["speech_s"] == 30.0
    assert len(audio_store) == 3 and v["clip_start"] != rows[0]["start"]
    assert routes_speakers.learn_me("我", [routes_speakers.voice_of(c["id"], "S1")], [{"conv": c["id"]}])["learned"]


def test_fragment_voices_keep_merge_suggestions_and_previews(audio_store, monkeypatch):
    c, rows = sample([2.0] * 8)
    with db.tx() as t:
        for r in rows[4:]:
            t.execute("UPDATE sentences SET speaker = 'S2' WHERE id = ?", (r["id"],))
        c["participants"] = ["S1", "S2"]
        db.insert_conversation(t, c)
        db.insert_voices(t, [{"conv": "named", "label": "Alex", "device": "live", "n": 5, "speech_s": 40, "e": VOICE},
                             *[{"conv": f"other-{i}", "label": "S1", "device": "live", "n": 5,
                                "speech_s": 40, "e": np.eye(4)[i + 1]} for i in range(3)]])
    voices_round(monkeypatch)
    fragments = [v for v in db.voices() if v["conv"] == c["id"]]
    assert len(fragments) == 2 and all(v["speech_s"] == 8 for v in fragments)
    suggestions = anyio.run(routes_speakers.conversation_speakers, c["id"])
    assert suggestions["merge"] and suggestions["like"]
    assert set(suggestions["clips"]) == {"S1", "S2"}
    assert all(x["clip_start"] and x["clip_end"] for x in suggestions["clips"].values())


def test_music_and_unlabelled_rows_do_not_supply_enrollment_speech(audio_store, monkeypatch):
    c, rows = sample([4.0, 4.0, 20.0, 20.0])
    with db.tx() as t:
        t.execute("UPDATE sentences SET music = 1 WHERE id = ?", (rows[2]["id"],))
        t.execute("UPDATE sentences SET speaker = 'S?' WHERE id = ?", (rows[3]["id"],))
    voices_round(monkeypatch)
    v, = db.voices()
    assert v["label"] == "S1" and v["n"] == 2 and v["speech_s"] == 8.0
    assert len(audio_store) == 2


@pytest.mark.parametrize("confirm", [False, True])
def test_later_media_summary_keeps_enrollment(audio_store, monkeypatch, confirm):
    c, rows = sample([12.0] * 4, "pending")
    db.put_kv("selfvoice", {"first_at": (dt.datetime.now() - dt.timedelta(days=2)).isoformat()})
    voices_round(monkeypatch)
    assert anyio.run(routes_speakers.self_get)["card"] is None, "unfinished summaries cannot supply self candidates"
    if confirm:
        g, = anyio.run(routes_speakers.speaker_list, True)["cards"]
        assert anyio.run(routes_speakers.speaker_name, {"group": g["id"], "name": "我", "all": True})["learned"]
    before = [tuple(r) for r in db.conn().execute("SELECT * FROM voices")]
    owner = core.owner_npz().read_bytes() if confirm else None

    async def summarize(conv, tags):
        return {"conversation": c["id"], "status": "done", "title": "Synthetic media",
                "media_spans": [{"from": rows[0]["start"], "to": rows[-1]["start"]}]}

    async def broadcast(message):
        raise RoundDone

    monkeypatch.setattr(summaries, "summarize", summarize)
    monkeypatch.setattr(ws, "broadcast", broadcast)

    async def run():
        await tenant.T().summaries[0].send(c)
        await summaries.summarize_loop()

    with pytest.raises(RoundDone):
        anyio.run(run)
    assert db.summary(c["id"])["media_spans"], "the media summary actually completed"
    assert [tuple(r) for r in db.conn().execute("SELECT * FROM voices")] == before
    assert (core.owner_npz().read_bytes() if confirm else None) == owner
    assert (db.kv("selfvoice") or {}).get("card") is None
    assert all(v["media"] for v in routes_speakers.classified_voices())
    assert bool(anyio.run(routes_speakers.speaker_list)["cards"]) == confirm
    assert bool(anyio.run(routes_speakers.speaker_list, True)["cards"]) != confirm


@pytest.mark.parametrize("status,eligible", [(None, False), ("pending", False), ("done", True), ("failed", True), ("media", False)])
def test_self_card_waits_for_finished_summary(audio_store, monkeypatch, status, eligible):
    c, _ = sample([6.0] * 4, status)
    db.put_kv("selfvoice", {"first_at": (dt.datetime.now() - dt.timedelta(days=2)).isoformat()})
    voices_round(monkeypatch)
    assert len(db.voices()) == 1, "voice extraction itself does not wait for summaries"
    card = anyio.run(routes_speakers.self_get)["card"]
    assert bool(card) == eligible
    if card:
        assert card["convs"] == 1 and card["candidates"][0]["speech_s"] == 24
        assert all(clip["start"].startswith(c["start"][:13]) for clip in card["candidates"][0]["clips"])


@pytest.mark.parametrize("initial,finished", [(None, "done"), ("pending", "failed")])
def test_finished_summary_makes_stored_voice_eligible_without_reembedding(audio_store, monkeypatch, initial, finished):
    c, _ = sample([12.0] * 4, initial)
    db.put_kv("selfvoice", {"first_at": (dt.datetime.now() - dt.timedelta(days=2)).isoformat()})
    voices_round(monkeypatch)
    assert anyio.run(routes_speakers.self_get)["card"] is None
    before, cuts = [tuple(r) for r in db.conn().execute("SELECT * FROM voices")], len(audio_store)
    db.put_summary({"conversation": c["id"], "status": finished})
    voices_round(monkeypatch)
    card = anyio.run(routes_speakers.self_get)["card"]
    assert card["convs"] == 1 and card["candidates"][0]["speech_s"] == 48
    assert [tuple(r) for r in db.conn().execute("SELECT * FROM voices")] == before
    assert len(audio_store) == cuts, "finished summaries use the stored voiceprint; no extraction or short clips needed"


def test_self_rule_uses_total_sentence_seconds(audio_store, monkeypatch):
    for hour in (9, 11, 13):
        sample([30.0] * 3, "done", hour)  # 270 s total; only 135 s embedded
    voices_round(monkeypatch)
    card = anyio.run(routes_speakers.self_get)["card"]
    assert card["why"] == "rule" and card["convs"] == 3
    assert card["candidates"][0]["speech_s"] == 270


def test_self_none_counts_only_finished_non_media_conversations_off_loop(audio_store, monkeypatch):
    loop_thread, calls = threading.get_ident(), []
    classify = routes_speakers.classified_voices

    def checked(convs=None):
        calls.append(threading.get_ident())
        assert calls[-1] != loop_thread, "classification must not block the event loop"
        return classify(convs)

    monkeypatch.setattr(routes_speakers, "classified_voices", checked)
    for hour, status in ((9, "done"), (11, "failed"), (13, "pending"), (15, None), (17, "media")):
        sample([6.0] * 4, status, hour)
    db.put_kv("selfvoice", {"first_at": (dt.datetime.now() - dt.timedelta(days=2)).isoformat()})
    voices_round(monkeypatch)
    assert db.kv("selfvoice")["card"]["convs"] == 2
    before = len(calls)
    anyio.run(routes_speakers.self_answer, {"answer": "none"})
    assert len(calls) == before + 1
    assert db.kv("selfvoice")["wait"] == {"convs": 2}


def test_unrelated_media_summary_keeps_pending_self_card(audio_store, monkeypatch):
    sample([6.0] * 4, "done", 9)
    c, rows = sample([6.0] * 4, "pending", 11)
    db.put_kv("selfvoice", {"first_at": (dt.datetime.now() - dt.timedelta(days=2)).isoformat()})
    voices_round(monkeypatch)
    before, cuts, broadcasts = db.kv("selfvoice"), len(audio_store), []
    assert before["card"]["convs"] == 1

    async def summarize(conv, tags):
        return {"conversation": c["id"], "status": "done", "title": "Synthetic media",
                "media_spans": [{"from": rows[0]["start"], "to": rows[-1]["start"]}]}

    async def broadcast(message):
        broadcasts.append(message["type"])
        if message["type"] == "summary":
            raise RoundDone

    monkeypatch.setattr(summaries, "summarize", summarize)
    monkeypatch.setattr(ws, "broadcast", broadcast)

    async def run():
        await tenant.T().summaries[0].send(c)
        await summaries.summarize_loop()

    with pytest.raises(RoundDone):
        anyio.run(run)
    anyio.run(routes_speakers.self_round)
    assert db.kv("selfvoice") == before, "an unrelated summary must not clear or re-pop the pending card"
    assert broadcasts == ["summary"] and len(audio_store) == cuts, "no duplicate notification or clip decoding"


@pytest.mark.parametrize("media", [False, True])
def test_summary_with_unchanged_media_labels_keeps_people_cache(audio_store, monkeypatch, media):
    c, rows = sample([6.0] * 4, "media" if media else "done")
    voices_round(monkeypatch)
    anyio.run(routes_speakers.speaker_list, True)
    cache = dict(tenant.T().speakers)

    async def summarize(conv, tags):
        # Different spans but the same media label set: eligibility did not change.
        return {"conversation": c["id"], "status": "done", "title": "Synthetic updated summary",
                "media_spans": [{"from": rows[0]["start"], "to": rows[-1]["end"]}] if media else []}

    async def broadcast(message):
        raise RoundDone

    monkeypatch.setattr(summaries, "summarize", summarize)
    monkeypatch.setattr(ws, "broadcast", broadcast)

    async def run():
        await tenant.T().summaries[0].send(c)
        await summaries.summarize_loop()

    with pytest.raises(RoundDone):
        anyio.run(run)
    assert db.summary(c["id"])["title"] == "Synthetic updated summary"
    assert tenant.T().speakers["at"] == cache["at"]
    assert tenant.T().speakers["groups"] is cache["groups"]


def test_media_summary_changes_refresh_existing_voice_cards(audio_store, monkeypatch):
    """Uploaded/late-written voices are classified too; a new summary hides or restores a cached automatic card."""
    convs = [sample([15.0] * 8, hour=hour) for hour in (9, 11)]
    with db.tx() as t:
        db.insert_voices(t, [{"conv": c["id"], "label": "S1", "device": "live", "n": 8,
                              "speech_s": 120.0, "e": VOICE} for c, _ in convs])
        db.insert_voices(t, [{"conv": f"other-{i}", "label": "S1", "device": "live", "n": 5,
                              "speech_s": 40.0, "e": np.eye(4)[i + 1]} for i in range(3)])
    before = [tuple(r) for r in db.conn().execute("SELECT * FROM voices")]
    assert len(anyio.run(routes_speakers.speaker_list)["cards"]) == 1  # warm the cache

    async def broadcast(message):
        raise RoundDone

    monkeypatch.setattr(ws, "broadcast", broadcast)
    for media in (True, False):
        for c, rows in convs:
            async def summarize(conv, tags):
                return {"conversation": c["id"], "status": "done", "title": "Synthetic summary",
                        "media_spans": [{"from": rows[0]["start"], "to": rows[-1]["start"]}] if media else []}

            monkeypatch.setattr(summaries, "summarize", summarize)

            async def run():
                await tenant.T().summaries[0].send(c)
                await summaries.summarize_loop()

            with pytest.raises(RoundDone):
                anyio.run(run)
        assert bool(anyio.run(routes_speakers.speaker_list)["cards"]) != media
        assert bool([g for g in anyio.run(routes_speakers.speaker_list, True)["cards"] if g["count"] == 2]) == media
        assert [tuple(r) for r in db.conn().execute("SELECT * FROM voices")] == before


def test_existing_identities_are_untouched(audio_store, monkeypatch):
    sample([24.0, 24.0])
    np.savez(core.owner_npz(), existing=np.array([0.0, 1, 0, 0]))
    owner = core.owner_npz().read_bytes()
    with db.tx() as t:
        db.insert_voices(t, [{"conv": "existing-conversation", "label": "Alex", "device": "other-recorder", "n": 4,
                              "speech_s": 90.0, "e": np.array([0.0, 0, 1, 0])}])
        t.execute("INSERT INTO aliases VALUES ('Al', 'Alex')")
        t.execute("INSERT INTO people VALUES (?, ?, ?)", ("Alex", 4, db.blob(np.array([0.0, 0, 1, 0]))))
    existing = tuple(db.conn().execute("SELECT * FROM voices WHERE label = 'Alex'").fetchone())
    people = [tuple(r) for r in db.conn().execute("SELECT * FROM people")]
    voices_round(monkeypatch)
    voices_round(monkeypatch)
    assert tuple(db.conn().execute("SELECT * FROM voices WHERE label = 'Alex'").fetchone()) == existing
    assert [tuple(r) for r in db.conn().execute("SELECT * FROM people")] == people
    assert db.aliases() == {"Al": "Alex"} and core.owner_npz().read_bytes() == owner
    assert len(audio_store) == 2, "previously built voices are not recomputed on the next tick"
