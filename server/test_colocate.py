"""同处备用 (colocate.py): two recorders in one place transcribe once, on synthetic levels. Unit cases on pieces rows
with envelopes (together, apart, a lagging clock, no levels, the standby's wait and takeover, never to a device that
left it), the late path, and two real pipelines over stored audio: together, apart, and the first one gone midway."""
import datetime as dt
import heapq
import json
import threading
import time

import anyio
import numpy as np
import pytest

from alldayrec import asr, audio, colocate, core, db, device_pipeline, infer, late, late_store, qwen_stream, ws
from alldayrec.tenant import T
from test_backlog import EnergyVad, flac, hear, rig, tone
from test_stream import FakeSession

X, Y = "LR-TEST-A", "LR-TEST-B"
T0 = round(time.time() * 1000) // 1000 * 1000 - 3600_000  # an hour ago: no piece waits for audio to come
F = audio.ENV_MS


def walk(seed: int, n: int) -> np.ndarray:
    """A room's level (-dBFS) each ENV_MS: speech and pauses rising and falling between 30 and 70."""
    rng, lv = np.random.default_rng(seed), np.zeros(n)
    for i, e in enumerate(rng.normal(0, 3, n)):  # a level that wanders and comes back (AR(1)), and each frame's own
        lv[i] = 0.9 * lv[i - 1] + e
    return np.clip(50 + lv + rng.normal(0, 2, n), 30, 70)


def put(device: str, lv: np.ndarray, start_s: float = 0, piece_s: int = 2):
    """Pieces of piece_s carrying levels lv from T0 + start_s."""
    k = piece_s * 1000 // F
    for i in range(0, len(lv), k):
        env = bytes(np.round(lv[i:i + k] * 2).astype(np.uint8))
        db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path, env) VALUES (?, ?, ?, 'x', ?)",
                          (device, round(T0 + start_s * 1000 + i * F), len(env) * F / 1000, env))


def squash(lv: np.ndarray, k: float) -> np.ndarray:
    """The same room heard with its rise and fall k times as deep (a worse microphone: k < 1)."""
    return 50 + (lv - 50) * k


def at(s: float) -> int:
    return round(T0 + s * 1000)


@pytest.fixture
def store(tmp_path):
    db.open_db(tmp_path / "live.db")
    return tmp_path


def test_envelope():
    x = (0.1 * 32767 * np.sin(np.arange(int(1.7 * audio.SR)) / 3)).astype(np.int16)  # -23 dBFS
    e = np.frombuffer(audio.envelope(x), np.uint8)
    assert len(e) == 3 and all(abs(v / 2 - 23) < 0.6 for v in e), e
    assert audio.envelope(np.zeros(audio.SR, np.int16)) == b"\xff\xff", "no signal is no level"


def test_correlation(store):
    room, other = walk(1, 1200), walk(2, 1200)
    put(X, room)
    put(Y, room + 8 + np.random.default_rng(3).normal(0, 2, 1200), start_s=3.5)  # louder, its clock 3.5 s off, its own noise
    put("LR-TEST-C", other)
    assert colocate.correlation(X, Y, at(100), at(400)) > 0.9
    assert abs(colocate.correlation(X, "LR-TEST-C", at(100), at(400))) < colocate.R_APART
    assert colocate.correlation(X, Y, at(700), at(1000)) is None, "no levels there"
    assert colocate.correlation(X, Y, at(500), at(800)) is None, "under MIN_FRAMES of the window"


def test_standby_waits_and_takes_over(store):
    room = walk(4, 2400)  # 20 min
    put(X, squash(room, 0.8))  # hears worse: stands by
    put(Y, room[:1200] + 5)  # Y's audio stops after 10 min
    a, b = at(400), at(420)
    assert colocate.decide(X, a, b) == Y and colocate.standby_of(X) == Y
    assert colocate.decide(Y, a, b) is None, "never to its own standby"
    assert colocate.decide(X, at(560), at(580)) == Y
    assert colocate.decide(X, at(600), at(620), now_ms=at(650)) == colocate.WAIT, "Y may still be sending"
    assert colocate.decide(X, at(600), at(620), now_ms=at(720)) is None and colocate.standby_of(X) is None, "Y gone: X takes over"
    put(Y, room[1300:] + 5, start_s=650)  # Y back
    assert colocate.decide(X, at(700), at(720)) is None, "not again within a window of the takeover"
    assert colocate.decide(X, at(1000), at(1020)) == Y


def test_apart_takes_over(store):
    room, away = walk(5, 2400), walk(6, 2400)
    put(X, np.r_[room[:1200], away[1200:]])  # X leaves the room after 10 min
    put(Y, room)
    assert colocate.decide(X, at(400), at(420)) == Y
    assert colocate.decide(X, at(500), at(520)) == Y
    took = next(s for s in range(500, 1200, 20) if colocate.decide(X, at(s), at(s + 20)) is None)
    assert 520 <= took <= 700, took  # within a few minutes of 600 (the window is centered: it looks ahead)
    assert colocate.decide(X, at(1100), at(1120)) is None


def test_no_comparable_levels_takes_over(store):
    """The other's mic sends only zeros (255): its audio covers the piece but says nothing; the standby takes over."""
    room = walk(16, 2400)
    put(X, room)
    put(Y, np.r_[room[:1200] + 5, np.full(1200, 127.5)])  # zeros after 10 min
    assert colocate.decide(X, at(400), at(420)) == Y
    took = next(s for s in range(420, 1200, 20) if colocate.decide(X, at(s), at(s + 20)) is None)
    assert 540 <= took <= 660, took  # under MIN_FRAMES of the window around the piece left


def test_stuck_level_takes_over(store):
    """The other's input stuck at one level (not zeros): no correlation, no usable level: the standby takes over."""
    put(X, walk(18, 2400))
    put(Y, np.full(2400, 40.0))
    db.put_kv(f"standby:{X}", {"of": Y, "since": at(0)})
    assert colocate.correlation(X, Y, at(250), at(550)) is None
    assert colocate.decide(X, at(390), at(410)) is None and colocate.standby_of(X) is None


def test_standby_left_to_a_third(store):
    """X stands by for Y, but Y left this piece to a third device: X cannot tell that one has it, X transcribes."""
    room = walk(19, 2400)
    put(X, room)
    put(Y, room + 3)
    assert colocate.decide(X, at(400), at(420)) == Y
    with db.tx() as t:
        db.record_processed(t, Y, [[at(440), at(460)]], "standby")
    assert colocate.decide(X, at(440), at(460)) is None and colocate.standby_of(X) == Y, "this piece only"
    assert colocate.decide(X, at(480), at(500)) == Y


