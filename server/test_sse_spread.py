"""A run of StepFun SSE deltas on one time spread between the timed deltas around it (stepfun_asr.spread), as
sse() hands the deltas to every reader (final: merge, draft: sentences).

The 10-15 13:38 window of main's LR-P3-BDAE, replayed as the engine got it (squeezed, 263 s), its times only: 460
deltas on 62644 ms and the next one at 180660 ms (118 s of speech on one instant), and the 23 turns the file job found
over that stretch. TEXT: one placeholder per delta, 字 for each character, 。 where one closed a sentence; no words."""
from collections import Counter

import anyio
import httpx2
import numpy as np

from alldayrec import net
from alldayrec import stepfun_asr

# the deltas' times (ms of the sent audio), run-length coded: (start_time, end_time, deltas)
TIMES = [(56120, 56200, 1), (56200, 56280, 1), (56440, 56520, 1), (62644, 62644, 460), (180660, 180820, 1),
         (180820, 180980, 1), (181700, 181780, 1), (181780, 181780, 2), (181780, 181940, 1), (181940, 182020, 1)]
# one placeholder per delta
TEXT = ('字|字|字。|字|字|字|字|字字|字|字|字|字字|字|字字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|'
        '字|字字|字|字|字|字字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字。|字|字|字|字|字|字|字|'
        '字|字|字。|字|字|字|字|字|字|字|字|字字字字|字字|字字字字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字字。|字|字字|字|字|字|字|字|字字|字|字|字|字|字|字|字字|'
        '字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字。|字|字|字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字字'
        '|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字字|字|字字|字|字字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字'
        '字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字字|字|字|字|字字|字|字|字|字|字|字|字|字'
        '|字|字字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字。|字|字|字字|字字|字字|字|字|字|字|'
        '字|字|字字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字字|'
        '字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字字|字|字|'
        '字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字|字字|字|字|字|字|字字|字|字|字|字|字|字|字|'
        '字|字')
# the file job's turns: (start_ms, end_ms, speaker, characters)
TURNS = [(57552, 76353, 'SPEAKER_01', 53), (76833, 80293, 'SPEAKER_01', 26), (81353, 84233, 'SPEAKER_02', 8), (84233, 84553, 'SPEAKER_01', 6),
         (84553, 84793, 'SPEAKER_02', 2), (84793, 84873, 'SPEAKER_01', 1), (89933, 93513, 'SPEAKER_01', 7), (102953, 104892, 'SPEAKER_01', 26),
         (105172, 107493, 'SPEAKER_01', 8), (109573, 112093, 'SPEAKER_01', 13), (112933, 120312, 'SPEAKER_01', 20), (120672, 123713, 'SPEAKER_01', 9),
         (124113, 130711, 'SPEAKER_01', 23), (130711, 132553, 'SPEAKER_02', 4), (132553, 134712, 'SPEAKER_01', 12), (134712, 135913, 'SPEAKER_02', 6),
         (135913, 141373, 'SPEAKER_01', 29), (142252, 142752, 'SPEAKER_01', 2), (143933, 144452, 'SPEAKER_02', 3), (145512, 147192, 'SPEAKER_01', 13),
         (156793, 159832, 'SPEAKER_01', 8), (160173, 180072, 'SPEAKER_01', 93), (180553, 201892, 'SPEAKER_01', 98)]
STRETCH = 56520, 180660  # the deltas with times around the run


def window(at: int = 62644) -> tuple[list[dict], list[dict]]:
    """The window's deltas (its run on `at`) and file turns."""
    ts = [(at, at) if a == 62644 else (a, b) for a, b, n in TIMES for _ in range(n)]
    ds = [{"delta": t, "start_time": a, "end_time": b} for (a, b), t in zip(ts, TEXT.split("|"), strict=True)]
    return ds, [{"start_ms": a, "end_ms": b, "speaker": s, "text": "字" * n} for a, b, s, n in TURNS]


def collapsed(segs: list[dict]) -> int:
    """Groups of more than one sentence on one start, of no length."""
    return sum(n > 1 for n in Counter(g["start_ms"] for g in segs if g["start_ms"] == g["end_ms"]).values())


def chars(segs: list[dict]) -> int:
    return sum(len(g["text"]) for g in segs)




def d(t: str, a: int | None, b: int | None) -> dict:
    return {"delta": t, "start_time": a, "end_time": b}


def times(ds: list[dict]) -> list[tuple]:
    return [(x["start_time"], x["end_time"]) for x in ds]


