"""「哪个是你？」(selfvoice.py): candidates and their probabilities, when the card pops, which sentences it plays;
the card's round trip on the server (only without a voiceprint, nothing taken before the user picks); a pick reaches
the pipeline at its next conversation, never the open one."""
import datetime as dt
import time

import anyio
import numpy as np
import pytest
from fastapi import HTTPException

import core
import db
import device_pipeline
import routes_audio
import routes_speakers
import selfvoice as sv
import tenant
import ws
from test_backlog import DEVICE, PIECE_S, SR, flac, hear, rig, save, tone

rng = np.random.default_rng(0)
unit = sv.unit
noisy = lambda v: unit(v + rng.normal(size=len(v)) * 0.15)  # cos ~0.85 to its centre, like one person's voices
ME, FR = unit(rng.normal(size=16)), unit(rng.normal(size=16))


def voice(conv, label, base, s, lv):
    return {"conv": conv, "label": label, "e": noisy(base), "speech_s": s, "level": lv}


def test_candidates():
    vs = [voice(f"c{i}", "S1", ME, 100, -40) for i in range(1, 6)]  # the wearer: 5 of 6 conversations, a bit louder
    vs += [voice(f"c{i}", "S2", FR, 80, -42) for i in (1, 2)]  # a friend in 2
    vs += [voice(f"c{i}", "S3", unit(rng.normal(size=16)), 20, -43) for i in range(1, 7)]  # a stranger each
    cands, n = sv.candidates(vs)
    assert n == 6
    me = cands[0]
    assert {m["label"] for m in me["members"]} == {"S1"} and me["convs"] == 5 and me["speech_s"] == 500
    assert me["p"] >= sv.POP_P and float(me["bank"] @ ME) > 0.95, me["p"]
    assert any({m["label"] for m in c["members"]} == {"S2"} for c in cands[1:])
    assert sum(c["p"] for c in cands) < 1, "the rest goes to 'none of them'"
    # two voices in every conversation, as long: the louder one comes first (10-06, a whole day with one person)
    vs = [voice(f"c{i}", "A", ME, 60, -44) for i in range(3)] + [voice(f"c{i}", "B", FR, 60, -40) for i in range(3)]
    cands, _ = sv.candidates(vs)
    assert [c["members"][0]["label"] for c in cands] == ["B", "A"] and cands[0]["p"] > cands[1]["p"]
    assert sv.candidates([]) == ([], 0)


def test_due():
    now = dt.datetime(2026, 10, 14, 12)
    first = (now - dt.timedelta(hours=2)).isoformat()
    top = lambda **k: [{"convs": 3, "speech_s": 180, "p": sv.POP_P, **k}]
    st = {"first_at": first}
    assert sv.due(st, top(), 3, now) == "rule"
    assert sv.due(st, top(), 2, now) is None, "3 conversations"
    assert sv.due(st, top(convs=2), 5, now) is None, "the first candidate in 3"
    assert sv.due(st, top(speech_s=179), 5, now) is None, "3 min of it"
    assert sv.due(st, top(p=sv.POP_P - 0.01), 5, now) is None, "clear enough"
    assert sv.due(st, [], 5, now) is None
    old = {"first_at": (now - dt.timedelta(hours=24)).isoformat()}
    assert sv.due(old, top(p=0.6), 5, now) == "fallback"
    assert sv.due({**old, "card": {"why": "rule"}}, top(), 5, now) is None, "a card is up"
    assert sv.due({**old, "confirmed": {"pick": 0}}, top(), 5, now) is None
    later = {**old, "wait": {"until": (now + dt.timedelta(hours=1)).isoformat()}}
    assert sv.due(later, top(), 5, now) is None and sv.due(later, top(p=0.1), 5, now + dt.timedelta(hours=1)) == "again"
    none = {**old, "wait": {"convs": 5}}
    assert sv.due(none, top(), 9, now) is None and sv.due(none, top(p=0.1), 10, now) == "again"
    assert [sv.tier(p) for p in (0.99, 0.8, 0.5, 0.4, 0.39, 0.0)] == ["很可能", "很可能", "可能", "可能", "不确定", "不确定"]


def test_level():
    x = (0.1 * 32767 * np.sin(2 * np.pi * 300 * np.arange(SR) / SR)).astype(np.int16)
    assert abs(sv.level(x) + 23.0) < 0.2  # mean square 0.005
    assert abs(sv.level(np.r_[x, np.zeros(SR, np.int16)]) + 23.0) < 0.2, "the pause after it does not count"
    assert sv.level(x[:800]) is None
    rel = sv.loudness([{"conv": "a", "level": -40}, {"conv": "a", "level": -45}, {"conv": "a", "level": -47},
                       {"conv": "b", "level": -40}, {"conv": "c", "level": None}])
    assert rel == [6.0, -1.5, -4.5, None, None]


