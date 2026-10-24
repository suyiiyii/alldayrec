"""A backlog does not hold back the drafts or the live analysis (10-06: a phone back after 3 h offline sent them at
once and the live page showed nothing new until all of it was final). While the final layer is still busy at the start
of a 3 h backlog and audio of now keeps coming, the drafts follow what is said now and the live analysis takes them as
the conversation going on; the backlog is never drafted (no engine call spent on it) nor analyzed live; once the final
layer goes on, it finalizes every utterance of the backlog in order, none lost, none twice, and the same live analysis
goes on without seeing a sentence twice. Real audio store, streams and pipeline; FireRed's own stream postprocessor on
frame energy for the VAD (no checkpoint in CI), a fixed voiceprint, fake engines that read back which tone they hear
and a fake LLM that adds a topic per turn."""
import datetime as dt
import re
import subprocess
import time

import anyio
import anyio.to_thread
import numpy as np
import sklearn.cluster  # noqa: F401  Diarist.add imports it on its first sentence: ~30 s in a new venv (bytecode), inside the waits below

import asr
import audio
import core
import db
import device_pipeline
import infer
import live
import monitor
import pipeline_state
import routes_upload
import squeeze
import summary
import ws
from tenant import T

SR, DEVICE = 16000, "LR-TEST"
BACKLOG_S, PIECE_S, LIVE_S = 3 * 3600, 60, 15  # the backlog in pieces of PIECE_S, a tone 10 s into each; then LIVE_S of now
N = BACKLOG_S // PIECE_S


class EnergyVad(infer.StreamVad):
    def detect_chunk(self, x):
        loud = np.lib.stride_tricks.sliding_window_view(np.abs(x.astype(np.int32)), 400)[::160].mean(1) > 500
        return [self.postprocessor.process_one_frame(float(v)) for v in loud]


def tone(idx: int, s: float) -> np.ndarray:
    """s seconds of a square wave whose amplitude is idx."""
    return (np.sign(np.sin(2 * np.pi * 500 * np.arange(int(s * SR)) / SR)) * (1000 + 50 * idx)).astype(np.int16)


def flac(x: np.ndarray) -> bytes:  # lossless: the amplitudes come back exact
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", "-f", "flac", "pipe:1"],
                          input=x.tobytes(), capture_output=True, check=True).stdout