def test_hole_in_the_others_audio(store):
    """Y's audio misses 8 s of X's 100 s piece (a BLE gap): X leaves Y the rest, the 8 s go to X's late layer."""
    room = walk(31, 2400)
    put(X, squash(room, 0.8))
    put(Y, room[:800] + 3)
    put(Y, room[816:] + 3, start_s=408)
    assert colocate.decide(X, at(340), at(440)) == Y
    assert colocate.split(X, Y, [[at(340), at(440)]]) == [[at(340), at(400)], [at(408), at(440)]]
    assert [json.loads(r[0]) for r in db.conn().execute("SELECT ranges FROM late_spans WHERE device = ?", (X,))] == [[[at(400), at(408)]]]


def test_tail_of_first_come(store, monkeypatch):
    """X transcribed [100, 400), stood by for Y from 400. Y's final piece [100, 420) is 94 % X's: first come, but
    X has not [400, 420): that goes to Y's late layer, which transcribes it (X stood by: not left to X again)."""
    room = walk(30, 2400)
    put(X, room)
    put(Y, room + 3)
    with db.tx() as t:
        db.record_processed(t, X, [[at(100), at(400)]], "final")
    assert colocate.decide(X, at(400), at(420)) == Y
    with db.tx() as t:
        db.record_processed(t, X, colocate.split(X, Y, [[at(400), at(420)]]), "standby")
    assert colocate.decide(Y, at(100), at(420)) == X
    skip = colocate.split(Y, X, [[at(100), at(420)]])
    assert skip == [[at(100), at(400)]]
    with db.tx() as t:
        db.record_processed(t, Y, skip, "standby")
    db.put_kv(f"finalized_to:{Y}", core.iso(dt.datetime.fromtimestamp(at(1300) / 1000)))
    span = late_store.claim()
    assert span and json.loads(span["ranges"]) == [[at(400), at(420)]]
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    monkeypatch.setattr(late, "read_exact", lambda d, a, b, bad=None: np.zeros(round((b - a) * audio.SR / 1000), np.int16))
    anyio.run(late.process, span)
    assert colocate.transcribed(Y, at(400), at(420)) == 1, "the 20 s are transcribed by Y"


def test_parting_after_a_tail_to_late(store):
    """X leaves Y the time first come; Y's audio misses 1 s at the end of X's last piece before X transcribes again
    (that 1 s went to X's late layer: split). Leaving was still the newest X did: the window goes back (resume), not
    only the 1 s, and once X transcribes it does not again."""
    room, away = walk(5, 2400), walk(6, 2400)
    put(X, np.r_[room[:1200], away[1200:]])  # X leaves at 600 s
    put(Y, room[:1438])
    put(Y, room[1440:], start_s=720)  # Y has nothing in [719, 720)
    with db.tx() as t:
        db.record_processed(t, Y, [[at(0), at(719)], [at(720), at(1200)]], "final")
    s = 300
    while (got := colocate.decide(X, at(s), at(s + 20))) == Y:
        with db.tx() as t:
            db.record_processed(t, X, colocate.split(X, Y, [[at(s), at(s + 20)]]), "standby")
        s += 20
    spans = late_store.union([x for r in db.conn().execute("SELECT ranges FROM late_spans WHERE device = ?", (X,)) for x in json.loads(r[0])])
    assert got is None and late_store.subtract([[at(600), at(s)]], spans) == [], (s, spans)
    with db.tx() as t:
        db.record_processed(t, X, [[at(s), at(s + 20)]], "final")
        db.record_processed(t, X, [[at(s - 100), at(s - 80)]], "standby")  # what the late layer left to Y again (left)
    assert colocate.decide(X, at(s + 20), at(s + 40)) is None
    assert db.processed(X, at(s - 100), at(s - 80), "standby"), "transcribing: no resume again"


def test_has_what_the_other_will_transcribe(store):
    """split() leaves Y what Y has: its transcript and the audio it will transcribe, not what it stands by for
    itself, nor what it left to a third device."""
    put(Y, walk(22, 800))
    with db.tx() as t:
        db.record_processed(t, Y, [[at(0), at(100)]], "final")
    assert colocate.split(X, Y, [[at(0), at(200)]]) == [[at(0), at(200)]], "Y will transcribe its audio"
    with db.tx() as t:
        db.record_processed(t, Y, [[at(130), at(150)]], "standby")
    assert colocate.split(X, Y, [[at(0), at(200)]]) == [[at(0), at(130)], [at(150), at(200)]], "not what Y left to a third"
    db.put_kv(f"standby:{Y}", {"of": "LR-TEST-C", "since": at(100)})
    assert colocate.split(X, Y, [[at(0), at(200)]]) == [[at(0), at(100)]], "Y stands by: only what it has transcribed"


def test_left_skips_only_what_the_other_has(store):
    room = walk(20, 1200)
    put(X, room)
    put(Y, room)
    with db.tx() as t:
        db.record_processed(t, Y, [[at(300), at(330)]], "final")
    assert colocate.left(X, [[at(300), at(360)]]) == [], "Y has half of the part: under COVER, all of it transcribed"
    with db.tx() as t:
        db.record_processed(t, Y, [[at(330), at(356)]], "final")
    assert colocate.left(X, [[at(300), at(360)]]) == [[at(300), at(356)]], "what Y has of it, not the 4 s it has not"


def test_resume_takes_pieces_across_the_window_start(store):
    """Pieces of 200 s, takeover at 1000: the one across 700 goes back whole, the one before stays left."""
    with db.tx() as t:
        db.record_processed(t, X, [[at(400), at(600)], [at(600), at(800)], [at(800), at(1000)]], "standby")
        colocate.resume(t, X, at(1000), round(time.time() * 1000))
    assert [json.loads(r[0]) for r in db.conn().execute("SELECT ranges FROM late_spans WHERE device = ?", (X,))] == [[[at(600), at(1000)]]]
    assert db.processed(X, at(0), at(1000), "standby") == [[at(400), at(600)]]


@pytest.mark.parametrize("first_come", [True, False])
def test_apart_sends_what_it_stood_by_for_late(store, first_come):
    """Leaving shows a minute or two after the two part: the pieces X left to Y in the last window go to the late
    layer, which leaves out again what Y has in the same place (left) and transcribes the rest. first_come: Y has
    transcribed the time already (X skips, no standby); else X stands by for Y and takes over."""
    room, away = walk(5, 2400), walk(6, 2400)
    put(X, np.r_[room[:1200], away[1200:]])
    put(Y, room)
    if first_come:
        with db.tx() as t:
            db.record_processed(t, Y, [[at(0), at(1200)]], "final")
    s = 300
    while (got := colocate.decide(X, at(s), at(s + 20))) == Y:
        with db.tx() as t:
            db.record_processed(t, X, [[at(s), at(s + 20)]], "standby")
        s += 20
    assert got is None and 600 < s <= 760, s
    queued = [json.loads(r[0]) for r in db.conn().execute("SELECT ranges FROM late_spans WHERE device = ?", (X,))]
    assert queued == [[[at(s - 300), at(s)]]], queued
    assert db.processed(X, at(s - 300), at(s), "standby") == [], "no longer counted as handled"
    assert db.processed(X, at(0), at(s - 300), "standby"), "older standby pieces stay"
    assert colocate.standby_of(X) is None
    if not first_come:
        return
    skip = colocate.left(X, queued[0])
    assert skip and late_store.subtract([[at(600), at(s)]], skip) == [[at(600), at(s)]], ("apart part transcribed", skip)