T = lambda s: (dt.datetime(2026, 10, 14, 9) + dt.timedelta(seconds=s)).isoformat(timespec="milliseconds")


def row(a, b, who, text="一二三四五六七八九"):
    return {"start": T(a), "end": T(b), "speaker": who, "text": text}


def test_clips():
    rows = [row(0, 3, "S1"), row(3.2, 5, "S2"),          # right next to another speaker: loosest tier only
            row(10, 13.5, "S1"),                          # nobody within 1 s
            row(20, 31, "S1"),                            # too long
            row(40, 42, "S1", "短"),                       # too little text
            row(50, 52.2, "S1"), row(52.8, 54, "S2")]     # 0.6 s from the next one
    got = sv.shortlist(rows, "S1")
    assert [(r["start"][-12:-4], r["k"]) for r in got] == [("09:00:10", 0), ("09:00:50", 1), ("09:00:00", 2)]
    assert sv.shortlist(rows, "S1", per=1)[0]["start"] == T(10)

    def p(conv, gap, e, lvl=-40.0, dur=3.0):
        return {"conv": conv, "gap": gap, "dur": dur, "text": "一二三四五六七八", "lvl": lvl, "lmed": -40.0, "e": unit(e)}
    a1, a2 = p("a", 2, ME), p("a", 2, ME + 0.5 * FR)  # a2 sounds a bit like the other candidate
    b1 = p("b", 2, ME, lvl=-44)                        # quieter than its voice by more than 3 dB
    c1 = p("c", 0.3, ME + 1.5 * FR)                    # close to another speaker, sounds even more like the other
    assert sv.pick([a2, b1, c1, a1], ME, [FR]) == [a1, c1, a2], "one per conversation first, relaxed while short of 3"
    d1, e1, f1 = p("d", 2, ME + 0.2 * FR), p("e", 2, ME + 0.1 * FR), p("f", 0.0, ME)
    assert sv.pick([f1, d1, a1, e1], ME, [FR]) == [a1, e1, d1], "3 in the strictest tier: no looser one"


def test_card_round_trip(tmp_path, monkeypatch):
    db.open_db(tmp_path / "live.db")
    amp = {}  # a sentence's start -> how loud its tone is: S1 3000, others 1000

    async def cut(start, end, fmt, device=None):
        n = int((dt.datetime.fromisoformat(end) - dt.datetime.fromisoformat(start)).total_seconds() * SR)
        return (np.ones(n) * amp[start]).astype(np.int16).tobytes()
    monkeypatch.setattr(routes_audio, "cut", cut)
    monkeypatch.setitem(core.state, "embed", lambda x: noisy(ME) if x.max() > 2000 else noisy(FR))
    monkeypatch.setattr(ws, "broadcast", lambda m: anyio.sleep(0))
    with db.tx() as t:
        for i in range(4):
            o = 600 * i
            rows = [row(o + 10 * k, o + 10 * k + 3, "S1") for k in range(4)] + [row(o + 10 * k + 5, o + 10 * k + 7, "S2") for k in range(4)]
            for j, r in enumerate(rows):
                r.update(id=f"{i}-{j}", source="mic")
                amp[r["start"]] = 3000 if r["speaker"] == "S1" else 1000
            db.insert_sentences(t, rows)
            db.insert_conversation(t, {"id": f"c{i}", "start": T(o), "end": T(o + 40), "participants": ["S1", "S2"],
                                       "sentences": 8, "speech_s": 20.0, "device": "mic"})
            db.insert_voices(t, [{"conv": f"c{i}", "label": "S1", "device": "live", "n": 4, "speech_s": 200, "e": noisy(ME), "level": -40},
                                 {"conv": f"c{i}", "label": "S2", "device": "live", "n": 4, "speech_s": 30, "e": noisy(unit(rng.normal(size=16))), "level": -45}])

    npz = tmp_path / "owner-redimnet2.npz"
    np.savez(npz, mic=FR)  # a voiceprint already (the main instance): no card, nothing written
    anyio.run(routes_speakers.self_round)
    assert db.kv("selfvoice") is None and anyio.run(routes_speakers.self_get) == {"card": None}
    npz.unlink()

    anyio.run(routes_speakers.self_round)
    st = db.kv("selfvoice")
    card = st["card"]
    assert card["why"] == "rule" and card["convs"] == 4 and not npz.exists(), "nothing is taken before the user picks"
    c0 = card["candidates"][0]
    assert c0["tier"] == "很可能" and c0["convs"] == 4 and float(np.asarray(c0["bank"]) @ ME) > 0.95
    assert len(c0["clips"]) == 3 and len({c["start"][:16] for c in c0["clips"]}) == 3, "3 clips from 3 conversations"
    assert all(amp[c["start"]] == 3000 and c["device"] == "mic" and c["text"] for c in c0["clips"])
    shown = anyio.run(routes_speakers.self_get)["card"]
    assert "bank" not in shown["candidates"][0] and "p" not in shown["candidates"][0], "no % on the card"

    anyio.run(routes_speakers.self_answer, {"answer": "later"})
    st = db.kv("selfvoice")
    assert st["card"] is None and "until" in st["wait"] and st["log"][-1]["answer"] == "later"
    anyio.run(routes_speakers.self_round)
    assert db.kv("selfvoice")["card"] is None, "a day later"
    db.put_kv("selfvoice", {**st, "wait": {"until": T(0)}})
    anyio.run(routes_speakers.self_round)
    assert db.kv("selfvoice")["card"]["why"] == "again"
    with pytest.raises(HTTPException):
        anyio.run(routes_speakers.self_answer, {"answer": 7})

    anyio.run(routes_speakers.self_answer, {"answer": 0})
    assert float(np.load(npz)["self"] @ ME) > 0.95 and float(tenant.T().owner["self"] @ ME) > 0.95
    st = db.kv("selfvoice")
    assert st["confirmed"]["pick"] == 0 and st["card"] is None and [x["answer"] for x in st["log"]] == ["later", 0]
    assert anyio.run(routes_speakers.self_get) == {"card": None}
    with pytest.raises(HTTPException):
        anyio.run(routes_speakers.self_answer, {"answer": 0})


