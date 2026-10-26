"""Enrollment uses speech duration, independent of ASR line merging and summary media classification.

Only synthetic PCM and fixed embeddings: exercise the real voices loop, store and naming handlers without models.
"""
import datetime as dt

import anyio
import numpy as np
import pytest

from alldayrec import core, db, routes_audio, routes_speakers, summaries, summary, tenant, ws

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


def sample(durations, status="done"):
    start = dt.datetime.combine(dt.date.today(), dt.time(10))
    rows = []
    for i, seconds in enumerate(durations):
        end = start + dt.timedelta(seconds=seconds)
        rows.append(dict(id=f"sample-{i}", start=core.iso(start), end=core.iso(end), speaker="S1",
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


@pytest.mark.parametrize("durations,eligible", [([4.9, 4.9], False), ([2.0] * 4, False), ([5.0, 5.0], True)])
def test_speech_seconds_gate_unknown_voices(audio_store, monkeypatch, durations, eligible):
    sample(durations)
    voices_round(monkeypatch)
    assert bool(db.voices()) == eligible
    assert bool(audio_store) == eligible
    assert anyio.run(routes_speakers.speaker_list)["cards"] == []
    assert db.people() == {} and not core.owner_npz().exists()


def test_music_and_unlabelled_rows_do_not_supply_enrollment_speech(audio_store, monkeypatch):
    c, rows = sample([4.0, 4.0, 20.0, 20.0])
    with db.tx() as t:
        t.execute("UPDATE sentences SET music = 1 WHERE id = ?", (rows[2]["id"],))
        t.execute("UPDATE sentences SET speaker = 'S?' WHERE id = ?", (rows[3]["id"],))
    voices_round(monkeypatch)
    assert db.voices() == [] and audio_store == []


@pytest.mark.parametrize("confirm", [False, True])
def test_later_media_summary_keeps_enrollment(audio_store, monkeypatch, confirm):
    c, rows = sample([12.0] * 4)
    voices_round(monkeypatch)
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