def test_first_come_and_never_to_one_that_left_it(store):
    room = walk(7, 2400)
    put(X, room)
    put(Y, room)
    with db.tx() as t:
        db.record_processed(t, Y, [[at(0), at(600)]], "final")
    db.put_kv(f"standby:{Y}", {"of": X, "since": at(500)})
    assert colocate.decide(X, at(400), at(420)) == Y and colocate.standby_of(X) is None, "Y has it: skipped, X is no standby"
    db.put_kv(f"standby:{Y}", None)
    with db.tx() as t:
        db.record_processed(t, Y, [[at(700), at(720)]], "standby")
    assert colocate.decide(X, at(710), at(730)) is None and colocate.standby_of(X) is None, "Y left part of it to X"
    assert colocate.decide(X, at(740), at(760)) == Y and colocate.standby_of(X) == Y, "Y has not got here: it will have it"


def test_imports_and_unknowns_transcribe(store):
    room = walk(8, 1200)
    put(X, room)
    put("import-20261105", room)
    assert colocate.decide(X, at(400), at(420)) is None, "an import is no recorder"
    db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path) VALUES (?, ?, 600, 'x')", (Y, at(0)))
    assert colocate.decide(X, at(400), at(420)) is None, "pieces from before envelopes: not judged"


def test_quality(store):
    """Speech over the floor, less what the speech falls short of QUIET_DB, less missing and clipped frames."""
    room = walk(41, 600)
    put(X, room)
    put(Y, room + 27)  # 27 dB quieter, as a recorder at a low gain
    put("LR-TEST-C", np.r_[room[:420], np.full(180, 127.5)])  # zeros for 30 % of it
    put("LR-TEST-D", np.maximum(room - 40, 0))  # louder: some frames clip
    q = {d: colocate.quality(d, at(0), at(300)) for d in (X, Y, "LR-TEST-C", "LR-TEST-D")}
    lv = -room
    assert abs(q[X] - (np.percentile(lv, 90) - np.percentile(lv, 10) - max(0, colocate.QUIET_DB - np.percentile(lv, 90)))) < 0.5, q
    assert q[X] - q[Y] > 20, ("as loud over its floor, but under QUIET_DB", q)
    assert q["LR-TEST-C"] < q[X] - colocate.BAD_DB * 0.3 + 3 and q["LR-TEST-D"] < q[X], q
    assert colocate.quality(X, at(400), at(500)) is None, "no level"


def test_new_device_waits_for_its_first_window(store):
    """Alone, a device just started is not held up. With X recording, Y's first pieces wait (WAIT) for the window from
    Y's start, at most FIRST_WAIT_S after the piece; once judged, Y stands by for X (X hears better) from its first."""
    room = walk(42, 2400)
    put(Y, room[1200:1260], start_s=600)  # Y's first 30 s
    a, b = at(600), at(620)
    assert colocate.decide(Y, a, b, now_ms=at(640)) is None, "alone: transcribed at once"
    put(X, room - 5)
    assert colocate.decide(Y, a, b, now_ms=at(640)) == colocate.WAIT, "X there: wait for Y's levels"
    assert colocate.decide(Y, a, b, now_ms=b + colocate.FIRST_WAIT_S * 1000 + 1) is None, "not past FIRST_WAIT_S"
    assert colocate.standby_of(Y) is None
    put(Y, room[1260:1600], start_s=630)  # Y's audio on to 800 s
    assert colocate.decide(Y, a, b, now_ms=at(640)) == X and colocate.standby_of(Y) == X


def run_both(dur_s: int, lag_s: int = 0, off_s: int = 0, lat_s: int = 0):
    """X's and Y's final layers by decide() over [0, dur_s): X's in pieces of 20 s, Y's of 20 s from off_s (a first
    one shorter), each decided as it ends, Y's lag_s later; what the pipeline would record (split, final rows), only
    where the device has audio. lat_s: the engine's delay, a piece transcribed commits that long after it was decided
    and is on its way meanwhile (busy); WAIT: the same piece again 5 s later, the device's next ones after it. Then the
    late spans less what another device transcribed (left). Who transcribed each piece (by its start), and the two
    devices' final and late ranges."""
    from alldayrec.pipeline_state import PipelineState
    cuts = {X: list(range(0, dur_s, 20)) + [dur_s], Y: sorted({0, *range(off_s, dur_s, 20), dur_s})}
    dts = lambda s: dt.datetime.fromtimestamp(at(s) / 1000)
    if lat_s:
        T().devices.update({d: PipelineState(done_to=dts(0), cut_to=dts(0)) for d in (X, Y)})
    who = {}
    ev = [(cuts[d][1] + lag_s * (d == Y), 1 + (d == Y), d, 0, None) for d in (X, Y)]  # each one's next piece, or a commit
    while ev:
        t, _, d, i, heard = heapq.heappop(ev)
        if heard:  # back from the engine
            with db.tx() as tx:
                db.record_processed(tx, d, heard, "final")
            T().devices[d].done_to = dts(i)
            continue
        s, e = cuts[d][i], cuts[d][i + 1]
        if heard := colocate.meet([[at(s), at(e)]], colocate.audio(d, at(s), at(e))):  # what it heard of the piece
            if (got := colocate.decide(d, heard[0][0], heard[-1][1])) == colocate.WAIT:
                assert lat_s, (d, s)
                heapq.heappush(ev, (t + 5, 1 + (d == Y), d, i, None))
                continue
            if got is None:
                if lat_s:
                    T().devices[d].cut_to = dts(e)
                    heapq.heappush(ev, (t + lat_s, 0, d, e, heard))
                else:
                    with db.tx() as tx:
                        db.record_processed(tx, d, heard, "final")
                who.setdefault(s, set()).add(d)
            else:
                with db.tx() as tx:
                    db.record_processed(tx, d, colocate.split(d, got, heard), "standby")
        if i + 2 < len(cuts[d]):
            heapq.heappush(ev, (max(t, cuts[d][i + 2] + lag_s * (d == Y)), 1 + (d == Y), d, i + 1, None))
    late = {d: late_store.union([x for r in db.conn().execute("SELECT ranges FROM late_spans WHERE device = ?", (d,))
                                 for x in json.loads(r[0])]) for d in (X, Y)}
    late = {d: late_store.subtract(late[d], colocate.left(d, late[d])) if late[d] else [] for d in (X, Y)}
    final = {d: late_store.union(db.processed(d, at(0), at(dur_s), "final")) for d in (X, Y)}
    return who, final, late