def test_pick_reaches_next_conversation(tmp_path, monkeypatch):
    """Picked while a conversation is open: it stays without 我 (also at its relabel at the close); the next one is 我."""
    rig(tmp_path, monkeypatch, hear)  # every sentence's voiceprint is [0, 1, 0, 0]
    (tmp_path / "owner-redimnet2.npz").unlink()
    monkeypatch.setattr(ws, "broadcast", lambda m: anyio.sleep(0))
    db.put_kv("selfvoice", {"first_at": T(0), "card": {"why": "rule", "convs": 3, "candidates": [{"p": 0.99, "bank": [0, 1, 0, 0]}]}})
    silence = np.zeros(PIECE_S * SR, np.int16)
    speech = lambda i: flac(np.concatenate([silence[:5 * SR], tone(i, 4), silence[9 * SR:15 * SR], tone(i + 1, 4), silence[19 * SR:]]))
    # A: pieces 0-1, 4 sentences (16 s: its first, alone, is S? and no speech); 6 quiet minutes close it; B: pieces 8-9
    pieces = {0: speech(0), 1: speech(2), 8: speech(10), 9: speech(12)}
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 16 * PIECE_S)
    texts = lambda: {r["text"]: r["speaker"] for r in db.sentences(core.iso(t0), core.iso(t0 + dt.timedelta(seconds=16 * PIECE_S)), source=DEVICE)}

    async def run():
        save(t0, pieces[0])
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for _ in range(60):
                if "1" in texts():
                    break
                await anyio.sleep(0.5)
            assert "1" in texts() and db.kv(f"open_conversation:{DEVICE}"), "A is open"
            await routes_speakers.self_answer({"answer": 0})
            for i in range(1, 16):
                save(t0 + dt.timedelta(seconds=PIECE_S * i), pieces.get(i) or flac(silence))
            db.put_kv(f"end_at:{DEVICE}", core.iso(t0 + dt.timedelta(seconds=16 * PIECE_S)))  # the recorder stopped: B closes
            for _ in range(120):
                if len(db.conversations()) == 2:
                    break
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
        got = texts()
        assert "我" not in {got[str(i)] for i in range(4)}, got
        assert [got[str(i)] for i in (11, 12, 13)] == ["我"] * 3, got  # 10 too, alone, is S? and in no conversation
        a, b = sorted(db.conversations(), key=lambda c: c["start"])
        assert "我" not in a["participants"] and b["participants"] == ["我"], (a, b)

    anyio.run(run)