def heard(x: np.ndarray, partial=None) -> list[dict]:
    """One sentence per tone, its text the tone's idx."""
    loud = np.abs(x[:len(x) // 160 * 160].astype(np.int32)).reshape(-1, 160).mean(1) > 500
    edges = np.flatnonzero(np.diff(np.r_[0, loud.astype(np.int8), 0]))
    return [{"start_ms": int(a) * 10, "end_ms": int(b) * 10, "speaker": "SPEAKER_0",
             "text": str(round((np.median(np.abs(x[a * 160:b * 160].astype(np.int32))) - 1000) / 50))}
            for a, b in zip(edges[::2], edges[1::2])]


async def hear(x: np.ndarray, partial=None) -> list[dict]:
    """heard() as a provider."""
    return heard(x)


def rig(tmp_path, monkeypatch, final_engine):
    """A pipeline's world: the energy VAD, fake engines and LLM, a page watching; returns what the draft engine was
    sent (seconds) and what the live analysis was fed (tone numbers per turn)."""
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    monkeypatch.setattr(squeeze, "LIFT_MAX_DB", 0.0)  # the fake engines read the tones' amplitudes: sent as they are
    monkeypatch.setattr(asr, "DRAFT_ENGINE", "stepfun-sse")
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "")
    monkeypatch.setattr(summary, "MIN_SPEECH_S", 3)
    monkeypatch.setattr(device_pipeline, "LIVE_EVERY_S", 1)
    sent, fed = {"draft": 0.0}, []

    async def llm(messages, timeout=None, kind=None):  # each turn's new sentences (their tone numbers), one topic added
        new = [int(t) for t in re.findall(r"^\[[\d:]{8}\] [^:]+: (\d+)$", messages[-1]["content"], re.M)]
        fed.append(new)
        return {"ops": [{"op": "add", "title": f"t{new[0]}"}]}, {"choices": [{"message": {"content": "{}"}}]}
    monkeypatch.setattr(live, "complete", llm)

    async def draft_engine(x, partial=None):
        sent["draft"] += len(x) / SR
        return heard(x)
    monkeypatch.setitem(asr.PROVIDERS, asr.ENGINE, final_engine)
    monkeypatch.setitem(asr.PROVIDERS, "stepfun-sse", draft_engine)
    np.savez(tmp_path / "owner-redimnet2.npz", owner=np.array([1.0, 0, 0, 0]))
    db.open_db(tmp_path / "live.db").watch_until = time.time() + 3600  # the tenant; a page of it watches
    monkeypatch.setitem(core.state, "embed", lambda x: np.array([0.0, 1, 0, 0]))
    return sent, fed


def save(t: dt.datetime, body: bytes):
    return audio.save(DEVICE, int(t.timestamp() * 1000), body, "audio/flac")


def st():
    return T().devices[DEVICE]


def test_backlog_does_not_hold_drafts(tmp_path, monkeypatch):
    busy = anyio.Event()  # the final layer's engine answers once busy is set

    async def final_engine(x, partial=None):
        await busy.wait()
        return heard(x)
    sent, fed = rig(tmp_path, monkeypatch, final_engine)
    silence = np.zeros(PIECE_S * SR, np.int16)
    backlog = [flac(np.concatenate([silence[:10 * SR], tone(i, 3), silence[13 * SR:]])) for i in range(N)]
    now = np.concatenate([np.concatenate([tone(N + j, 2), np.zeros(3 * SR, np.int16)]) for j in range(LIVE_S // 5)])
    now = [flac(now[k * SR:(k + 1) * SR]) for k in range(LIVE_S)]  # 1 s pieces, as an app in front sends them
    live0 = dt.datetime.fromtimestamp(round(time.time()) + 2)
    t0 = live0 - dt.timedelta(seconds=PIECE_S + BACKLOG_S)  # the backlog: t0 .. live0 - PIECE_S

    async def run():
        save(t0, backlog[0])  # the pipeline has started; the phone goes offline, then comes back with the rest at once
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            await anyio.sleep(1)
            async with anyio.create_task_group() as rest:
                rest.start_soon(anyio.to_thread.run_sync, lambda: [save(t0 + dt.timedelta(seconds=PIECE_S * i), b) for i, b in enumerate(backlog) if i])
                for k, body in enumerate(now):  # meanwhile what is said now, as it is said
                    await anyio.sleep(live0.timestamp() + k + 1 - time.time())
                    await anyio.to_thread.run_sync(save, live0 + dt.timedelta(seconds=k), body)
            for _ in range(20):  # the last tone's pause has just come in
                if {str(N + j) for j in range(LIVE_S // 5)} <= {r["text"] for r in st().drafts}:
                    break
                await anyio.sleep(0.5)
            drafts = db.drafts("", None, DEVICE)
            assert [r["text"] for r in drafts if int(r["text"]) >= N] == [str(N + j) for j in range(LIVE_S // 5)], drafts
            assert st().done_to is None or st().done_to < t0 + dt.timedelta(seconds=PIECE_S), "the final layer was to be busy"
            edge = live0 - dt.timedelta(seconds=PIECE_S + device_pipeline.DRAFT_BACK_S)
            assert all(r["start"] >= core.iso(edge) for r in drafts), "the backlog is not drafted"
            assert sent["draft"] < device_pipeline.DRAFT_BACK_S + LIVE_S, sent
            for _ in range(20):  # the analysis of the last pause's drafts
                if fed and fed[-1] and fed[-1][-1] == N + LIVE_S // 5 - 1:
                    break
                await anyio.sleep(0.5)
            lv = st().live
            assert lv and lv["start"] >= core.iso(edge) and lv["topics"], lv and live.public(lv)
            assert fed and fed[-1][-1] == N + LIVE_S // 5 - 1 and min(min(f) for f in fed) >= N - device_pipeline.DRAFT_BACK_S // PIECE_S - 1, fed

            busy.set()
            finals = lambda: db.sentences(core.iso(t0), core.iso(live0), source=DEVICE)
            for _ in range(240):
                if len(finals()) >= N:
                    break
                await anyio.sleep(0.5)
            assert [r["text"] for r in finals()] == [str(i) for i in range(N)], [r["text"] for r in finals()][:20]
            await anyio.sleep(3)  # the final layer's turn of the live analysis, now that the conversation is final
            assert st().live["start"] == lv["start"], "the same live analysis goes on"
            assert len(sum(fed, [])) == len(set(sum(fed, []))), fed
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_hold_keeps_backlog_final(tmp_path, monkeypatch):
    """The phone sends what is said now first and the backlog it reads off the card after it, each upload with hold =
    where now began: drafts follow now at once, finalization takes the whole backlog in order (none arrives
    behind it) and waits at the hold; once the phone lets go, now is finalized after it."""
    sent, fed = rig(tmp_path, monkeypatch, hear)
    n = 20  # 20 min of backlog
    silence = np.zeros(PIECE_S * SR, np.int16)
    backlog = [flac(np.concatenate([silence[:10 * SR], tone(i, 3), silence[13 * SR:]])) for i in range(n)]
    now = np.concatenate([np.concatenate([tone(n + j, 2), np.zeros(3 * SR, np.int16)]) for j in range(LIVE_S // 5)])
    now = [flac(now[k * SR:(k + 1) * SR]) for k in range(LIVE_S)]
    live0 = dt.datetime.fromtimestamp(round(time.time()) + 2)
    t0 = live0 - dt.timedelta(seconds=PIECE_S * (n + 1))
    h = int(live0.timestamp() * 1000)
    finals = lambda: [r["text"] for r in db.sentences(core.iso(t0), core.iso(live0 + dt.timedelta(seconds=LIVE_S)), source=DEVICE)]

    async def run():
        save(t0, backlog[0])  # final up to here before the phone went away
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            await anyio.sleep(1)
            late = []

            def card():  # the rest of the backlog, as the phone reads it off the card meanwhile
                for i in range(1, n):
                    time.sleep(0.2)
                    audio.hold(DEVICE, h)
                    t = t0 + dt.timedelta(seconds=PIECE_S * i)
                    if int(t.timestamp() * 1000) <= T().read_to.get(DEVICE, -1):
                        late.append(i)
                    save(t, backlog[i])
            async with anyio.create_task_group() as rest:
                for k, body in enumerate(now):
                    await anyio.sleep(live0.timestamp() + k + 1 - time.time())
                    audio.hold(DEVICE, h)
                    await anyio.to_thread.run_sync(save, live0 + dt.timedelta(seconds=k), body)
                    if k == 4:
                        rest.start_soon(anyio.to_thread.run_sync, card)
            for _ in range(60):
                if len(finals()) >= n and {str(n + j) for j in range(LIVE_S // 5)} <= {r["text"] for r in st().drafts}:
                    break
                await anyio.sleep(0.5)
            assert not late, f"backlog pieces behind the queue: {late}"
            assert finals() == [str(i) for i in range(n)], finals()
            assert {str(n + j) for j in range(LIVE_S // 5)} <= {r["text"] for r in st().drafts}, "drafts follow now meanwhile"
            assert sent["draft"] < device_pipeline.DRAFT_BACK_S + LIVE_S, sent
            assert monitor.device_status(DEVICE, st())["hold"] == live0.isoformat(timespec="seconds")
            await anyio.sleep(3)
            assert finals() == [str(i) for i in range(n)], "now waits at the hold"

            audio.hold(DEVICE, None)  # the next upload says nothing older is coming
            db.put_kv(f"end_at:{DEVICE}", core.iso(live0 + dt.timedelta(seconds=LIVE_S)))  # and the recorder stops
            want = [str(i) for i in range(n + LIVE_S // 5)]
            for _ in range(60):
                if finals() == want:
                    break
                await anyio.sleep(0.5)
            assert finals() == want, finals()
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_hold_expires(tmp_path, monkeypatch):
    """A phone that never comes back: a hold not said again for HOLD_MAX_S is let go and the queue goes on; older audio
    arriving after that is kept, not finalized (as before there were holds)."""
    monkeypatch.setattr(audio, "HOLD_MAX_S", 2)
    db.open_db(tmp_path / "live.db")
    t = lambda m: dt.datetime(2026, 10, 6, 10, m)
    piece = flac(tone(0, 3))
    save(t(0), piece)
    audio.hold(DEVICE, int(t(5).timestamp() * 1000))
    save(t(5), piece)

    async def run():
        s, got = audio.stream(DEVICE, None), []
        got.append((await anext(s))[0])
        send, recv = anyio.create_memory_object_stream()

        async def nxt():  # the reader back for more
            await send.send((await anext(s))[0])
        with send, recv:
            async with anyio.create_task_group() as tg:
                tg.start_soon(nxt)
                with anyio.move_on_after(1) as wait:
                    got.append(await recv.receive())
                assert wait.cancelled_caught, "held"
                with anyio.fail_after(5):
                    got.append(await recv.receive())
                assert got == [t(0), t(5)]
                tg.start_soon(nxt)  # 10:05 is done
                await anyio.sleep(0.5)
                assert audio.held(DEVICE) is None and int(t(3).timestamp() * 1000) <= T().read_to[DEVICE], "10:03 now comes too late"
                tg.cancel_scope.cancel()

    anyio.run(run)



def test_final_goes_on_after_a_gap(tmp_path, monkeypatch):
    """A recording stopped and finalized to its very end, the next one after a pause (10-07: the iPhone's microphone up
    to 17:58:55, then from 19:09): the first cut after the finalized time fell in the pause, a piece of no samples, and
    the final layer waited on it for good (370 pieces queued; restarts did not help). It goes on over the pause, in the
    same pipeline and in one started after it."""
    rig(tmp_path, monkeypatch, hear)
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 900)
    at = lambda k: t0 + dt.timedelta(seconds=180 * k)  # a recording every 3 min
    finals = lambda: [r["text"] for r in db.sentences(core.iso(t0), core.iso(at(5)), source=DEVICE)]
    stop = lambda t: db.put_kv(f"end_at:{DEVICE}", core.iso(t))

    async def until(want):
        for _ in range(60):
            if finals() == want:
                return
            await anyio.sleep(0.5)
        assert finals() == want, (finals(), st().done_to)

    async def run():
        save(at(0), flac(np.concatenate([tone(0, 2), np.zeros(3 * SR, np.int16)])))  # 5 s, then stopped
        stop(at(0) + dt.timedelta(seconds=5))
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            await until(["0"])
            assert st().done_to == at(0) + dt.timedelta(seconds=5), "finalized to the very end of the recording"
            # 45 s in 1 s pieces, quiet at first (the VAD pads an utterance's start back: across the pause into the
            # recording before, it bridged the pause), a pause after the tone ends the final piece
            x = np.concatenate([np.zeros(5 * SR, np.int16), tone(1, 2), np.zeros(38 * SR, np.int16)])
            for k in range(45):
                save(at(1) + dt.timedelta(seconds=k), flac(x[k * SR:(k + 1) * SR]))
            await until(["0", "1"])
            stop(at(1) + dt.timedelta(seconds=45))
            for _ in range(20):
                if st().done_to == at(1) + dt.timedelta(seconds=45):
                    break
                await anyio.sleep(0.5)
            assert st().done_to == at(1) + dt.timedelta(seconds=45), st().done_to
            tg.cancel_scope.cancel()
        save(at(2), flac(np.concatenate([tone(2, 2), np.zeros(3 * SR, np.int16)])))  # while the server restarts
        stop(at(2) + dt.timedelta(seconds=5))
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)  # resumes at the end of a recording, the next after a pause
            await until(["0", "1", "2"])
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_final_goes_on_after_a_recording_of_odd_length(tmp_path, monkeypatch):
    """As above, with recordings not a whole number of milliseconds long (imported files), the next one's speech
    going on at the newest audio: the final layer takes the next recording from where the last one ended, not a sample
    or a few short of it, in the same pipeline and in one started after it (10-24: the finalized time came back from a
    restart cut to the millisecond; the 11 samples after it went to the engine alone, and the local engine, which fails
    on less than 0.1 s, was asked again every 10 s for good). A store finalized to the millisecond before the fix goes
    on too."""
    short = []

    async def engine(x, partial=None):
        if len(x) < SR // 10:
            short.append(len(x))
            raise RuntimeError("Invalid input shape: {0,80}")  # local_asr's on that little audio
        return heard(x)
    rig(tmp_path, monkeypatch, engine)
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 900)
    at = lambda k: t0 + dt.timedelta(seconds=180 * k)  # a recording every 3 min, 4 s and 11 samples long
    finals = lambda: [r["text"] for r in db.sentences(core.iso(t0), core.iso(at(5)), source=DEVICE)]

    async def until(want):
        for _ in range(60):
            if finals() == want:
                return
            await anyio.sleep(0.5)
        assert finals() == want, (finals(), st().done_to, short)

    async def record(k: int):
        """Recording k (tones 2k and 2k + 1) in two pieces: the first ends in speech (more of it is to come, as far as
        the VAD knows), the second is quiet at its end; then the recorder stops. Its end comes back 1 sample early
        from at()'s float and the clock's int, 11 from the store's milliseconds."""
        quiet = lambda s: np.zeros(int(s * SR), np.int16)
        save(at(k), flac(np.concatenate([quiet(0.25), tone(2 * k, 0.75), quiet(1.25), tone(2 * k + 1, 0.25)])))
        await anyio.sleep(3)
        save(at(k) + dt.timedelta(seconds=2.5), flac(np.concatenate([tone(2 * k + 1, 0.25), quiet(1.25), np.zeros(11, np.int16)])))
        db.put_kv(f"end_at:{DEVICE}", core.iso(at(k) + dt.timedelta(seconds=4 + 11 / SR)))
        await until([str(i) for i in range(2 * k + 2)])

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            await record(0)
            await record(1)
            tg.cancel_scope.cancel()
        async with anyio.create_task_group() as tg:  # restarted
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            await record(2)
            tg.cancel_scope.cancel()
        key = f"finalized_to:{DEVICE}"  # as a store written before the fix keeps it
        db.put_kv(key, core.iso(dt.datetime.fromisoformat(db.kv(key))))
        async with anyio.create_task_group() as tg:  # restarted on it
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            await record(3)
            tg.cancel_scope.cancel()
        assert not short, short

    anyio.run(run)


def test_hold_release(tmp_path, monkeypatch):
    """The phone no longer has the recorder (unbound, or another bound): the hold goes at once, not HOLD_MAX_S after
    the phone last said it (10-07: LR-P3-116C's 261 pieces waited from 17:47 to 05:47). Meanwhile the status says when
    the phone last said it and until when the queue waits without it, and whether the device is recording now."""
    db.open_db(tmp_path / "live.db")
    monkeypatch.setattr(ws, "note", lambda *a, **k: None)
    t = dt.datetime(2026, 10, 7, 17, 36)
    save(t, flac(tone(0, 3)))  # it came just now: the device records
    audio.hold(DEVICE, int(t.timestamp() * 1000))
    st = pipeline_state.PipelineState()
    d = monitor.device_status(DEVICE, st)
    assert d["hold"] == "2026-10-07T17:36:00" and d["recording"], d
    seen, until = dt.datetime.fromisoformat(d["hold_seen"]), dt.datetime.fromisoformat(d["hold_until"])
    assert abs((seen - dt.datetime.now()).total_seconds()) < 5 and (until - seen).total_seconds() == audio.HOLD_MAX_S
    monkeypatch.setattr(device_pipeline, "LIVE_AUDIO_S", 0)
    assert not monitor.device_status(DEVICE, st)["recording"], "no audio came in LIVE_AUDIO_S: not recording"

    anyio.run(routes_upload.hold_release, DEVICE)
    d = monitor.device_status(DEVICE, st)
    assert d["hold"] is None and d["hold_seen"] is None and audio.held(DEVICE) is None


def test_pipeline_cursors_and_pause_notifications_are_independent():
    """Final commits do not advance drafts; a pause during a draft wakes the next pass."""
    async def run():
        t = dt.datetime(2026, 10, 7, 17, 36)
        a, b = pipeline_state.PipelineState(draft_to=t), pipeline_state.PipelineState()
        a.progress = [(0.0, t), (599.0, t)]
        a.finalized(t + dt.timedelta(seconds=300), 600.0)
        assert a.done_to == t + dt.timedelta(seconds=300) and a.draft_to == t
        assert a.progress == [(599.0, t), (600.0, a.done_to)]
        assert b.done_to is None and b.progress == []
        a.wake.set()
        assert a.take_draft_wake()
        assert not a.wake.is_set() and not b.wake.is_set()
        a.wake.set()  # a new pause while the current pass runs
        assert a.take_draft_wake()
        assert not a.take_draft_wake()
    anyio.run(run)