def once(final: dict, late: dict, dur_s: int):
    """Every ms of [0, dur_s) transcribed by one device, final or late: none twice, none by neither."""
    rs = [r for d in (X, Y) for r in final[d] + late[d]]
    assert late_store.subtract([[at(0), at(dur_s)]], rs) == [], ("by neither", final, late)
    assert sum(b - a for a, b in rs) == at(dur_s) - at(0), ("twice", final, late)


@pytest.mark.parametrize("lag_s,off_s", [(0, 0), (120, 10), (-120, 10)])
def test_better_newcomer_takes_over(store, lag_s, off_s):
    """X (worse: a squashed rise and fall) records; Y (better) starts at 300 s: Y transcribes from where X had got to
    (what X transcribed before stays X's) and X stands by for Y; every ms once around the handover. lag_s: Y's final
    layer that far behind X's (or ahead), its pieces off_s off X's."""
    room = walk(43, 1800)
    put(X, squash(room, 0.4))
    put(Y, room[600:], start_s=300)
    who, final, late = run_both(900, lag_s, off_s)
    once(final, late, 900)
    assert colocate.standby_of(X) == Y and colocate.standby_of(Y) is None, who
    assert db.kv(f"standby_left:{X}") is None, "X never took over (no gone before Y's audio: since)"
    after = [v for s, v in who.items() if s >= 560]
    assert after and all(v == {Y} for v in after), ("Y from a window after it started on", who)


@pytest.mark.parametrize("lag_s,off_s", [(0, 0), (120, 10), (-120, 10)])
def test_switch_with_hysteresis(store, capsys, lag_s, off_s):
    """X stands by for Y. X better by a few dB: stays a standby, also through 4 min of Y much worse (over SWITCH_DB
    in one window, not two). Y then hears much worse for long: X transcribes, Y stands by for it, every ms by one at
    least, twice only in the two windows before the switch (X takes them again: resume, took) and, with Y's final layer
    ahead, what Y had past it (X transcribes it too). Y back a few dB better than X: no switch back (no flapping)."""
    room = walk(44, 5400)  # 45 min
    k = np.r_[np.full(600, 0.85), np.full(480, 0.3), np.full(720, 0.85), np.full(1800, 0.3), np.full(1800, 1.0)]  # Y's depth: X's 1, then 0.85
    put(X, np.r_[room[:3600], squash(room[3600:], 0.85)])
    put(Y, 50 + (room - 50) * k)
    q = lambda s: colocate.lead(X, Y, at(s))
    sw = colocate.SWITCH_DB
    assert 0 < q(900) < sw < q(540) and sw < q(1800) and -sw < q(2700) < 0, (q(900), q(540), q(1800), q(2700))
    db.put_kv(f"standby:{X}", {"of": Y, "since": at(0)})
    who, final, late = run_both(2700, lag_s, off_s)
    x, y = (final[d] + late[d] for d in (X, Y))
    assert late_store.subtract([[at(0), at(2700)]], x + y) == [], ("by neither", final, late)
    # one switch, none back: Y's final pieces up to it, X's from it on (with Y ahead, both in the time Y had past it)
    xs, ys = ([s for s, v in sorted(who.items()) if d in v] for d in (X, Y))
    assert 900 < xs[0] < 1800 + abs(lag_s) + 60 and xs == list(range(xs[0], 2700, 20)), (xs, who)
    assert ys == [s for s in sorted({0, *range(off_s, 2700, 20)}) if s <= ys[-1]] and ys[-1] < xs[0] - min(0, lag_s) + 20, (ys, who)
    twice, took = colocate.meet(x, y), db.kv(f"took:{X}")  # the two windows before it, and what Y's final had past it
    sec = lambda rs: sum(b - a for a, b in rs) / 1000
    assert twice and sec(took) <= 2 * colocate.WINDOW_S + 20, (took, twice)
    assert late_store.subtract(twice, took + [[at(xs[0]), at(xs[0] - min(0, lag_s) + 20)]]) == [], (took, twice)
    with capsys.disabled():
        print(f"\nswitch, lag {lag_s} s: twice {sec(twice):.0f} s")
    assert colocate.standby_of(Y) == X and colocate.standby_of(X) is None


def test_switches_keep_what_each_handed_over(store):
    """X stands by for Y; Y much worse: X transcribes (switch); X much worse: Y again; Y much worse: X again. What
    each of X's switches handed over stays in took, the first too, so late audio requeued after the second (#313)
    is still not skipped there (left)."""
    room, n = walk(52, 7600), [1800, 1800, 1800, 2200]  # 15 min each, the last one on past 60 min (a standby waits)
    put(X, 50 + (room - 50) * np.repeat([0.85, 1.0, 0.3, 1.0], n))
    put(Y, 50 + (room - 50) * np.repeat([1.0, 0.3, 1.0, 0.3], n))
    db.put_kv(f"standby:{X}", {"of": Y, "since": at(0)})
    who, final, late = run_both(3600)
    xs = [s for s, v in sorted(who.items()) if v == {X}]
    first = [s for s, n in zip(xs, xs[1:]) if n - s > 20]  # X's last piece before Y took over again
    assert len(first) == 1 and 900 < xs[0] < first[0] < 2700 < xs[-1], xs
    took = db.kv(f"took:{X}")
    assert len(took) == 2 and took[0][1] <= at(xs[0]) and at(first[0]) < took[1][0] <= took[1][1] <= at(3600), (took, xs)
    assert colocate.left(X, took) == [], "neither switch's windows skipped"


@pytest.mark.parametrize("x_first", [True, False])
def test_newcomer_a_few_db_better_whoever_looks_first(store, x_first):
    """Y starts at 300 s, a few dB better than X: Y transcribes, X stands by, also when X judges a piece of the window
    Y started in before Y judges its first (X's window from before Y's start is no worse a Y: those frames were none)."""
    room = walk(43, 2400)
    put(X, squash(room, 0.8))
    put(Y, room[600:], start_s=300)
    assert 0 < colocate.lead(Y, X, at(600)) < colocate.SWITCH_DB and colocate.lead(X, Y, at(490)) < 0
    looks = [(X, at(470), at(490)), (Y, at(300), at(320))]
    for d, a, b in looks if x_first else looks[::-1]:
        colocate.decide(d, a, b)
    assert colocate.standby_of(X) == Y and colocate.standby_of(Y) is None


