"""同处备用 (colocate.py): two recorders in one place transcribe once, on synthetic levels. Unit cases on pieces rows
with envelopes (together, apart, a lagging clock, no levels, the standby's wait and takeover, never to a device that
left it), the late path, and two real pipelines over stored audio: together, apart, and the first one gone midway."""
import datetime as dt
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
    put(X, room)
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
    put(X, room)
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


def test_late_audio_left_to_who_has_it(store, monkeypatch):
    room, away = walk(9, 2400), walk(10, 2400)
    put(X, np.r_[room[:1200], away[1200:]])
    put(Y, room)
    with db.tx() as t:
        db.record_processed(t, Y, [[at(0), at(1200)]], "final")
    skip = colocate.left(X, [[at(0), at(1200)]])
    assert late_store.subtract([[at(60), at(480)]], skip) == [] and skip[-1][1] <= at(660), ("together: Y has it; apart: not", skip)
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


def recording(room: int, noise: int, first: int = 0, at: int = 10) -> np.ndarray:
    """15 min in a room whose level walks (seed room; noise the VAD hears no speech in, its own seed), a tone each
    30 s from `at` s, numbered from first."""
    lv = 10 ** (-walk(room, N * PIECE_S * 1000 // F) / 20) * 32768 * 0.25  # under the energy VAD's 500
    x = (np.random.default_rng(noise).normal(0, 1, N * PIECE_S * audio.SR) * np.repeat(lv, audio.SR * F // 1000)).astype(np.int16)
    for k in range(N * PIECE_S // 30):
        i = (k * 30 + at) * audio.SR
        x[i:i + 2 * audio.SR] = tone(first + k, 2)
    return x


def run_two(tmp_path, monkeypatch, xa, xb, b_upto=N * PIECE_S, b_after=False, holes=None):
    """X's and Y's pipelines over their stored audio of an hour ago, Y's only to b_upto; b_after: Y's starts once X's
    is done (its final layer behind); holes: {device: piece index} saved 1 s short. Their final rows' texts."""
    rig(tmp_path, monkeypatch, hear)
    t0 = dt.datetime.fromtimestamp(time.time() - 3600).replace(microsecond=0)
    for d, x, upto in ((X, xa, N * PIECE_S), (Y, xb, b_upto)):
        for k in range(upto // PIECE_S):
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
    xa, xb = np.r_[recording(11, 1)[:600 * S], recording(13, 1, 50, 25)[600 * S:]], recording(11, 2, 100)  # elsewhere: other times
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


def test_two_live_draft_once(tmp_path, monkeypatch):
    """Two recorders in one room recording now, a page watching, their final layers far behind (as a piece is judged
    only once it is cut): both draft until their levels can be judged, then one of them only (drafted_by); every
    sentence drafted. Time short: a 6 s window, a look each second."""
    async def never(x, partial=None):
        await anyio.sleep_forever()
    rig(tmp_path, monkeypatch, never)
    monkeypatch.setattr(asr, "DRAFT_ENGINE", "qwen-stream")
    monkeypatch.setattr(qwen_stream, "Session", FakeSession)
    monkeypatch.setattr(FakeSession, "ends_first", False)
    monkeypatch.setattr(colocate, "WINDOW_S", 6)
    monkeypatch.setattr(device_pipeline, "DRAFT_NEAR_S", 1)
    FakeSession.opened = []

    async def broadcast(msg):
        pass
    monkeypatch.setattr(ws, "broadcast", broadcast)
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