def test_window_1338():
    ds, turns = window()
    end = 263300  # the sent audio's ms
    assert collapsed(stepfun_asr.sentences(ds)) == 1, "as it came: the draft layer's sentences pile up on 62644"
    assert sum(1 for g in stepfun_asr.merge(ds, turns) if g.get("speaker")) == 2, "as it came: one turn takes the run, 21 are dropped"
    fixed = stepfun_asr.spread(ds, end)
    assert ds == window()[0], "the deltas given stay as they were"
    run = fixed[3:463]
    assert run[0]["start_time"] == STRETCH[0] and run[-1]["end_time"] == STRETCH[1] and \
        all(a["end_time"] == b["start_time"] < b["end_time"] for a, b in zip(run, run[1:])), "evenly, end to end"
    assert times(fixed[:3] + fixed[463:]) == times(ds[:3] + ds[463:]) and collapsed(stepfun_asr.sentences(fixed)) == 0
    for at in (62644, 87400):  # the run inside a turn, and 2.5 s from any (10-15 on main: 6 rows of no length, S?)
        ds, turns = window(at)
        assert collapsed(stepfun_asr.merge(ds, turns)) == (at == 87400)
        got = stepfun_asr.merge(stepfun_asr.spread(ds, end), turns)
        assert collapsed(got) == 0 and not any(g["start_ms"] == g["end_ms"] for g in got if len(g["text"]) > 2)
        assert {(g["start_ms"], g["end_ms"]) for g in got if g.get("speaker")} == {(a, b) for a, b, _, _ in TURNS}, "every turn has text"
        assert chars(got) == chars(stepfun_asr.merge(ds, turns)) == len(TEXT.replace("|", "")), "no character lost"


def test_edges():
    run = lambda t, n: [d("字", t, t) for _ in range(n)]
    # at the start, nothing timed before it: from 0
    assert times(stepfun_asr.spread(run(500, 3) + [d("字", 900, 1000)], 2000)) == [(0, 300), (300, 600), (600, 900), (900, 1000)]
    # at the end: to the audio's end; the stream still going on: left out until a delta after it comes
    ds = [d("字", 100, 200)] + run(500, 3)
    assert times(stepfun_asr.spread(ds, 1100)) == [(100, 200), (200, 500), (500, 800), (800, 1100)]
    assert times(stepfun_asr.spread(ds)) == [(100, 200)]
    assert times(stepfun_asr.spread(ds, 400)) == times(ds), "the audio ends before the run's time: as it is"
    # the delta after starts on or before the run's time: as it is, no length below 0
    for after in (500, 400):
        ds = [d("字", 0, 100)] + run(500, 2) + [d("字", after, after + 80)]
        assert stepfun_asr.spread(ds, 2000) == ds
    # timeless deltas are skipped looking for the times around, and stay as they are
    ds = [d("字", 0, 100), d("，", None, None)] + run(500, 2) + [d("。", None, None), d("字", 900, 1000)]
    assert times(stepfun_asr.spread(ds, 2000)) == [(0, 100), (None, None), (100, 500), (500, 900), (None, None), (900, 1000)]
    # one delta of no length is no run
    ds = [d("字", 0, 100), d("字", 500, 500), d("字", 900, 1000)]
    assert stepfun_asr.spread(ds, 2000) == ds


def test_fast_speech():
    """A burst of fast speech gets one time for a dozen characters (13:38: 14 on 199060 ms between 198825 and 199300):
    spread over the 475 ms around it, the sentences stay the same."""
    ds = [d("字", 198510, 198590), d("字", 198590, 198825)] + [d("字", 199060, 199060) for _ in range(14)] + \
        [d("字。", 199300, 199300), d("字", 200400, 200480)]
    got = stepfun_asr.spread(ds, 263300)
    assert [(g["text"], g["start_ms"], g["end_ms"]) for g in stepfun_asr.sentences(got)] == \
        [(g["text"], g["start_ms"], g["end_ms"]) for g in stepfun_asr.sentences(ds)] == [("字" * 17 + "。", 198510, 199300), ("字", 200400, 200480)]
    assert 198825 <= min(x["start_time"] for x in got[2:16]) and max(x["end_time"] for x in got[2:16]) <= 199300


def test_sse_hands_them_spread(monkeypatch):
    """sse() spreads what it returns (to x's end) and what it hands on_sentence as the stream goes (a run at its end
    waits for the delta after it)."""
    monkeypatch.setenv("STEPFUN_API_KEY", "k")
    ev = lambda t, a, b: b"data: " + f'{{"type": "transcript.text.delta", "delta": "{t}", "start_time": {a}, "end_time": {b}}}'.encode() + b"\n\n"
    body = ev("字", 0, 100) + ev("字", 500, 500) + ev("字。", 500, 500) + ev("字", 900, 1000) + ev("字", 1500, 1500) + ev("字", 1500, 1500) + \
        b'data: {"type": "transcript.text.done"}\n\n'
    monkeypatch.setattr(net, "TRANSPORT", httpx2.MockTransport(lambda req: httpx2.Response(200, content=body)))
    seen = []
    got = anyio.run(stepfun_asr.sse, np.zeros(32000, np.int16), lambda ds: seen.append(times(ds)))
    assert seen == [[(0, 100)]], "the 。 closed a sentence inside a run with no delta after it yet"
    assert times(got) == [(0, 100), (100, 500), (500, 900), (900, 1000), (1000, 1500), (1500, 2000)]