@pytest.mark.parametrize("lag_s,off_s", [(0, 0), (120, 10), (-120, 10)])
def test_back_together_the_standby_stands_by_again(store, lag_s, off_s):
    """X stands by for Y, hearing a few dB better (under SWITCH_DB). X apart for 5 min (from 600 s): it takes over;
    back together, Y transcribes again and X stands by again, whichever looks first. Every ms by one at least, X's
    only around the time apart."""
    room, away = walk(49, 5400), walk(50, 5400)
    put(X, np.r_[room[:1200], away[1200:1800], room[1800:]])
    put(Y, squash(room, 0.9))
    assert 0 < colocate.lead(X, Y, at(2400)) < colocate.SWITCH_DB
    db.put_kv(f"standby:{X}", {"of": Y, "since": at(0)})
    who, final, late = run_both(2700, lag_s, off_s)
    assert late_store.subtract([[at(0), at(2700)]], final[Y] + late[Y]) == [], (final, late)
    assert late_store.subtract(final[X] + late[X], [[at(300), at(1500)]]) == [], (final, late)
    assert db.kv(f"standby_left:{X}"), "X took over while apart"
    assert colocate.standby_of(X) == Y and colocate.standby_of(Y) is None, who
    assert all(v == {Y} for s, v in who.items() if s >= 1800 + 2 * colocate.WINDOW_S + abs(lag_s)), who


@pytest.mark.parametrize("new", [X, Y])
@pytest.mark.parametrize("x_first", [True, False])
def test_off_since_parting_judged_as_new(store, new, x_first):
    """X stood by for Y and took over (apart) an hour before either recorded again (off overnight: a hole in both).
    Back in one place, one just started (from 300 s): X hears better by a few dB (under SWITCH_DB): X transcribes,
    Y stands by, whoever looks first; not X standing by again for the parting (a: X new, b: Y new, takes X)."""
    room = walk(51, 2400)
    old = Y if new == X else X
    put(old, room if old == X else squash(room, 0.8))
    put(new, (room if new == X else squash(room, 0.8))[600:], start_s=300)
    db.put_kv(f"standby_left:{X}", at(-3600))
    db.put_kv(f"parted:{X}", Y)
    assert 0 < colocate.lead(X, Y, at(600)) < colocate.SWITCH_DB
    looks = [(old, at(470), at(490)), (new, at(300), at(320))]
    for d, a, b in looks if x_first == (old == X) else looks[::-1]:
        colocate.decide(d, a, b)
    assert colocate.standby_of(Y) == X and colocate.standby_of(X) is None


def test_no_take_in_the_window_the_other_took_over_in(store):
    """Y hears better, but X took over from Y within the last window (apart, by X's window around its piece): Y does
    not make X stand by again until that window is past (11-12: they took turns each 20 s)."""
    room = walk(47, 2400)
    put(X, squash(room, 0.4))
    put(Y, room)
    db.put_kv(f"standby_left:{X}", at(500))
    assert colocate.decide(Y, at(600), at(620)) is None and colocate.standby_of(X) is None
    assert colocate.decide(Y, at(800), at(820)) is None and colocate.standby_of(X) == Y


def test_take_waits_for_the_piece_at_the_engine(store):
    """Y (hears better) takes X while X's piece over it is at the engine: Y waits for it (X commits it as its own)."""
    from alldayrec.pipeline_state import PipelineState
    room = walk(46, 2400)
    put(X, squash(room, 0.4))
    put(Y, room)
    dts = lambda s: dt.datetime.fromtimestamp(at(s) / 1000)
    T().devices[X] = PipelineState(done_to=dts(400), cut_to=dts(430))
    assert colocate.decide(Y, at(410), at(440)) == colocate.WAIT and colocate.standby_of(X) == Y
    with db.tx() as t:
        db.record_processed(t, X, [[at(400), at(430)]], "final")
    T().devices[X].done_to = dts(430)
    assert colocate.decide(Y, at(410), at(440)) == X, "what X has stays X's (split: the rest late)"


def test_draft_where_the_better_one_drafts(store):
    """Two drafting in one room: the one that hears worse (by over SWITCH_DB) leaves its drafts to the other, not the
    other way."""
    from alldayrec.pipeline_state import PipelineState
    room = walk(45, 1200)
    put(X, room)
    put(Y, squash(room, 0.4))
    T().devices.update({X: PipelineState(drafting=True), Y: PipelineState(drafting=True)})
    assert colocate.lead(X, Y, at(600)) > colocate.SWITCH_DB
    assert colocate.drafted_by(X, now_ms=at(600)) is None and colocate.drafted_by(Y, now_ms=at(600)) == X


# walking out of the room (#324): X worn, Y in the room. Y hears better, X stands by; at OUT s the wearer leaves with X

OUT, DUR = 600, 2700


def walkout(talk: float, room: float) -> tuple:
    """The wearer's speech (a level that walks) heard by both until OUT: Y as it is, X a little worse (squashed). From OUT X hears
    it as it is (better) and Y the wearer `talk` deep through the door over its own room's noise (7 dB) `room` deep:
    talk 0, the two apart; a little talk, r 0.3-0.6; more, r over R_TOGETHER; X better by over SWITCH_DB in each."""
    p, q, k = walk(61, 2 * DUR), np.random.default_rng(62).normal(0, 7, 2 * DUR), 2 * OUT
    put(X, np.r_[squash(p[:k], 0.9), p[k:]], piece_s=10)  # pieces of 10 s: levels() reads each one an hour back
    put(Y, np.r_[p[:k], 50 + talk * (p[k:] - 50) + room * q[k:]], piece_s=10)
    db.put_kv(f"standby:{X}", {"of": Y, "since": at(0)})
    return lambda s: colocate.correlation(X, Y, at(s - 150), at(s + 150)), lambda s: colocate.lead(X, Y, at(s))


@pytest.mark.parametrize("lat_s", [0, 30, 90])
@pytest.mark.parametrize("lag_s,off_s", [(0, 0), (120, 10), (-120, 10)])
@pytest.mark.parametrize("case,talk,room,r_lo,r_hi", [
    ("a: apart", 0, 0.38, -1, colocate.R_APART),
    ("b: r 0.3-0.6", 0.22, 0.38, colocate.R_APART, colocate.R_TOGETHER),
    ("c: through the door", 0.3, 0.25, colocate.R_TOGETHER, 1)])
def test_walk_out_of_the_room(store, capsys, case, talk, room, r_lo, r_hi, lag_s, off_s, lat_s):
    """X stands by for Y; the wearer walks out with X at OUT and talks on, all of it X's speech: from OUT every ms of X's
    audio transcribed by X (final, or late less what Y has: left), not left to Y, who heard it worse or not at all.
    Before OUT Y's, but around it (a window at most) X's too. Prints when X transcribes again, what is by neither, twice.
    lag_s: Y's final layer that far behind X's (-: ahead, as a standby waits for the window after its piece); lat_s: the
    engine's delay (Y's piece on its way at the switch: Y commits it after, it is still X's to transcribe)."""
    r, q = walkout(talk, room)
    rs = {s: r(s + 10) for s in range(300, DUR - 150, 20)}  # the window decide() judges each of run_both's pieces by
    assert min(rs.values()) >= r_lo and max(v for s, v in rs.items() if s >= OUT + 300) < r_hi, case
    assert all(q(s) >= colocate.SWITCH_DB for s in range(OUT + 300, DUR, 60)), case
    who, final, late = run_both(DUR, lag_s, off_s, lat_s)
    took = min(s for s, v in who.items() if X in v)
    lost = late_store.subtract([[at(OUT), at(DUR)]], final[X] + late[X])
    twice = colocate.meet(final[X] + late[X], [[at(0), at(OUT)]])
    sec = lambda rs: sum(b - a for a, b in rs) / 1000
    with capsys.disabled():
        print(f"\n{case}, lag {lag_s} s, engine {lat_s} s: walked out at {OUT} s, X transcribes from {took} s ({took - OUT} s after); "
              f"after it by neither: {sec(lost):.0f} s; before it by both: {sec(twice):.0f} s; "
              f"twice in all: {sec(colocate.meet(final[X] + late[X], final[Y] + late[Y])):.0f} s")
    assert not lost, (case, took, lost)
    assert sec(twice) <= colocate.WINDOW_S, (case, twice)


def test_walk_out_drafts(store, capsys):
    """Drafts on walking out: drafted_by() leaves X's drafts to Y while r over R_TOGETHER and X not better by SWITCH_DB
    (one window); in b (r 0.3-0.6) and c (through the door) when does it stop? (The pipeline drafts on X only when X is
    no standby too: device_pipeline drafting().)"""
    from alldayrec.pipeline_state import PipelineState
    for case, talk, room in (("b", 0.22, 0.38), ("c", 0.3, 0.25)):
        db.conn().execute("DELETE FROM pieces")
        walkout(talk, room)
        T().devices.update({X: PipelineState(standby=Y), Y: PipelineState(drafting=True)})
        assert colocate.drafted_by(X, now_ms=at(OUT)) == Y
        free = next(s for s in range(OUT, DUR, device_pipeline.DRAFT_NEAR_S) if colocate.drafted_by(X, now_ms=at(s)) is None)
        with capsys.disabled():
            print(f"\n{case}: drafted_by() frees X's drafts {free - OUT} s after walking out")
        assert free - OUT <= colocate.WINDOW_S, (case, free)


def test_late_audio_where_one_hears_worse_but_none_switched(store):
    """Together all along, Y transcribed it all, Y 10 dB worse from 600 s (no switch: X's audio came late, a catch-up):
    left() leaves all of it to Y. Only what a switch handed over (take) is transcribed again."""
    room = walk(11, 2400)
    put(X, room)
    put(Y, np.r_[room[:1200], squash(room[1200:], 0.5)])
    assert colocate.lead(X, Y, at(1200)) > colocate.SWITCH_DB
    with db.tx() as t:
        db.record_processed(t, Y, [[at(0), at(1200)]], "final")
    assert late_store.subtract([[at(0), at(1200)]], colocate.left(X, [[at(0), at(1200)]])) == []


def test_late_audio_left_to_who_has_it(store, monkeypatch):
    room, away = walk(9, 2400), walk(10, 2400)
    put(X, np.r_[room[:1200], away[1200:]])
    put(Y, room)
    with db.tx() as t:
        db.record_processed(t, Y, [[at(0), at(1200)]], "final")
    skip = colocate.left(X, [[at(0), at(1200)]])
    # the window from a part's start reaches past the parting (600 s) from 420 s: X transcribes those again
    assert late_store.subtract([[at(60), at(420)]], skip) == [] and skip[-1][1] <= at(600), ("together: Y has it; apart: not", skip)
    db.put_kv(f"finalized_to:{X}", core.iso(dt.datetime.fromtimestamp(at(1300) / 1000)))
    with db.tx() as t:
        late_store.enqueue(t, X, at(0), at(1200), round(time.time() * 1000), True)
    span = late_store.claim()
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    threads_ = []
    left = colocate.left
    monkeypatch.setattr(colocate, "left", lambda *a: threads_.append(threading.current_thread()) or left(*a))
    read = []
    monkeypatch.setattr(late, "read_exact", lambda d, a, b, bad=None: read.append((a, b)) or np.zeros(round((b - a) * audio.SR / 1000), np.int16))
    anyio.run(late.process, span)
    assert threads_ and threads_[0] is not threading.main_thread(), "left() reads the store off the event loop"
    assert read and min(a for a, _ in read) == skip[-1][1], "only the audio apart (and a part at the seam) was read for the engine"
    assert db.processed(X, at(0), at(1200), "standby") == skip
    assert db.conn().execute("SELECT status FROM late_spans").fetchone()[0] == "done"


# two pipelines over stored audio

PIECE_S, N = 10, 90  # 15 min in pieces of 10 s; a 2 s tone each 30 s (the fake engines read its number back)


def recording(room: int, noise: int, first: int = 0, at: int = 10, floor: float = 0) -> np.ndarray:
    """15 min in a room whose level walks (seed room; noise the VAD hears no speech in, its own seed), a tone each
    30 s from `at` s, numbered from first; floor: the device's own hiss (RMS) under the room (hears it worse)."""
    lv = 10 ** (-walk(room, N * PIECE_S * 1000 // F) / 20) * 32768 * 0.25  # under the energy VAD's 500
    x = (np.random.default_rng(noise).normal(0, 1, N * PIECE_S * audio.SR) * (np.repeat(lv, audio.SR * F // 1000) + floor)).astype(np.int16)
    for k in range(N * PIECE_S // 30):
        i = (k * 30 + at) * audio.SR
        x[i:i + 2 * audio.SR] = tone(first + k, 2)
    return x


def run_two(tmp_path, monkeypatch, xa, xb, b_upto=N * PIECE_S, b_after=False, holes=None, a_from=0):
    """X's and Y's pipelines over their stored audio of an hour ago, X's from a_from s, Y's only to b_upto; b_after:
    Y's starts once X's is done (its final layer behind); holes: {device: piece index} saved 1 s short. Their final
    rows' texts."""
    rig(tmp_path, monkeypatch, hear)
    t0 = dt.datetime.fromtimestamp(time.time() - 3600).replace(microsecond=0)
    for d, x, upto, start in ((X, xa, N * PIECE_S, a_from), (Y, xb, b_upto, 0)):
        for k in range(start // PIECE_S, upto // PIECE_S):
            short = audio.SR if (holes or {}).get(d) == k else 0
            audio.save(d, round((t0 + dt.timedelta(seconds=k * PIECE_S)).timestamp() * 1000),
                       flac(x[k * PIECE_S * audio.SR:(k + 1) * PIECE_S * audio.SR - short]), "audio/flac")
        db.put_kv(f"end_at:{d}", core.iso(t0 + dt.timedelta(seconds=upto)))  # the recorder stopped: its last piece is cut

    async def until_final(d, upto):
        for _ in range(240):
            if (st := T().devices.get(d)) and st.done_to and st.done_to >= t0 + dt.timedelta(seconds=upto - 1):
                return
            await anyio.sleep(0.5)
        raise AssertionError((d, T().devices.get(d) and T().devices[d].done_to))

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, X)
            if not b_after:
                tg.start_soon(device_pipeline.pipeline, Y)
            await until_final(X, N * PIECE_S)
            if b_after:
                tg.start_soon(device_pipeline.pipeline, Y)
            await until_final(Y, b_upto)
            tg.cancel_scope.cancel()
    anyio.run(run)
    return [r["text"] for r in db.sentences("", "z", source=X)], [r["text"] for r in db.sentences("", "z", source=Y)]


ALL = [str(k) for k in range(30)]


def test_two_pipelines_together(tmp_path, monkeypatch):
    a, b = run_two(tmp_path, monkeypatch, recording(11, 1), recording(11, 2))  # one room, each its own noise
    both = sorted(a + b, key=int)
    assert sorted(set(both), key=int) == ALL and [t for t in both if int(t) >= 7] == ALL[7:], (a, b)
    assert min(len(a), len(b)) <= 7, ("the standby has only what came before the first window", a, b)
    assert [d for d in (X, Y) if T().devices[d].standby] in ([X], [Y])


def test_two_pipelines_together_hole_in_the_others_audio(tmp_path, monkeypatch):
    """Each misses 1 s of silence (Y at 419 s, X at 539 s): the standby sends the other's missing second to its own
    late layer instead of leaving it to the other (device_pipeline: colocate.split)."""
    run_two(tmp_path, monkeypatch, recording(11, 1), recording(11, 2), holes={X: 53, Y: 41})
    sb = next(d for d in (X, Y) if T().devices[d].standby)
    hole = {X: 539, Y: 419}[T().devices[sb].standby]
    spans = [x for r in db.conn().execute("SELECT ranges FROM late_spans WHERE device = ?", (sb,)) for x in json.loads(r[0])]
    t0 = min(r[0] for r in db.processed(sb, 0, 2 ** 62))
    assert any(a <= t0 + hole * 1000 + 100 and b >= t0 + (hole + 1) * 1000 - 100 for a, b in spans), (sb, hole, spans)


def test_two_pipelines_apart(tmp_path, monkeypatch):
    a, b = run_two(tmp_path, monkeypatch, recording(13, 1), recording(14, 2, 100, 25))  # elsewhere: what is said there is said at other times
    assert a == ALL and b == [str(k) for k in range(100, 130)], (a, b)
    assert not T().devices[X].standby and not T().devices[Y].standby


def test_two_pipelines_first_one_gone(tmp_path, monkeypatch):
    """Y's audio ends after 7.5 min and its final layer is behind: X stands by for it, takes over where its audio
    ends; Y then transcribes what X stood by for (never back to X: nobody would have it), not what X has already."""
    a, b = run_two(tmp_path, monkeypatch, recording(15, 1), recording(15, 2), b_upto=450, b_after=True)
    both = sorted(a + b, key=int)
    assert sorted(set(both), key=int) == ALL and len(both) - len(set(both)) <= 1, (a, b)
    assert ALL[15:] == [t for t in a if int(t) >= 15], ("X from where Y's audio ended", a)
    assert b and set(b) <= set(ALL[:15]), ("Y: what X stood by for (a final piece is up to 5 min: from the first)", b)
    assert not T().devices[X].standby and not T().devices[Y].standby


def test_two_pipelines_better_one_joins(tmp_path, monkeypatch):
    """Y (hears worse: its own hiss) records alone; X joins at 300 s: X transcribes from where Y had got to, Y stands
    by for it; every tone once, in final rows or (around the handover) in a late span."""
    a, b = run_two(tmp_path, monkeypatch, recording(11, 1), recording(11, 2, floor=60), a_from=300)
    assert len(a) + len(b) == len(set(a + b)), ("none twice", a, b)
    t0 = min(r[0] for d in (X, Y) for r in db.processed(d, 0, 2 ** 62))
    spans = [x for r in db.conn().execute("SELECT ranges FROM late_spans") for x in json.loads(r[0])]
    lost = [k for k in range(30) if str(k) not in a + b
            and not any(lo <= t0 + (10 + 30 * k) * 1000 and hi >= t0 + (12 + 30 * k) * 1000 for lo, hi in spans)]
    assert not lost, ("each tone transcribed or in a late span", lost, a, b, spans)
    assert set(ALL[20:]) <= set(a), ("X from a window after it joined on", a, b)
    assert T().devices[Y].standby == X and not T().devices[X].standby


def test_conversation_lists_who_stood_by_short(store):
    """A 20 s conversation whose 20 s alone do not correlate (each its own noise in a quiet moment): judged on the
    WINDOW_S around it, as decide() did."""
    from alldayrec import routes_api
    room = walk(23, 1200)
    put(X, room)
    put(Y, np.r_[room[:600], walk(24, 40) - 25, room[640:]])
    c = dict(id="c2", start=core.iso(dt.datetime.fromtimestamp(at(300) / 1000)), end=core.iso(dt.datetime.fromtimestamp(at(320) / 1000)),
             participants=[], sentences=0, speech_s=0, device=X)
    with db.tx() as t:
        db.insert_conversation(t, c)
        db.record_processed(t, Y, [[at(300), at(320)]], "standby")
    assert (colocate.correlation(X, Y, at(300), at(320)) or 0) < colocate.R_TOGETHER
    assert anyio.run(routes_api.conversation, "c2")["standby"] == [Y]


def test_conversation_lists_who_stood_by(store):
    from alldayrec import routes_api
    put(X, walk(17, 1200))
    put(Y, walk(17, 1200))
    c = dict(id="c1", start=core.iso(dt.datetime.fromtimestamp(at(100) / 1000)), end=core.iso(dt.datetime.fromtimestamp(at(400) / 1000)),
             participants=[], sentences=0, speech_s=0, device=X)
    with db.tx() as t:
        db.insert_conversation(t, c)
        db.record_processed(t, Y, [[at(300), at(500)]], "standby")
    assert anyio.run(routes_api.conversation, "c1")["standby"] == [Y]
    with db.tx() as t:
        db.record_processed(t, X, [[at(100), at(400)]], "standby")  # its own does not count
    assert anyio.run(routes_api.conversation, "c1")["standby"] == [Y]
    put("LR-TEST-C", walk(21, 1200))  # stood by for a fourth device elsewhere: not this conversation's
    with db.tx() as t:
        db.record_processed(t, "LR-TEST-C", [[at(100), at(400)]], "standby")
    assert anyio.run(routes_api.conversation, "c1")["standby"] == [Y]


def test_takeover_hands_back_a_standby_piece_cut_ahead(tmp_path, monkeypatch):
    """X alone for 5 min (its piece slow at the engine), then with Y for 5 min (stands by), then elsewhere (takes over,
    the window it stood by in going to its late layer). Side by side (FINAL_ASR_CONCURRENCY) must not cut the standby
    piece while the first is at the engine: resume() at the takeover would not see it, and it would be committed as
    standby after: that speech transcribed by neither."""
    async def slow(x, partial=None):
        await anyio.sleep(3)
        return await hear(x)
    rig(tmp_path, monkeypatch, slow)
    t0 = dt.datetime.fromtimestamp(time.time() - 3600).replace(microsecond=0)
    S = audio.SR
    xa = np.r_[recording(11, 1, floor=60)[:600 * S], recording(13, 1, 50, 25)[600 * S:]]  # with Y (hears worse), then elsewhere
    xb = recording(11, 2, 100)
    for d, x, first in ((X, xa, 0), (Y, xb, 27)):
        for k in range(first, N):
            audio.save(d, round((t0 + dt.timedelta(seconds=k * PIECE_S)).timestamp() * 1000),
                       flac(x[k * PIECE_S * S:(k + 1) * PIECE_S * S]), "audio/flac")
    db.put_kv(f"end_at:{X}", core.iso(t0 + dt.timedelta(seconds=N * PIECE_S)))

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, X)
            for _ in range(240):
                if (st := T().devices.get(X)) and st.done_to and st.done_to >= t0 + dt.timedelta(seconds=N * PIECE_S - 1):
                    break
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
    anyio.run(run)
    lo, hi = (round((t0 + dt.timedelta(seconds=s)).timestamp() * 1000) for s in (0, N * PIECE_S))
    final = db.processed(X, lo, hi, "final")
    assert db.kv(f"standby_left:{X}") and len(final) >= 2, ("stood by, then took over", final)
    assert db.processed(X, lo, hi, "standby") == [], "the piece it stood by in went back at the takeover"
    spans = [x for r in db.conn().execute("SELECT ranges FROM late_spans WHERE device = ?", (X,)) for x in json.loads(r[0])]
    assert late_store.subtract([[final[0][1], final[-1][0]]], spans + final) == [], (final, spans)


def live_rig(tmp_path, monkeypatch, window: int):
    """Live drafts by the stream engine, final layers out of the way, colocate's window `window` s, a look each second."""
    async def never(x, partial=None):
        await anyio.sleep_forever()
    rig(tmp_path, monkeypatch, never)
    monkeypatch.setattr(asr, "DRAFT_ENGINE", "qwen-stream")
    monkeypatch.setattr(qwen_stream, "Session", FakeSession)
    monkeypatch.setattr(FakeSession, "ends_first", False)
    monkeypatch.setattr(colocate, "WINDOW_S", window)
    monkeypatch.setattr(device_pipeline, "DRAFT_NEAR_S", 1)
    FakeSession.opened = []

    async def broadcast(msg):
        pass
    monkeypatch.setattr(ws, "broadcast", broadcast)


def test_two_live_draft_once(tmp_path, monkeypatch):
    """Two recorders in one room recording now, a page watching, their final layers far behind (as a piece is judged
    only once it is cut): both draft until their levels can be judged, then one of them only (drafted_by); every
    sentence drafted. Time short: a 6 s window, a look each second."""
    live_rig(tmp_path, monkeypatch, 6)
    S, n = audio.SR, 8  # a tone each 3 s; each device its own noise under the VAD's
    xs = {d: np.concatenate([np.r_[tone(7 + j, 1), (np.random.default_rng(seed * 10 + j).normal(0, 60, 2 * S)).astype(np.int16)]
                             for j in range(n)]) for d, seed in ((X, 1), (Y, 2))}
    live0 = dt.datetime.fromtimestamp(round(time.time()) + 2)

    async def run():
        async with anyio.create_task_group() as tg:
            for d in (X, Y):
                tg.start_soon(device_pipeline.pipeline, d)
            for k in range(3 * n):
                await anyio.sleep(live0.timestamp() + k + 1 - time.time())
                for d in (X, Y):
                    await anyio.to_thread.run_sync(audio.save, d, round((live0 + dt.timedelta(seconds=k)).timestamp() * 1000),
                                                   flac(xs[d][k * S:(k + 1) * S]), "audio/flac")
            await anyio.sleep(3)
            tg.cancel_scope.cancel()
    anyio.run(run)
    a, b = ({r["text"] for r in db.drafts("", None, d)} for d in (X, Y))
    every = {str(7 + j) for j in range(n)}
    assert a | b == every, (a, b)
    later = {str(7 + j) for j in range(4, n)}  # from 12 s: judged after ~4 s (MIN_FRAMES of 6 s), a look at most 1 s after
    assert later <= a ^ b, ("one device drafts them, not both", a, b)


def test_drafter_leaves_no_gap(tmp_path, monkeypatch):
    """Two in one room until one drafts for both; then that one stops sending (walks off, BLE drops) and the other
    records on: it drafts every tone after, from where the other stopped, not from when it next judges itself alone
    (a window later: 2 min live). A 12 s window, a look each second, a tone each 3 s."""
    live_rig(tmp_path, monkeypatch, 12)
    S, n, m = audio.SR, 8, 8
    sec = lambda j, seed: np.r_[tone(7 + j, 1), np.random.default_rng(seed * 100 + j).normal(0, 60, 2 * S).astype(np.int16)]
    live0 = dt.datetime.fromtimestamp(round(time.time()) + 2)
    gone = {}

    async def run():
        async with anyio.create_task_group() as tg:
            for d in (X, Y):
                tg.start_soon(device_pipeline.pipeline, d)
            for k in range(3 * (n + m)):
                j = k // 3
                if j == n and not gone:  # the one drafting for both leaves
                    on = [d for d in (X, Y) if T().devices[d].drafting]
                    gone["d"] = on[0] if len(on) == 1 else on
                await anyio.sleep(live0.timestamp() + k + 1 - time.time())
                for d, seed in ((X, 1), (Y, 2)):
                    if j < n or d != gone.get("d"):
                        await anyio.to_thread.run_sync(audio.save, d, round((live0 + dt.timedelta(seconds=k)).timestamp() * 1000),
                                                       flac(sec(j, seed)[(k % 3) * S:(k % 3 + 1) * S]), "audio/flac")
            await anyio.sleep(3)
            tg.cancel_scope.cancel()
    anyio.run(run)
    assert gone["d"] in (X, Y), ("one device drafts for both before one leaves", gone)
    stay = {r["text"] for r in db.drafts("", None, Y if gone["d"] == X else X)}
    after = {str(7 + j) for j in range(n, n + m)}
    assert after <= stay, ("tones after the drafter left, drafted by no one", sorted(after - stay, key=int))
