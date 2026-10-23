"""Live transcript of the phone's recording.

    set -a; . .env; set +a; uv run python server.py [--port 8770]   (variables: .env.example; tenants: ../tenants.json, tenant.py)

Takes the audio pieces the app and the /record page upload, keeps them on disk as a queue and feeds them in time
order (only as fast as finalization keeps up), finds speech with FireRed Stream-VAD and transcribes in two layers:
final pieces of up to CHUNK_S cut in pauses (sentences, conversations, summaries), and while a page watches, drafts at
each pause for the minutes not final yet; all state is in data/live.db.
"""
import argparse
import base64
import contextlib
import copy
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import time
from pathlib import Path

# numpy's and scipy's OpenBLAS start a thread per CPU when they load (31 on the test box): audio.health() on every upload
# woke them to spin in sched_yield, 63 % of the server's CPU over 74.7 h; with 1 its 60 pieces took 0.15 s sys, not
# ~150 s, and less wall time. Set before the first import that loads numpy (all below); .env still overrides it.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import anyio
import anyio.to_thread
import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.requests import HTTPConnection

import apk
import ask
import audio
import db
import firmware
import hints
import infer
import live
import local_asr
import mcp_server
import net
import profiles
import qwen_asr
import qwen_stream
import recap
import selfvoice
import speakers
import squeeze
import stepfun_asr
import summary
import tenant
import threads
from conversations import IGNORE, Grouper
from pipeline_state import PipelineState
from tenant import T

HERE = Path(__file__).resolve().parent


SR = 16000
FRAME_LEN, FRAME_SHIFT = 400, 160          # FireRed fbank: 25 ms window, 10 ms shift
MERGE_GAP_S = 0.6                          # VAD utterances closer than this are one
MAX_UTTERANCE_S = 15.0
VAD_CUT_S = 0.5                            # a VAD-driven pass ends this far into the pause (< MERGE_GAP_S: still silence)
CHUNK_S = 300.0                            # final layer: most audio in one piece (one engine call; 15 min in one call timed out)
QUIET_S = 30.0                             # a pause this long ends a final piece early, 15 s into it
CUT_SEARCH_S = 60.0                        # a full piece ends in the widest pause of its last CUT_SEARCH_S
REFINE_EVERY_S = int(os.environ.get("REFINE_EVERY_S", "30"))  # draft layer: a pass after this long without a pause
REFINE_WINDOW_S = int(os.environ.get("REFINE_WINDOW_S", "240"))    # draft layer: most audio one pass takes
REFINE_MARGIN_S = 1.0                      # audio re-read before the last drafted time: the engine's sentence starts come a little late
DRAFT_BACK_S = 300                         # a page that opens gets drafts this far back at most (the minutes not final yet)
RELABEL_GROUPS = True                      # relabel at a close: the engine's speaker ids in a piece as units, split when their voices disagree
SLICE_FOREGROUND_S = int(os.environ.get("SLICE_FOREGROUND_S", "1"))   # the apps' piece seconds with the app in front: 1 s
SLICE_BACKGROUND_S = int(os.environ.get("SLICE_BACKGROUND_S", "20"))  # ... in the background             (drafts ~8 s vs ~13 s at 5 s)
SLICE_SCREEN_OFF_S = int(os.environ.get("SLICE_SCREEN_OFF_S", "60"))  # ... screen off (Android)
LIVE_AUDIO_S = 120                         # a device whose audio came this recently is recording now: drafts and live analysis
ENGINE = os.environ.get("ENGINE") or "qwen"  # final layer's ASR provider (PROVIDERS): volc | qwen | stepfun | stepfun-sse | stepfun-file | local
DRAFT_ENGINE = os.environ.get("DRAFT_ENGINE") or ENGINE  # draft layer's (stepfun-sse: fast, no speakers, drafts are replaced anyway;
                                                         # qwen-stream: one streaming session while a page watches, stream_loop)
ENGINE_FALLBACK = os.environ.get("ENGINE_FALLBACK", "")  # a window the provider fails on (402, 451, 5xx, timeout) goes here; empty: none
SQUEEZE_KEEP_S = float(os.environ.get("SQUEEZE_KEEP_S", "3"))  # VAD silences longer than this go to the engine cut to this; 0: whole
SQUEEZE_VAD = float(os.environ.get("SQUEEZE_VAD", "0.3"))  # ... silences by this VAD threshold: 0.5 (cutting pieces) misses short replies
BUFFER_S = 900                             # audio kept for refinement; pieces are fed only while finalization is within BUFFER_S - 300
LIVE_EVERY_S = int(os.environ.get("LIVE_EVERY_S", "120"))  # new speech in the open conversation before the live analysis is redone
TURN_GAP_S = float(os.environ.get("TURN_GAP_S", "1.0"))  # same speaker, closer than this: one final row; 0 keeps the engine's sentences as rows
EDGE_S = 3.0                               # rows touching the window edges may be cut: left to the next pass
TENANTS: list[tenant.Tenant] = []  # every tenant of this process (tenant.load at the start); the first one's data has the
                                   # firmware, the APK and the StepFun balance, shared by all

log = logging.getLogger("live")




class Segmenter:
    """FireRed Stream-VAD over a continuous sample stream; emits (start, end, samples) utterances."""

    def __init__(self):
        self.vad = infer.StreamVad()
        self.loose = copy.deepcopy(self.vad.postprocessor)  # the same frames at SQUEEZE_VAD: only what the engine is sent
        self.loose.speech_threshold = SQUEEZE_VAD
        self.speech: list[tuple[int, int]] = []  # its speech in the buffer (stream samples)
        self.buf = np.zeros(0, np.int16)      # samples not yet turned into frames
        self.audio = np.zeros(0, np.int16)    # recent samples for cutting utterances
        self.audio_base = 0                   # stream sample index of audio[0]
        self.anchors: list[tuple[int, dt.datetime]] = []  # (stream sample index, wall time)
        self.samples = 0
        self.frames = 0
        self.pending = None                   # utterance waiting for a possible merge

    def time_at(self, sample: int) -> dt.datetime:
        idx, t = next((a for a in reversed(self.anchors) if a[0] <= sample), self.anchors[0])
        return t + dt.timedelta(seconds=(sample - idx) / SR)

    def push(self, t: dt.datetime, x: np.ndarray) -> list:
        if not self.anchors or abs((self.time_at(self.samples) - t).total_seconds()) > 0.5:
            self.anchors.append((self.samples, t))  # new file or a gap: re-anchor the clock
        self.samples += len(x)
        self.audio = np.concatenate([self.audio, x])
        self.buf = np.concatenate([self.buf, x])
        out = []
        if len(self.buf) >= FRAME_LEN:
            k = (len(self.buf) - FRAME_LEN) // FRAME_SHIFT + 1
            # Only whole frames, keeping the overlap for the next call: splitting at arbitrary
            # sample counts drops frames at every boundary and the clock drifts ~2 %.
            for r in self.vad.detect_chunk(self.buf[:(k - 1) * FRAME_SHIFT + FRAME_LEN]):
                q = self.loose.process_one_frame(r.raw_prob)
                if q.is_speech_end:
                    self.speech.append(((q.speech_start_frame - 1) * FRAME_SHIFT, (q.speech_end_frame - 1) * FRAME_SHIFT))
                if r.is_speech_end:
                    out += self._utterance(r.speech_start_frame - 1, r.speech_end_frame - 1)
            self.frames += k
            self.buf = self.buf[k * FRAME_SHIFT:]
        out += self._flush_if_quiet()
        keep = self.samples - BUFFER_S * SR
        if keep > self.audio_base:
            self.audio = self.audio[keep - self.audio_base:]
            self.audio_base = keep
            self.speech = [u for u in self.speech if u[1] > keep]
        return out

    def speaking(self) -> bool:
        """An utterance is still open (the VAD is in speech, or one waits for a merge): last_speech does not cover it yet."""
        return self.pending is not None or self.vad.postprocessor.state.name != "SILENCE"

    def open_speech(self) -> int | None:
        """Stream sample where the loose VAD's speech still running started (None in silence)."""
        p = self.loose
        if p.state.name == "SILENCE":
            return None
        f = p.frame_cnt - p.speech_cnt + 1 - p.pad_start_frame if p.state.name == "POSSIBLE_SPEECH" else p.last_speech_start_frame
        return max(0, f - 1) * FRAME_SHIFT

    def sample_at(self, t: dt.datetime) -> int:
        i = next((k for k in range(len(self.anchors) - 1, -1, -1) if self.anchors[k][1] <= t), 0)
        idx, t0 = self.anchors[i]
        s = idx + int((t - t0).total_seconds() * SR)
        # a time in the gap before the next session has no audio: the first sample after the gap
        return min(s, self.anchors[i + 1][0]) if i + 1 < len(self.anchors) else s

    def window(self, seconds: float, start: dt.datetime | None = None) -> tuple[dt.datetime, np.ndarray]:
        """Up to `seconds` of audio from `start` (default: the last `seconds` up to now)."""
        if start is None:
            a = max(self.audio_base, self.samples - int(seconds * SR))
        else:
            a = min(max(self.audio_base, self.sample_at(start)), self.samples)
        b = min(self.samples, a + int(seconds * SR))
        return self.time_at(a), self.audio[a - self.audio_base:b - self.audio_base]

    def _utterance(self, f0: int, f1: int) -> list:
        a, b = f0 * FRAME_SHIFT, f1 * FRAME_SHIFT
        p = self.pending
        if p and (a - p[1]) / SR < MERGE_GAP_S and (b - p[0]) / SR <= MAX_UTTERANCE_S:
            self.pending = (p[0], b)
            return []
        out = self._emit() if p else []
        self.pending = (a, b)
        return out

    def _flush_if_quiet(self) -> list:
        if self.pending and (self.frames * FRAME_SHIFT - self.pending[1]) / SR >= MERGE_GAP_S \
                and self.vad.postprocessor.state.name == "SILENCE":
            return self._emit()
        return []

    def _emit(self) -> list:
        a, b = self.pending
        self.pending = None
        x = self.audio[max(0, a - self.audio_base):max(0, b - self.audio_base)]
        return [(self.time_at(a), self.time_at(b), x)] if len(x) else []


async def _http(url, data=None, headers=None, method=None) -> bytes:
    """urllib.request's way: POST when there is data, else GET; raises urllib's errors (net)."""
    r = await net.request(method or ("GET" if data is None else "POST"), url, 60, content=data,
                          headers={"User-Agent": "curl/8", **(headers or {})})
    return r.content


FILE_MODELS = tuple(os.environ.get("FILE_MODELS", "stepaudio-2.5-asr-8006,doubao-seedasr-2.0").split(","))  # file-mode engines, in order of preference


def sent_audio(x: np.ndarray, speech: list[tuple[float, float]]) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """What a provider gets of a window and the spans unsqueeze maps its times back by: x with the VAD silences squeezed
    out, lifted where it is quiet (squeeze.py)."""
    xs, spans = squeeze.squeeze(x, speech, SQUEEZE_KEEP_S)
    return squeeze.lift(xs), spans


async def engine_segs(x: np.ndarray, speech: list[tuple[float, float]], partial=None, engine: str = ENGINE, kind: str = "final",
                      device: str | None = None) -> list[dict]:
    """Sentences with times and speaker ids for a window of audio, from `engine` (PROVIDERS), or ENGINE_FALLBACK when
    it fails; each carries `engine`, and `fallback` (why) when it came from ENGINE_FALLBACK. speech: the VAD speech in
    x, (start, end) seconds; the provider gets sent_audio (silences squeezed, quiet audio lifted), the times come back in x.
    partial(segs): the sentences so far, whenever an answer adds some during the call (volc, stepfun*). Each try goes
    in the calls table as `kind` (final, draft) of `device`, one cut off (the task cancelled) too, as CancelledError."""
    xs, spans = sent_audio(x, speech)
    why = None
    for name in dict.fromkeys(filter(None, (engine, ENGINE_FALLBACK))):
        mark = lambda gs, name=name: squeeze.unsqueeze([{**g, "engine": name, **({"fallback": why} if why else {})} for g in gs], spans)
        t = time.perf_counter()
        try:
            segs = await PROVIDERS[name](xs, partial and (lambda gs: partial(mark(gs))))
        except BaseException as e:
            e = threads.first(e)  # stepfun and volc run parts of a call side by side: their error as before
            db.add_call(kind, name, device, round((time.perf_counter() - t) * 1000), failure(e), len(x) / SR, len(xs) / SR)
            if not isinstance(e, Exception):  # cancelled: no fallback
                raise
            log.warning("asr %s: %.0f of %.0f s sent, failed after %.1f s: %r", name, len(xs) / SR, len(x) / SR, time.perf_counter() - t, e)
            why = (f"{why}; " if why else "") + f"{name}: {e!r}"[:200]
            continue
        db.add_call(kind, name, device, round((time.perf_counter() - t) * 1000), None, len(x) / SR, len(xs) / SR)
        log.info("asr %s: %.0f of %.0f s sent, %.1f s, %d sentences%s", name, len(xs) / SR, len(x) / SR, time.perf_counter() - t,
                 len(segs), f" (fallback; {why})" if why else "")
        return mark(segs)
    raise RuntimeError(why)


def failure(e: Exception) -> str:
    """An error as the calls table keeps it: its class and first words (an HTTP code says itself), no response body."""
    return f"{type(e).__name__}: {e}"[:120]


async def volc(x: np.ndarray, partial=None) -> list[dict]:
    import volc_asr
    mark = lambda us: [{"speaker": "SPEAKER_0", **u} for u in us]
    return mark((await volc_asr.transcribe(x, partial=partial and (lambda us: partial(mark(us)))))[0])


async def stepfun_sse(x: np.ndarray, partial=None) -> list[dict]:
    """StepFun's SSE alone: text and character times, sentences cut by punctuation and pauses, no speakers."""
    return stepfun_asr.sentences(await stepfun_asr.sse(x, partial and (lambda ds: partial(stepfun_asr.sentences(ds)))))


async def stepfun(x: np.ndarray, partial=None) -> list[dict]:
    """StepFun's SSE text on the sentences and speakers of a file job on the same audio, both at once: either
    failing cancels the other (an SSE failure goes to the fallback at once), raised in an ExceptionGroup."""
    file = []

    async def job():
        file.append(await diarize(x))
    async with anyio.create_task_group() as tg:
        tg.start_soon(job)
        deltas = await stepfun_asr.sse(x, partial and (lambda ds: partial(stepfun_asr.sentences(ds))))
    return stepfun_asr.merge(deltas, file[0])


async def stepfun_file(x: np.ndarray, partial=None) -> list[dict]:
    return await diarize(x)


async def qwen(x: np.ndarray, partial=None) -> list[dict]:
    """Bailian's Qwen-Audio 3.1 file job (qwen_asr): text, sentences and speakers at once, no partial answers."""
    return await qwen_asr.transcribe(x)


async def local(x: np.ndarray, partial=None) -> list[dict]:
    """sherpa-onnx X-ASR in this process (local_asr): text and token times, sentences as stepfun-sse's, no speakers
    (the diarist's), no network, no partial answers."""
    return await local_asr.transcribe(x)


# provider(x: 16 kHz int16 PCM, partial=None) -> [{start_ms, end_ms, text, speaker?, words?}], times in x; async
PROVIDERS = {"volc": volc, "qwen": qwen, "stepfun": stepfun, "stepfun-sse": stepfun_sse, "stepfun-file": stepfun_file, "local": local}


async def upload(data: bytes, content_type: str) -> str:
    """A file up through the upload bridge (ASR_UPLOAD_BRIDGE_URL: POST {filename, content_type} -> {upload_url,
    download_url}, then a presigned PUT); its download URL, for a provider to fetch."""
    up = json.loads(await _http(os.environ["ASR_UPLOAD_BRIDGE_URL"], json.dumps({"filename": "live." + content_type.split("/")[1], "content_type": content_type}).encode(),
                                {"Authorization": f"Bearer {os.environ['ASR_UPLOAD_BRIDGE_TOKEN']}", "Content-Type": "application/json"}))
    await _http(up["upload_url"], data, {"Content-Type": content_type}, "PUT")
    return up["download_url"]


async def diarize(x: np.ndarray, model: str = FILE_MODELS[0]) -> list[dict]:
    """File-mode ASR with speakers and word times on a window of audio (via the file-mode ASR service, ASR_SERVICE_URL:
    POST /audio/transcriptions, GET /jobs/{id}; stepfun and stepfun-file only, main() checks its settings).
    StepFun's risk control refuses some content outright; that window is then sent to Doubao and every
    segment carries `engine` so the rows say which engine wrote them."""
    t_enc = time.perf_counter()
    async with threads.MEDIA:
        ogg = (await anyio.run_process(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                                        "-c:a", "libopus", "-b:a", "16k", "-f", "ogg", "pipe:1"], input=x.tobytes())).stdout
    t_up = time.perf_counter()
    url = await upload(ogg, "audio/ogg")
    auth = {"Authorization": "Basic " + base64.b64encode(os.environ["ASR_SERVICE_BASIC_AUTH"].encode()).decode()}
    service = os.environ["ASR_SERVICE_URL"]
    job = json.loads(await _http(f"{service}/audio/transcriptions", json.dumps({"audio_url": url, "model": model}).encode(),
                                 {**auth, "Content-Type": "application/json"}))["job_id"]
    t_job = time.perf_counter()
    for _ in range(120):
        r = json.loads(await _http(f"{service}/jobs/{job}", headers=auth))
        if r["status"] == "COMPLETED":
            log.info("diarize %s: %.0f s audio, encode %.1f s, upload %.1f s, job %.1f s", model, len(x) / SR,
                     t_up - t_enc, t_job - t_up, time.perf_counter() - t_job)
            for g in r["segments"]:
                g["engine"] = model.split("-")[0]
            return r["segments"]
        if r["status"] == "FAILED":
            err = str(r.get("error"))
            if "risk" in err and model == FILE_MODELS[0]:
                log.warning("StepFun refused the window (%s); sending it to Doubao", err[:80])
                return await diarize(x, FILE_MODELS[1])
            raise RuntimeError(err)
        await anyio.sleep(1)
    raise RuntimeError(f"diarization job {job} still running after 120 s")


CLIP_JITTER_MS = 300  # a re-cut turn's start moves about this much between passes


def clip_turns(segs: list[dict], since_s: float) -> list[dict]:
    """The previous pass finalized every turn starting before `since` (its `to` is the start of the first
    turn it left out), so those are dropped here, whole: keeping the part after `since` re-emitted the
    sentence of a speaker overlapping the cut turn (phone case 09-29 13:12). Only a turn starting within
    the re-cut jitter of `since` is kept, clipped to it."""
    out = []
    for g in segs:
        if g["start_ms"] < since_s * 1000 - CLIP_JITTER_MS or g["end_ms"] <= since_s * 1000 + 200:
            continue
        if g["start_ms"] < since_s * 1000:
            g = {**g, "start_ms": int(since_s * 1000)}
        out.append(g)
    return out


def label_sentences(dia, embed, segs: list[dict], x: np.ndarray) -> list:
    """Speaker labels from the conversation clustering, in time order; returns the diarist mark after each
    sentence (None when too short to embed) so a pass can take back the sentences it leaves open."""
    marks = []
    for g in segs:
        a, b = g["start_ms"] / 1000, g["end_ms"] / 1000
        g["engine_speaker"] = g.get("speaker")
        if b - a < 0.3:
            g["speaker"], g["confidence"] = "S?", 0.0
            marks.append(None)
            continue
        g["speaker"], g["confidence"] = dia.add(embed(x[int(a * SR):int(min(b, a + 15) * SR)]), b - a)
        marks.append(dia.mark())
    return marks


def final_rows(segs: list[dict], names: dict, t0: dt.datetime, win_s: float, conf: dict | None = None,
               since: float = EDGE_S, at=None, edge: float = EDGE_S) -> tuple[dt.datetime, dt.datetime, list[dict]]:
    """Second pass: the diarized speaker turns of one window become the final rows for [from, to).

    VAD utterances glue fast back-and-forth into one line (no pause to cut at), so the final
    layer is built from the diarized turns instead. Consecutive pieces of one speaker are joined;
    rows near the window edges may be cut mid-turn and are left for the next, later window.
    `since` (seconds into the window) is where the previous pass stopped: rows before it are
    already final and never rewritten. `at` maps seconds into the window to wall time; the default
    is linear from t0, the live server passes the segmenter's clock (a new session's file starting
    inside the window jumps it). `edge` is how close to the window end a row may end: 0 when the window ends in
    a pause the VAD saw.
    """
    rows = []
    for g in sorted(segs, key=lambda g: g["start_ms"]):
        a, b, who = g["start_ms"] / 1000, g["end_ms"] / 1000, names[g["speaker"]]
        if rows and rows[-1]["speaker"] == who and a - rows[-1]["b"] < TURN_GAP_S:
            rows[-1]["b"], rows[-1]["text"] = b, rows[-1]["text"] + g["text"]
        else:
            rows.append(dict(a=a, b=b, speaker=who, text=g["text"]))
    lo, hi = since, win_s - edge
    keep = [r for r in rows if r["a"] >= lo - 0.05]  # turns are clipped to `since` by the caller
    cut = next((i for i, r in enumerate(keep) if r["b"] > hi), len(keep))
    to = keep[cut]["a"] if cut < len(keep) else hi
    at = at or (lambda s: t0 + dt.timedelta(seconds=s))
    out = [dict(id="f" + at(r["a"]).strftime("%Y%m%d%H%M%S%f")[:17], start=at(r["a"]).isoformat(timespec="milliseconds"),
                end=at(r["b"]).isoformat(timespec="milliseconds"), speaker=r["speaker"], text=r["text"].strip(),
                speaker_confidence=(conf or {}).get(r["speaker"]), state="final")
           for r in keep[:cut] if r["text"].strip()]
    return at(lo), at(to), out


def next_cut(utts: list[tuple[float, float]], end: float, speaking: bool, force: bool = False) -> float | None:
    """Where the next final piece ends, in seconds after the finalized time, or None to wait for more audio.

    utts: VAD utterances (start, end) after the finalized time, in time order; end: the audio fed so far; speaking:
    speech runs on at `end` (its start may be unknown). The piece ends QUIET_S / 2 into the first pause of QUIET_S
    (leading and trailing silence count), else, once CHUNK_S of audio is there, in the middle of the widest pause of its
    last CUT_SEARCH_S (CHUNK_S itself when there is none). force (the recorder stopped, all audio in): at `end`, even
    in speech: no more audio comes, and the VAD may never see the end of an utterance the recording cut off.
    Otherwise every cut lies in known silence or at CHUNK_S, so the engine always has the audio after a sentence.
    """
    bounds = [(0.0, 0.0)] + utts
    gaps = [(b1, a2) for (_, b1), (a2, _) in zip(bounds, bounds[1:])]
    if not speaking:
        gaps.append((bounds[-1][1], end))
    for lo, hi in gaps:
        if lo + QUIET_S / 2 > CHUNK_S:
            break
        if hi - lo >= QUIET_S:
            return lo + QUIET_S / 2
    if end >= CHUNK_S:
        near = [(hi - lo, (lo + hi) / 2) for lo, hi in gaps if CHUNK_S - CUT_SEARCH_S <= (lo + hi) / 2 <= CHUNK_S and hi > lo]
        return max(near)[1] if near else CHUNK_S
    if force and end > 0.05:
        return end
    return None


def speech_in(seg: Segmenter, a0: int, n: int) -> list[tuple[float, float]]:
    """The loose VAD's speech (SQUEEZE_VAD) in the n samples from stream sample a0, in seconds into them, the speech
    still running too."""
    o = seg.open_speech()
    return [((a - a0) / SR, (b - a0) / SR) for a, b in seg.speech + ([(o, a0 + n)] if o is not None else [])]


def overlaps(lv: dict, c: dict) -> bool:
    """A live analysis (start to its last analyzed row, upto) and a conversation (start to end) share time: drafts and
    final rows give the same conversation slightly different starts."""
    return lv["start"] <= c["end"] and lv.get("upto", lv["start"]) >= c["start"]


def merge_drafts(finals: list[dict], drafts: list[dict], final_to: dict[str, str | None]) -> list[dict]:
    """What a page shows: final rows, and per device the drafts after its finalized time (before it, final rows
    cover the time; the drafts stay in the table for comparing the layers)."""
    return sorted(finals + [r for r in drafts if r["start"] >= (final_to.get(r.get("source")) or "")], key=lambda r: r["start"])


@contextlib.asynccontextmanager
async def lifespan(app):
    """The process's tasks (the voiceprint model, the metrics, the balance) and each tenant's (run_tenant), until the
    server stops: then all of them are cancelled."""
    async with anyio.create_task_group() as tg:
        tg.start_soon(load_model, name="model")
        tg.start_soon(supervised, "metrics", metrics_loop, name="metrics")
        if os.environ.get("STEPFUN_API_KEY"):
            tg.start_soon(supervised, "balance", balance_loop, name="balance")
        for t in TENANTS:
            tg.start_soon(run_tenant, t, name=f"tenant:{t.id}")
        yield
        tg.cancel_scope.cancel()


app = FastAPI(lifespan=lifespan)
app.include_router(firmware.router)  # the recorders' firmware packages (firmware.DIR: the first tenant's, set by main)
state = {"commit": "", "embed": None}  # the process's: what runs, and the voiceprint model every tenant uses
MODEL = anyio.Event()  # the model is loaded


async def load_model():
    from diarist import load_embedder
    state["embed"] = await anyio.to_thread.run_sync(load_embedder, limiter=threads.INFER)
    MODEL.set()


async def run_tenant(t: tenant.Tenant):
    """A tenant's loops, each started again after a failure; the tenant's pipelines and live analyses run in its task
    group too, so cancelling t.scope stops all of it."""
    tenant.enter(t)
    t.recordings.mkdir(parents=True, exist_ok=True)
    with anyio.CancelScope() as t.scope:
        async with anyio.create_task_group() as t.tg:
            for name, fn in (("pipeline", pipelines), ("status", status_loop), ("summary", summary_worker), ("voices", voices_loop),
                             *((("recap", recap_loop),) if RECAP_AT else ())):
                t.tg.start_soon(supervised, name, fn, name=f"{name}:{t.id}")


async def supervised(name: str, fn):
    """fn for good: one that ends (a bug, a returned loop) is logged and started again RESTART_S later, without the
    tasks beside it (a TaskGroup would otherwise cancel them all)."""
    while True:
        try:
            await fn()
            log.error("task %s ended: returned", name)
        except Exception as e:
            log.exception("task %s ended: %r", name, threads.first(e))
        await anyio.sleep(RESTART_S)


async def broadcast(msg: dict):
    for ws in list(T().clients):
        try:
            await ws.send_json(msg)
        except Exception:
            T().clients.discard(ws)


def note(text: str, kind: str = "info"):
    """One line of the system log the page shows: what arrived, what each pass did, state changes."""
    e = {"type": "event", "at": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind, "text": text}
    T().events.append(e)
    del T().events[:-300]
    if T().tg:  # none: a test without the tenant's tasks, nobody to send it to
        T().tg.start_soon(broadcast, e)


def find_conversation(conv_id: str) -> dict | None:
    return db.conversation(conv_id)


def conversation_rows(c: dict) -> list[dict]:
    """Its device's rows in its time; a conversation from before per-device pipelines (no device) takes every row."""
    return db.sentences(c["start"], c["end"], inclusive=True, source=c.get("device"))


def queue_summary(c: dict, keep: bool = False):
    """keep: the current summary stays on the page until the new one is written (a rename redoes it)."""
    if not keep:
        db.put_summary({"conversation": c["id"], "status": "pending", "error": None})
    T().summaries[0].send_nowait(c)


def all_conversations() -> list[dict]:
    """Every closed conversation with its summary, newest first."""
    return db.conversations()


def tag_counts() -> dict[str, int]:
    """Recounted at most once a minute: a backfill summarizes thousands in a row."""
    _tags = T().tags
    if time.time() - _tags["at"] < 60:
        return _tags["counts"]
    n = {}
    for c in all_conversations():
        for t in (c["summary"] or {}).get("tags") or []:
            n[t] = n.get(t, 0) + 1
    _tags.update(at=time.time(), counts=dict(sorted(n.items(), key=lambda kv: -kv[1])))
    return _tags["counts"]


SUMMARY_WORKERS = int(os.environ.get("SUMMARY_WORKERS", "4"))  # 6 drew ~100 429s in a full re-run (09-30); 10 got 429s at once


async def summary_worker():
    """Queued first: conversations closed while the service was down and summaries that never finished. A newer
    prompt does not redo old summaries: the owner redoes one from its page (POST /api/conversation/{id}/summarize)."""
    for c in reversed(all_conversations()):
        s = c["summary"]
        if not s or s["status"] == "pending":
            queue_summary(c)
    async with anyio.create_task_group() as tg:
        for _ in range(SUMMARY_WORKERS):
            tg.start_soon(summarize_loop)


async def summarize(c: dict, tags: list[str]) -> dict:
    """A conversation that closed with a live agent gets its final summary from that agent (the whole conversation
    and the day in its context, the prefix cached); any other, and every redo, from a plain summary call."""
    rows = conversation_rows(c)
    tl = db.timeline(c["id"]) if c.get("via_live") else None
    if tl and tl.get("messages") and (c.get("speech_s", 0) >= summary.MIN_SPEECH_S or tl.get("asked")):  # asked: 立即总结 on a short one
        return {**summary.result(c, rows, *await live.final(tl, rows, tags, c["start"])), "via": "live"}
    return await summary.summarize(c, rows, tags)


async def summarize_loop():
    while True:
        c = await T().summaries[1].receive()
        for delay in (10, 60, 300, None):
            try:
                res = await summarize(c, list(tag_counts())[:60])
                break
            except Exception as e:  # HTTP errors, timeouts, a reply that is not JSON
                log.warning("summary %s failed: %r", c["id"], e)
                if delay == 300:
                    c.pop("via_live", None)  # the last try takes the plain summary
                res = {"conversation": c["id"], "status": "failed", "error": repr(e)[:300]}
                if delay:
                    await anyio.sleep(delay)
        old = db.summary(c["id"])
        if res["status"] == "failed" and old and old["status"] == "done":  # a failed upgrade keeps the old summary
            continue
        media = summary.media_labels(conversation_rows(c), res.get("media_spans") or [])
        with db.tx() as t:
            db.put_summary(res, t)
            if media:  # a redone summary found media: its voices leave the voiceprint bank
                t.executemany("DELETE FROM voices WHERE conv = ? AND label = ?", [(c["id"], m) for m in media])
        if media:
            T().speakers["at"] = 0.0
        note(f"对话 {c['start'][11:19]} 的摘要" + (f"：{res['title']}" if res["status"] == "done" else f"生成失败：{res['error']}"),
             "state" if res["status"] == "done" else "error")
        await broadcast({"type": "summary", **res})
        if res["status"] == "done" and res.get("model") and not db.name_hints(c["id"]):
            await name_hints(c, media)
        if res["status"] == "done":  # a profile kept up to date once it exists
            for p in c.get("participants") or []:
                if p != "我" and not speakers.ANON.fullmatch(p) and db.profile(p):
                    try:
                        await make_profile(p)
                    except Exception as e:
                        log.warning("profile %s failed: %r", p, e)


async def name_hints(c: dict, media: set[str]):
    """What each label was called (hints.py), once per conversation with a summary call (a rename carries them over in
    apply_rename). A failure is logged."""
    if not set(c.get("participants") or []) - {"我", "S?"} - media:
        return
    try:
        h = await hints.hints(c, conversation_rows(c), media)
    except Exception as e:
        log.warning("name hints %s failed: %r", c["id"], e)
        return
    db.put_name_hints(h)
    if h["names"]:
        T().speakers["at"] = 0.0
        note(f"对话 {c['start'][11:19]}：听出 {len(h['names'])} 个称呼，可在对话页确认", "state")


RECAP_AT = os.environ.get("RECAP_AT", "10:00")  # yesterday's recap (recap.py) is written then; empty: never


async def write_recap(day: str) -> dict | None:
    r = await recap.make(day)
    if r:
        db.put_kv(f"recap:{day}", r)
    return r


async def balance_loop():
    """The StepFun balance every hour: what transcription costs a day, and when the money runs out. One account
    for every tenant: kept in the first one's store, the others' pages show none."""
    while True:
        try:
            tenant.call(TENANTS[0], db.add_balance, "stepfun", await stepfun_asr.balance())
        except Exception as e:
            log.warning("stepfun balance: %r", e)
        await anyio.sleep(3600)


VM_INGEST_URL = os.environ.get("VM_INGEST_URL", "")  # .../api/v1/import/prometheus: metrics_loop pushes there; empty: nothing
STARTED = dt.datetime.now().isoformat()  # lr_calls_total counts from here: a restart resets it, as a counter may
CHARGE = {"battery": 0, "charging": 1, "full": 2}  # a recorder's hw charge (firmware lr_power.h)


def prom_line(name: str, labels: dict, v) -> str:
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return name + "{" + ",".join(f'{k}="{esc(x)}"' for k, x in labels.items()) + "} " + repr(float(v))


def metrics() -> str:
    """The current tenant's numbers in Prometheus text, its id the instance label: status(), db.calls and
    db.balance. Devices by their LR-… name, a phone (named by its owner) by a hash of it; never what was said, nor a name
    of anyone."""
    s, now, out = status(), dt.datetime.now(), []

    def m(name, v, **labels):
        if v is not None:
            out.append(prom_line(name, {"instance": T().id, **labels}, v))
    m("lr_up", 1, commit=s["commit"])
    m("lr_summaries_pending", s["summaries_pending"])
    for d in s["devices"]:
        dev = d["device"] if d["device"].startswith("LR-") else "phone-" + hashlib.sha256(d["device"].encode()).hexdigest()[:8]
        hw, snd, arr = d["hw"] or {}, d["sound"] or {}, d["arrival"] or {}
        # the pipeline, its last 10 min of audio (sound), the recorder as its phone last read it (hw)
        for name, v in (("behind_seconds", d["behind_s"]), ("latency_seconds", d["latency_s"]), ("queued_pieces", d["queued"]),
                        ("backlog_seconds", d["left_s"]), ("hold_active", d["hold"] is not None), ("recording", d["recording"]),
                        ("arrival_lag_seconds", arr.get("lag_s")), ("sound_seconds", snd.get("s")),
                        ("sound_zero_seconds", snd.get("zero_s")), ("sound_full_samples", snd.get("full")),
                        ("device_battery_percent", hw.get("battery")), ("device_charge", CHARGE.get(hw.get("charge"))),
                        ("device_last_seen_seconds", hw.get("at") and (now - dt.datetime.fromisoformat(hw["at"])).total_seconds())):
            m(f"lr_{name}", v, device=dev)
        if hw.get("fw"):
            m("lr_device_info", 1, device=dev, firmware=hw["fw"])
    for c in db.calls(STARTED):
        for result, n in {"ok": c["n"] - sum(c["failed"].values()), **c["failed"]}.items():  # failed: by HTTP code or class
            m("lr_calls_total", n, kind=c["kind"], engine=c["engine"], result=result)
        m("lr_call_audio_seconds_total", round(c["sent_h"] * 3600) or None, kind=c["kind"], engine=c["engine"])
    for c in db.calls((now - dt.timedelta(hours=1)).isoformat()):
        for q in ("50", "90"):
            m("lr_call_latency_seconds", c[f"ms{q}"] and c[f"ms{q}"] / 1000, kind=c["kind"], engine=c["engine"], quantile=f"0.{q[0]}")
    b = s["balance"] or {}
    m("lr_stepfun_balance_cny", b.get("cny"))
    m("lr_stepfun_days_left", b.get("days_left"))
    return "\n".join(out) + "\n"


async def metrics_loop():
    """Every tenant's metrics() to VictoriaMetrics every minute (basic auth VM_INGEST_USER / VM_INGEST_PASSWORD); a failed push is a
    warning, the next minute tries again."""
    while True:
        if VM_INGEST_URL:
            try:
                auth = base64.b64encode(f"{os.environ.get('VM_INGEST_USER', '')}:{os.environ.get('VM_INGEST_PASSWORD', '')}".encode()).decode()
                body = "".join(tenant.call(t, metrics) for t in TENANTS).encode()
                await _http(VM_INGEST_URL, body, {"Authorization": f"Basic {auth}", "Content-Type": "text/plain"})
            except Exception as e:
                log.warning("metrics push: %s", failure(e))  # the class and code, never the URL or credentials
        await anyio.sleep(60)


async def recap_loop():
    """Yesterday's recap at RECAP_AT, or at the start once that has passed and it is missing (a day without summaries
    gets none); a failed one is tried again in an hour."""
    at = dt.time.fromisoformat(RECAP_AT)
    while True:
        now = dt.datetime.now()
        day = (now.date() - dt.timedelta(days=1)).isoformat()
        if now.time() >= at and db.kv(f"recap:{day}") is None:
            try:
                await write_recap(day)
            except Exception as e:  # HTTP errors, timeouts, a reply that is not JSON
                log.warning("recap %s failed: %r", day, e)
                await anyio.sleep(3600)
                continue
        nxt = dt.datetime.combine(now.date() + dt.timedelta(days=now.time() >= at), at)
        await anyio.sleep(max(1.0, (nxt - dt.datetime.now()).total_seconds()))


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="milliseconds")


async def pipelines():
    """One pipeline per device with audio after its finalized time: a phone sending last night's backlog while an
    iPhone records today is transcribed on its own timeline. A device's pipeline lives on once started, in the
    tenant's task group."""
    await MODEL.wait()
    while True:
        devices = [r[0] for r in db.conn().execute("SELECT DISTINCT device FROM pieces")]
        after = {d: dt.datetime.fromisoformat(v) for d in devices if (v := db.kv(f"finalized_to:{d}"))}
        for d in audio.pending(after):
            if d not in T().pipelines:
                T().pipelines.add(d)
                T().tg.start_soon(lasting, d, name=f"pipeline:{d}")
        await anyio.sleep(5)


RESTART_S = 60  # a device's pipeline that failed starts again this much later


async def lasting(device: str):
    """The device's pipeline for good: one that fails (a bug, a missing file) starts again RESTART_S later from the
    finalized time, as after a server restart (10-09: a new instance's first device stayed untranscribed until one)."""
    while True:
        log.info("pipeline for %s from %s", device, db.kv(f"finalized_to:{device}"))
        try:
            await pipeline(device)
        except Exception as e:
            log.exception("pipeline %s ended", device)
            note(f"{device} 的处理任务退出：{threads.first(e)!r}"[:140] + f"，{RESTART_S} s 后重来", "error")
        await anyio.sleep(RESTART_S)


async def pipeline(device: str):
    """Two layers over one audio stream. The final layer always runs: pieces of up to CHUNK_S cut in pauses go whole
    to the engine once, become the device's sentences, conversations and summaries. The draft layer runs only while
    a page watches and the device is recording now, on its own stream of the device's newest audio (edge_feed): each
    pause its VAD sees sends the stretch before it at once; its rows go to the drafts table and the page until the
    final layer covers their time."""
    seg, dseg = Segmenter(), Segmenter()  # the final layer's audio, the draft layer's
    resume = db.kv(f"finalized_to:{device}")
    done0 = dt.datetime.fromisoformat(resume) if resume else None
    draft0 = db.kv(f"draft_to:{device}")
    st = T().devices[device] = PipelineState(
        seg=seg, done_to=done0,
        draft_to=dt.datetime.fromisoformat(draft0) if draft0 else None,
        drafts=db.drafts(iso(done0) if done0 else "", None, device))
    if resume:
        log.info("%s resuming after the last finalized row at %s", device, resume)

    async def feed():
        """The device's audio stream into the final layer's segmenter, taking the next stretch only while finalization
        is less than BUFFER_S - 300 s of audio behind: the buffer must not drop audio before it is finalized."""
        async for t, x in audio.stream(device, st.done_to):
            while True:
                done = st.done_to
                a = max(seg.audio_base, seg.sample_at(done)) if seg.anchors and done else seg.audio_base
                if (seg.samples - a) / SR <= BUFFER_S - 300:
                    break
                await anyio.sleep(1)
            st.audio_end = t + dt.timedelta(seconds=len(x) / SR)
            try:
                st.utts += [(u[0], u[1]) for u in seg.push(t, x)]
            except Exception:
                log.exception("%s: segmenter failed on %.1f s at %s", device, len(x) / SR, t.time())

    async def edge_feed():
        """The draft layer's own stream: the device's newest DRAFT_BACK_S of audio, whatever the final layer is busy
        with. A backlog (a phone back after hours offline sends them at once) is skipped here and finalized in order
        by the final layer; fed through feed(), drafts waited until it was all final (10-06: hours)."""
        async for t, x in audio.stream(device, None, edge_s=DRAFT_BACK_S):
            st.edge_end = t + dt.timedelta(seconds=len(x) / SR)
            try:
                utts = dseg.push(t, x)
            except Exception:
                log.exception("%s: draft segmenter failed on %.1f s at %s", device, len(x) / SR, t.time())
                continue
            for i, u in enumerate(utts):
                st.last_speech = u[1]  # a piece with no VAD speech is not sent to the engine
                st.draft_utts.append((u[0], u[1]))
                nxt = utts[i + 1][0] if i + 1 < len(utts) else dseg.time_at(dseg.pending[0]) if dseg.pending else None
                if nxt is None or (nxt - u[1]).total_seconds() >= MERGE_GAP_S:  # a pause, not a split of a long utterance
                    st.vad_to = u[1] + dt.timedelta(seconds=VAD_CUT_S)
                    st.wake.set()
            del st.draft_utts[:-200]

    from diarist import Diarist, relabel
    bank = {k: e for k, (_, e) in db.people().items()}
    vp = owner_npz()
    owner = dict(np.load(vp)) if vp.exists() else {}  # a new instance has nobody's voiceprint yet: nobody is 我 (selfvoice)
    embed, dia, grouper = state["embed"], Diarist(owner, people=bank), Grouper(open_=db.kv(f"open_conversation:{device}"))
    ddia = Diarist(owner, people=bank)  # the drafts' own clustering: it never touches the final labels
    T().dias.extend([dia, ddia])  # a rename on the conversation page teaches every diarist of the tenant a name
    live_failed = 0.0

    def open_view() -> dict | None:
        """The conversation going on now as the pages and the live analysis see it: the open conversation's final rows
        and the drafts after the finalized time; drafts alone start one before any final row has come."""
        o, done = grouper.open, st.done_to
        ds = [r for r in st.drafts if not done or r["start"] >= iso(done)]
        if o and ds and (dt.datetime.fromisoformat(ds[0]["start"]) - dt.datetime.fromisoformat(o["end"])).total_seconds() >= grouper.gap:
            o = None  # the drafts begin after the open conversation's gap: a new one
        if not o and not ds:
            return None
        speech = sum((dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds()
                     for r in ds if r.get("speaker") not in IGNORE)
        rows = (o["sentences"] if o else []) + ds
        return {"start": o["start"] if o else ds[0]["start"], "end": max(r["end"] for r in rows),
                "sentences": rows, "speech_s": (o["speech_s"] if o else 0.0) + speech}

    def show_open():
        v = open_view()
        st.conversation = {"start": v["start"], "end": v["end"], "sentences": len(v["sentences"]), "speech_s": round(v["speech_s"]),
                              "device": device} if v else None

    if (lv := db.kv(f"live:{device}")) and "messages" in lv and (v := open_view()) and overlaps(lv, v):
        st.live = lv
    show_open()
    if grouper.open:
        log.info("%s continuing the open conversation from %s", device, grouper.open["start"])
        note(f"{device}：接着上次打开的对话 {grouper.open['start'][11:19]}", "state")
    else:
        note(f"{device}：开始处理", "state")

    def record(c: dict) -> dict:
        cid = "c" + c["start"].replace("-", "").replace(":", "").replace("T", "")[:14]
        if (other := db.conversation(cid)) and other.get("device") != device:  # two devices starting the same second
            cid += "-" + re.sub(r"[^\w]", "_", device)
        return {"id": cid, "start": c["start"], "end": c["end"], "participants": c["participants"],
                "sentences": len(c["sentences"]), "speech_s": round(c["speech_s"], 1), "device": device}

    def relabel_rows(rec: dict) -> tuple[list[dict], int] | None:
        """A closed conversation's final rows labelled again with all its sentences in view (diarist.relabel), and how
        many changed; None when its voices do not cover its rows (a restart lost the earlier ones)."""
        vs = [v for v in st.voiced if rec["start"] <= v["start"] <= rec["end"]]
        rows = db.sentences(rec["start"], rec["end"], inclusive=True, source=device)
        if not vs or not rows:
            return None
        labels = relabel([v["e"] for v in vs], [v["dur"] for v in vs], owner,
                         [v["key"] or ("alone", i) for i, v in enumerate(vs)] if RELABEL_GROUPS else None, people=dia.people)
        changed, missing = 0, 0
        for r in rows:
            mine = [(v["dur"], lab) for v, lab in zip(vs, labels) if r["start"] <= v["start"] <= r["end"]]
            if not mine:
                missing += 1
                continue
            new = max(mine)[1]  # a row of several sentences: its longest one's
            if new != r.get("speaker"):
                r["speaker"], changed = new, changed + 1
        return (rows, changed) if missing <= len(rows) // 10 else None

    async def commit(closed: list[dict], rows: list[dict] | None = None, lo: str = "", hi: dt.datetime | None = None):
        """One transaction: the piece's rows replace [lo, hi), closed conversations (with the live analysis that
        overlaps one), the finalized time, the open conversation."""
        recs = [record(c) for c in closed]
        lv = st.live
        with db.tx() as t:
            if rows is not None:
                db.replace_sentences(t, lo, iso(hi), rows, device)
            attached, relabelled = False, []
            for rec in recs:
                if (rl := relabel_rows(rec)) and rl[1]:  # every label again, with the whole conversation in view
                    t.executemany("UPDATE sentences SET speaker = ? WHERE source = ? AND id = ?", [(r["speaker"], device, r["id"]) for r in rl[0]])
                    rec["participants"] = sorted({r["speaker"] for r in rl[0] if r.get("speaker") and r["speaker"] not in IGNORE}, key=str)
                    relabelled.append((rec, rl[0], rl[1]))
                    if lv and overlaps(lv, rec):
                        lv = {**lv, "drafted": True}  # its agent saw other labels: the closing turn resends the final transcript
                st.voiced = [v for v in st.voiced if v["start"] > rec["end"]]
                db.insert_conversation(t, rec)
                db.put_summary({"conversation": rec["id"], "status": "pending", "error": None}, t)
                if lv and overlaps(lv, rec):
                    db.put_timeline(rec["id"], lv, t)
                    rec["via_live"] = True  # its agent writes the final summary
                    attached = True
            if hi:
                db.put_kv(f"finalized_to:{device}", iso(hi), t)
            db.put_kv(f"open_conversation:{device}", grouper.open, t)
            if hi:
                st.drafts = [r for r in st.drafts if r["start"] >= iso(hi)]  # covered by final rows now
            # the analysis ends with its conversation, or when nothing is going on any more (a too short one is dropped)
            live_ends = bool(lv) and (attached or open_view() is None)
            if live_ends:
                db.put_kv(f"live:{device}", None, t)
        if hi:
            st.finalized(hi, time.time())
        if rows is not None:
            await broadcast({"type": "final", "device": device, "from": lo, "to": iso(hi), "rows": rows})
        for rec, rs, n in relabelled:
            log.info("%s relabelled %d of %d rows of %s", device, n, len(rs), rec["id"])
            note(f"对话 {rec['start'][11:19]}：整段重新认人，{len(rs)} 句里改了 {n} 句", "state")
            await broadcast({"type": "final", "device": device, "from": rec["start"],
                             "to": iso(dt.datetime.fromisoformat(rec["end"]) + dt.timedelta(milliseconds=1)), "rows": rs})
        for rec in recs:
            dia.reset()
            ddia.reset()
            T().summaries[0].send_nowait(rec)
            log.info("%s conversation closed %s", device, rec)
            note(f"对话结束：{rec['start'][11:19]} 到 {rec['end'][11:19]}，{rec['sentences']} 句，参与 {'、'.join(rec['participants']) or '—'}", "state")
            await broadcast({"type": "conversation", **db.conversation(rec["id"])})
        show_open()
        if live_ends:
            st.live = None
            await broadcast({"type": "live", "device": device, "live": None})

    def present(o: dict) -> bool:
        """The device is recording now and o is the conversation going on: during a backlog the drafts of what is said
        now (open_view starts one of them after the gap), not the old conversation the final layer is still on."""
        edge = st.edge_end
        return bool(edge) and (dt.datetime.now() - edge).total_seconds() <= LIVE_AUDIO_S and \
            (edge - dt.datetime.fromisoformat(o["end"])).total_seconds() <= grouper.gap

    def live_due() -> bool:
        """A page is in front, the conversation going on has a minute of speech and is the present, and LIVE_EVERY_S of
        new speech came (or any, when a page just came to the front)."""
        o, lv, now = open_view(), st.live, time.time()
        if not o or o["speech_s"] < summary.MIN_SPEECH_S or now > T().watch_until or now - live_failed < 120 or not present(o):
            return False
        if not lv or not overlaps(lv, o):
            return True
        grown = o["speech_s"] - lv["speech_s"]
        return grown >= LIVE_EVERY_S or (grown > 0 and T().wake_at > lv["wall"])

    async def live_update(asked: bool = False):
        nonlocal live_failed
        o = open_view()
        if o is None:  # it closed between the start and now
            return
        lv = st.live if st.live and overlaps(st.live, o) else live.new(o["start"], device, db.conversations(o["start"][:10], o["start"]))
        rows, speech = [r for r in o["sentences"] if r.get("text")], o["speech_s"]
        try:
            res = await live.analyze(lv, rows)
        except Exception as e:
            live_failed = time.time()
            log.warning("%s live analysis failed: %r", device, e)
            note(f"{device}：实时分析失败：{str(e)[:120]}，2 分钟后再试", "error")
            return
        res.update(speech_s=speech, wall=time.time(), at=dt.datetime.now().isoformat(timespec="seconds"),
                   upto=rows[-1]["start"], sentences=len(rows), asked=lv.get("asked") or asked)
        if (v := open_view()) and overlaps(res, v):
            st.live = res
            db.put_kv(f"live:{device}", res)
            await broadcast({"type": "live", "device": device, "live": live.public(res)})
        elif r := db.conn().execute("SELECT id FROM conversations WHERE device = ? AND start <= ? AND end >= ? ORDER BY start DESC",
                                    (device, res["upto"], res["start"])).fetchone():
            db.put_timeline(r[0], res)  # it closed while the call ran

    async def analysis(asked: bool = False):
        try:
            await live_update(asked)
        except Exception:  # a bug here must not end the pipeline beside it (its task group would)
            log.exception("%s live analysis", device)
        finally:
            st.analyzing = False

    def start_analysis(asked: bool = False):
        """One at a time, in the pipeline's task group: it ends with the pipeline."""
        st.analyzing = True
        ptg.start_soon(analysis, asked, name=f"live:{device}")

    def maybe_live():
        if not st.analyzing and live_due():
            start_analysis()

    def ask() -> str | None:
        """立即总结: the live analysis of the conversation going on, now: before its minute of speech, before LIVE_EVERY_S
        more, during a failure's wait. One at a time, and only with speech it has not seen (the page holds its button 10 s
        after a press). None when it started, else why not. Asked once, its agent writes the final summary however short
        it stays (summarize)."""
        o, lv = open_view(), st.live
        if st.analyzing:
            return "正在分析"
        if not o or not present(o):
            return "没有进行中的对话"
        if o["speech_s"] <= (lv["speech_s"] if lv and overlaps(lv, o) else 0):
            return "没有新的语音"
        start_analysis(asked=True)
        return None

    async def final_piece() -> bool:
        """Cut and finalize the next piece when there is one; True when a piece was done (look for the next at once)."""
        nonlocal owner
        if not grouper.open and T().owner is not None and T().owner is not owner:
            owner = T().owner  # 我 picked on the card (self_answer): from the next conversation on, never the open one
            dia.set_bank(owner)
            ddia.set_bank(owner)
        if not seg.anchors:
            return False
        done = st.done_to
        base = done or seg.time_at(seg.audio_base)
        st.utts = [u for u in st.utts if u[1] > base]
        rel = lambda t: (t - base).total_seconds()
        end_at = db.kv(f"end_at:{device}")
        force = bool(end_at) and not audio.queued(device)  # the recorder stopped and all its audio is in: no more is coming
        known = st.utts + ([(seg.time_at(seg.pending[0]), seg.time_at(seg.pending[1]))] if seg.pending else [])  # one waiting for a merge
        cut = next_cut([(max(0.0, rel(a)), rel(b)) for a, b in known], rel(seg.time_at(seg.samples)), seg.speaking(), force)
        if cut is None:
            return False
        a0 = max(seg.audio_base, seg.sample_at(base))
        b0 = min(seg.samples, seg.sample_at(base + dt.timedelta(seconds=cut)))
        if b0 <= a0:
            # finalized to the very end of a recording, the next one after a pause: the cut fell in the pause, no audio
            # to finalize. The pause has no speech: on to where the audio goes on (10-07: the iPhone's microphone waited
            # here from 17:59 on, 370 pieces queued; a restart started over the same way)
            if a0 < seg.samples and (nxt := seg.time_at(a0)) > base:
                await commit(grouper.advance(nxt), [], iso(base), nxt)
                return True
            return False
        x = seg.audio[a0 - seg.audio_base:b0 - seg.audio_base]
        at = lambda s: seg.time_at(a0 + int(s * SR))  # the piece may span a session boundary, where the clock jumps
        hi = at(len(x) / SR)
        if not any(rel(u[0]) < cut for u in known) and not seg.speaking():  # no speech: nothing to send, the clock still advances
            await commit(grouper.advance(hi), [], iso(base), hi)  # no rows: pages still drop their drafts of this time
            return True
        t_run = time.perf_counter()
        was_open = bool(grouper.open)
        m0 = dia.mark()
        try:
            segs = await engine_segs(x, speech_in(seg, a0, len(x)), device=device)
            engine = segs[0]["engine"] if segs else None
            marks = await anyio.to_thread.run_sync(label_sentences, dia, embed, segs, x, limiter=threads.INFER)
        except Exception as e:
            dia.rollback(m0)
            log.warning("%s final piece failed: %r", device, e)
            note(f"{device}：定稿引擎调用失败：{str(e)[:120]}，稍后重试这一块", "error")
            st.refine = {"error": str(e)[:200]}
            await anyio.sleep(10)
            return False
        by_label = {}
        for g in segs:  # final_rows keys confidence by label
            by_label[g["speaker"]] = max(by_label.get(g["speaker"], 0.0), g["confidence"])
        lo, hi, rows = final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, base, len(x) / SR, by_label, 0.0, at, 0.0)
        st.voiced += [{"start": iso(at(g["start_ms"] / 1000)), "dur": (g["end_ms"] - g["start_ms"]) / 1000,
                          "e": dia.E[m[0] - 1] if m else None, "key": (iso(base), g["engine_speaker"]) if g.get("engine_speaker") else None}
                         for g, m in zip(segs, marks)]
        with open(T().data / f"{lo.date()}.passes.jsonl", "a") as f:  # every piece's raw engine output, for tracing lost rows
            f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), "layer": "final", "t0": iso(base), "since": 0.0,
                                "from": iso(lo), "to": iso(hi), "segs": segs}, ensure_ascii=False) + "\n")
        shown = dt.datetime.now()
        for r in rows:
            r["source"], r["engine"], r["shown_at"] = device, engine, iso(shown)
            st.final_latency.append(round((shown - dt.datetime.fromisoformat(r["end"])).total_seconds(), 1))
        del st.final_latency[:-50]
        closed = [c for r in rows for c in grouper.sentence(r)]
        closed += grouper.advance(hi)
        await commit(closed, rows, iso(lo), hi)
        st.refine = {"at": dt.datetime.now().isoformat(timespec="seconds"), "ms": round((time.perf_counter() - t_run) * 1000),
                        "engine": engine, "speakers": by_label, "changed": len(rows), "piece_s": round(len(x) / SR),
                        "backlog_s": round((seg.time_at(seg.samples) - hi).total_seconds())}
        log.info("final %s: %s", device, st.refine)
        note(f"定稿一块：{device} {lo.strftime('%H:%M:%S')}–{hi.strftime('%H:%M:%S')}（{len(x) / SR:.0f} s）给 {engine or ENGINE}，"
             f"{st.refine['ms'] / 1000:.1f} s，{len(rows)} 句，积压 {st.refine['backlog_s']} s")
        if segs and segs[0].get("fallback"):
            note(f"{device}：定稿引擎失败（{segs[0]['fallback'][:120]}），这一块改用 {engine}", "warn")
        if grouper.open and not was_open:
            note(f"{device}：对话开始 {grouper.open['start'][11:19]}", "state")
        return True

    async def final_loop():
        while True:
            while await final_piece():
                maybe_live()
            closed = []
            end_at = db.kv(f"end_at:{device}")
            final_all = not seg.anchors or (st.done_to and (seg.time_at(seg.samples) - st.done_to).total_seconds() < 0.5)
            if end_at and not audio.queued(device) and final_all:
                closed += grouper.end(dt.datetime.fromisoformat(end_at))  # the recorder stopped and its last audio is final
                db.put_kv(f"end_at:{device}", None)
            idle = grouper.tick()  # the safety net: nothing at all for IDLE_S (the recorder died without an end)
            if idle:
                note(f"{device}：20 分钟没有任何事件，按兜底规则关闭对话", "warn")
            closed += idle
            if closed:
                await commit(closed)
            maybe_live()
            await anyio.sleep(2)

    def drafting() -> bool:
        """A page watches and this device is recording now (its newest audio is recent, backlog or not)."""
        return (time.time() < T().watch_until and bool(dseg.anchors) and st.edge_end is not None
                and (dt.datetime.now() - st.edge_end).total_seconds() < LIVE_AUDIO_S)

    async def publish_drafts(rows: list[dict], upto: dt.datetime, engine: str):
        """Store, push and advance draft_to: a crash after this does not redo these rows."""
        shown = dt.datetime.now()
        for r in rows:
            r["id"], r["state"] = "d" + r["id"][1:], "draft"
            r["source"], r["engine"], r["shown_at"] = device, engine, iso(shown)
            st.latency.append(round((shown - dt.datetime.fromisoformat(r["end"])).total_seconds(), 1))
        del st.latency[:-50]
        with db.tx() as t:
            db.insert_drafts(t, rows)
            db.put_kv(f"draft_to:{device}", iso(upto), t)
        st.draft_to = upto
        done = st.done_to
        st.drafts += [r for r in rows if not done or r["start"] >= iso(done)]
        if rows:
            await broadcast({"type": "draft", "device": device, "rows": rows})
        show_open()
        maybe_live()

    async def draft_pass(end: dt.datetime | None = None) -> bool:
        """One draft pass; True when more remains. Timer (end None): [draft_to - margin, + window], rows near its end
        left open; VAD: from 1 s before the first undrafted utterance to `end`, a point in the pause after one."""
        dto = st.draft_to
        t_run = time.perf_counter()
        if end is None:
            t0, x = dseg.window(REFINE_WINDOW_S, dto - dt.timedelta(seconds=REFINE_MARGIN_S))
        else:
            us = st.draft_utts = [u for u in st.draft_utts if u[1] > dto]
            if not us:
                return False
            start = max(us[0][0] - dt.timedelta(seconds=1), dto - dt.timedelta(seconds=REFINE_MARGIN_S))
            t0, x = dseg.window(min(REFINE_WINDOW_S, (dseg.sample_at(end) - dseg.sample_at(start)) / SR), start)
        a0 = dseg.sample_at(t0)
        at = lambda s: dseg.time_at(a0 + int(s * SR))
        since = max(0.0, (dseg.sample_at(dto) - a0) / SR)
        edge = 0.0 if end is not None and len(x) / SR < REFINE_WINDOW_S else EDGE_S  # a window cut short of its pause may end mid-sentence
        if len(x) / SR - since < (0.3 if end is not None else EDGE_S + 10):
            return False
        hi = at(len(x) / SR - EDGE_S)
        if end is None and not dseg.speaking() and (st.last_speech is None or st.last_speech <= dto):
            st.draft_to = hi  # silence: nothing to send
            db.put_kv(f"draft_to:{device}", iso(hi))
            return (dseg.time_at(dseg.samples) - hi).total_seconds() > REFINE_WINDOW_S - REFINE_MARGIN_S
        sent, n_rows = since, 0  # seconds into the window up to which rows went out while the call ran

        async def publish(rows: list[dict], upto: dt.datetime, engine: str):
            nonlocal n_rows
            await publish_drafts(rows, upto, engine)
            n_rows += len(rows)

        async def early(segs: list[dict]):
            """An answer during the call: its sentences but the last (it may still grow) go out now, labelled in order."""
            nonlocal sent
            segs = clip_turns(segs[:-1], sent)
            if not segs:
                return
            await anyio.to_thread.run_sync(label_sentences, ddia, embed, segs, x, limiter=threads.INFER)
            rows = final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, t0, len(x) / SR, None, sent, at, 0.0)[2]
            sent = segs[-1]["end_ms"] / 1000
            await publish(rows, at(sent), segs[0]["engine"])

        send, answers = anyio.create_memory_object_stream(math.inf)
        got = []

        async def call():
            with send:  # closed when the call ends, and with it the answers
                got.append(await engine_segs(x, speech_in(dseg, a0, len(x)), send.send_nowait, DRAFT_ENGINE, "draft", device))
        m0 = ddia.mark()
        try:
            with answers:
                async with anyio.create_task_group() as tg:  # a pass failing or cancelled takes its engine call with it
                    tg.start_soon(call)
                    async for segs in answers:  # a 5 min backfill window: its first sentences in ~3 s, not after the whole call
                        await early(segs)
                        m0 = ddia.mark()
            raw = got[0]
            segs = clip_turns(raw, sent)
            marks = await anyio.to_thread.run_sync(label_sentences, ddia, embed, segs, x, limiter=threads.INFER)
        except Exception as e:
            e = threads.first(e)
            ddia.rollback(m0)
            log.warning("%s draft pass failed: %r", device, e)
            st.draft = {"error": str(e)[:200]}
            return False
        by_label = {}
        for g in segs:
            by_label[g["speaker"]] = max(by_label.get(g["speaker"], 0.0), g["confidence"])
        lo, hi, rows = final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, t0, len(x) / SR, by_label, sent, at, edge)
        kept = [m for g, m in zip(segs, marks) if m and at(g["start_ms"] / 1000) < hi]  # sentences left open come back next pass
        ddia.rollback(kept[-1] if kept else m0)
        hi = max(hi, at(sent))  # rows that went out early near the window's end: never draft them twice
        with open(T().data / f"{lo.date()}.passes.jsonl", "a") as f:
            f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), "layer": "draft", "t0": iso(t0), "since": since,
                                "early_to": sent, "from": iso(at(since)), "to": iso(hi), "segs": raw}, ensure_ascii=False) + "\n")
        engine = raw[0]["engine"] if raw else DRAFT_ENGINE
        await publish(rows, hi, engine)
        st.draft = {"at": dt.datetime.now().isoformat(timespec="seconds"), "ms": round((time.perf_counter() - t_run) * 1000),
                       "engine": engine, "changed": n_rows, "early_s": round(sent - since), "to": iso(hi)}
        log.info("draft %s: %s", device, st.draft)
        if end is not None:  # pauses that came during the pass have set wake again
            return len(x) / SR >= REFINE_WINDOW_S
        return (dseg.time_at(dseg.samples) - hi).total_seconds() > REFINE_WINDOW_S - REFINE_MARGIN_S

    async def draft_loop():
        while True:
            with anyio.move_on_after(REFINE_EVERY_S):  # a pause after speech drafts at once; the timer covers pause-less speech
                await st.wake.wait()
            by_vad = st.take_draft_wake()
            if not drafting():
                st.drafting = False
                continue
            # from where drafts or final rows stop, but no further back than DRAFT_BACK_S: a page that opens sees the
            # minutes not final yet, not a backlog
            floor = dseg.time_at(dseg.samples) - dt.timedelta(seconds=DRAFT_BACK_S)
            st.draft_to = max(t for t in (st.draft_to, st.done_to, floor, dseg.time_at(dseg.audio_base)) if t)
            if not st.drafting:
                st.drafting = True
                note(f"{device}：页面在看，开始出初稿（从 {st.draft_to.strftime('%H:%M:%S')} 起）", "state")
                while await draft_pass():  # what came before the page opened, in windows
                    pass
            if by_vad:
                while st.vad_to and st.vad_to > st.draft_to and await draft_pass(st.vad_to):
                    pass
            else:
                while await draft_pass():
                    pass

    async def stream_sentence(g: dict, base: int):
        """A sentence of the streaming session that began at stream sample `base`: one still being said goes to the
        pages as `partial` (replaced by the next one); one it ended becomes a draft row, labelled as a draft pass's."""
        a = base + g["start_ms"] * SR // 1000
        b = max(a, base + g["end_ms"] * SR // 1000)
        at = lambda s: dseg.time_at(a + int(s * SR))
        if not g["final"]:
            await broadcast({"type": "partial", "device": device, "row": {
                "id": "p" + device, "start": at(0).isoformat(timespec="milliseconds"), "end": at((b - a) / SR).isoformat(timespec="milliseconds"),
                "speaker": "", "text": g["text"], "state": "draft", "source": device, "engine": "qwen-stream"}})
            return
        seg = {"start_ms": 0, "end_ms": (b - a) * 1000 // SR, "text": g["text"], "speaker": None}
        x = dseg.audio[max(0, a - dseg.audio_base):max(0, b - dseg.audio_base)]
        await anyio.to_thread.run_sync(label_sentences, ddia, embed, [seg], x, limiter=threads.INFER)
        rows = final_rows([seg], {seg["speaker"]: seg["speaker"]}, at(0), (b - a) / SR, None, 0.0, at, 0.0)[2]
        await publish_drafts(rows, at((b - a) / SR), "qwen-stream")
        st.draft = {"at": dt.datetime.now().isoformat(timespec="seconds"), "engine": "qwen-stream", "to": iso(st.draft_to)}

    async def stream_loop():
        """DRAFT_ENGINE qwen-stream: while drafting(), one streaming session (qwen_stream) fed the draft stream's audio as
        it comes, every 0.2 s, from where drafts or final rows stop (as draft_loop); each sentence it ends is a draft row
        at once, the one still being said is shown as `partial`. A session that ends (the page went, the server closed
        it, it failed) is one call in the calls table; the next starts again at the end of the last draft row."""
        while True:
            await anyio.sleep(1)
            if not drafting():
                st.drafting = False
                continue
            floor = dseg.time_at(dseg.samples) - dt.timedelta(seconds=DRAFT_BACK_S)
            st.draft_to = max(t for t in (st.draft_to, st.done_to, floor, dseg.time_at(dseg.audio_base)) if t)
            if not st.drafting:
                st.drafting = True
                note(f"{device}：页面在看，开始出初稿（实时流，从 {st.draft_to.strftime('%H:%M:%S')} 起）", "state")
            base = max(dseg.audio_base, dseg.sample_at(st.draft_to))
            sent, t, err = base, time.perf_counter(), None
            try:
                async with qwen_stream.Session() as s, anyio.create_task_group() as tg:
                    async def pump():
                        nonlocal sent
                        while drafting():
                            if dseg.samples > sent:
                                lo, sent = max(sent, dseg.audio_base), dseg.samples
                                await s.send(dseg.audio[lo - dseg.audio_base:sent - dseg.audio_base].tobytes())
                            await anyio.sleep(0.2)
                        await s.finish()
                    tg.start_soon(pump)
                    async for g in s.results():
                        await stream_sentence(g, base)
                    tg.cancel_scope.cancel()  # the task finished: no more audio into it
            except Exception as e:
                err = threads.first(e)
                log.warning("%s draft stream failed after %.0f s: %r", device, time.perf_counter() - t, err)
                st.draft = {"error": str(err)[:200]}
            db.add_call("draft", "qwen-stream", device, round((time.perf_counter() - t) * 1000), err and failure(err), (sent - base) / SR, (sent - base) / SR)
            await broadcast({"type": "partial", "device": device, "row": None})

    async def forever(part):
        await part()
        raise RuntimeError(f"{part.__name__} returned")

    # each part runs for good: the first to end (an exception) ends the pipeline, its task group taking the others and
    # the live analysis with it (nothing left behind: two readers on one device would both move its queue), and
    # lasting() starts it again whole
    try:
        async with anyio.create_task_group() as ptg:
            st.ask = ask
            drafts = stream_loop if DRAFT_ENGINE == "qwen-stream" else draft_loop
            for name, part in (("feed", feed), ("edge", edge_feed), ("draft", drafts), ("final", final_loop)):
                ptg.start_soon(forever, part, name=f"{name}:{device}")
    finally:
        st.ask = None  # no task group to run in until lasting() starts it again
        T().dias.remove(dia)
        T().dias.remove(ddia)


def device_status(device: str, st: PipelineState) -> dict:
    end, done, pr = st.audio_end, st.done_to, st.progress
    lat = st.latency if st.drafting else st.final_latency  # what the page shows first: drafts while it watches
    left, received_to = audio.backlog(device, done)
    hold, seen, arrived = audio.held(device), audio.hold_seen(device), audio.arrival(device)
    # seconds of audio finalized per second, over the commits of the last 10 min (silence goes fast, speech ~8x)
    rate = (pr[-1][1] - pr[0][1]).total_seconds() / (pr[-1][0] - pr[0][0]) if len(pr) > 1 and pr[-1][0] > pr[0][0] else None
    return {"device": device, "audio_end": end.isoformat(timespec="seconds") if end else None,
            "behind_s": round((dt.datetime.now() - end).total_seconds(), 1) if end else None,
            "queued": audio.queued(device), "done_to": done.isoformat(timespec="seconds") if done else None,
            "received_to": received_to.isoformat(timespec="seconds") if received_to else None, "left_s": left,
            # finalization waits here for older audio the phone is still sending (its hw: backlog_kb, backlog_eta_s)
            "hold": dt.datetime.fromtimestamp(hold / 1000).isoformat(timespec="seconds") if hold else None,
            # when its phone last said it, and when it is let go without the phone (audio.HOLD_MAX_S)
            "hold_seen": dt.datetime.fromtimestamp(seen).isoformat(timespec="seconds") if hold and seen else None,
            "hold_until": dt.datetime.fromtimestamp(seen + audio.HOLD_MAX_S).isoformat(timespec="seconds") if hold and seen else None,
            # its newest audio came in LIVE_AUDIO_S ago at most: behind_s and the latencies are about now (otherwise
            # behind_s is how long ago it last recorded)
            "recording": bool(arrived) and time.time() * 1000 - arrived["at"] < LIVE_AUDIO_S * 1000,
            "rate": round(rate, 1) if rate else None, "eta_s": round(left / rate) if rate and rate > 0.1 and left > 30 else None,
            "last_speech": st.last_speech.isoformat(timespec="seconds") if st.last_speech else None,
            "refine": st.refine, "conversation": st.conversation, "drafting": st.drafting, "draft": st.draft,
            "analyzing": st.analyzing,  # the live analysis is running
            "draft_to": st.draft_to.isoformat(timespec="seconds") if st.draft_to else None,
            "latency_s": sorted(lat)[len(lat) // 2] if lat else None,
            "arrival": arrived, "sound": sound(device, received_to)}


def sound(device: str, end: dt.datetime | None) -> dict | None:
    """How the device's last 10 min of audio (before `end`, its newest) sounded: s of it, s in zero runs (the mic sent
    nothing), full-scale samples (audio.health)."""
    ms = int(end.timestamp() * 1000) if end else 0
    m = audio.minutes(device, ms - 600_000, ms) if end else []
    return {"s": round(sum(r["s"] for r in m), 1), "zero_s": round(sum(r["zero_s"] for r in m), 2), "full": sum(r["full"] for r in m)} if m else None


def status() -> dict:
    """What the page's status line shows (the device whose audio came in last), every device's pipeline, and the
    pieces still queued over all devices."""
    ds = [{**device_status(d, st), "hw": db.device_status(d)} for d, st in T().devices.items()]
    top = max(ds, key=lambda d: d["audio_end"] or "", default={"device": None, "audio_end": None, "behind_s": None, "done_to": None,
                                                                 "last_speech": None, "refine": None, "conversation": None, "latency_s": None})
    summaries = db.conn().execute("SELECT count(*) FROM summaries WHERE status = 'pending'").fetchone()[0]
    return {**top, "queued": sum(d["queued"] for d in ds), "devices": ds, "summaries_pending": summaries, "summary_queue": T().summaries[1].statistics().current_buffer_used,
            "recording": any(d["recording"] for d in ds), "last_audio": max((d["received_to"] for d in ds if d["received_to"]), default=None),
            "balance": db.balance("stepfun"),
            "commit": state["commit"], "chunks": T().chunks,
            "engine": f"{ENGINE} · 初稿 {DRAFT_ENGINE}" + (f" · 兜底 {ENGINE_FALLBACK}" if ENGINE_FALLBACK else "")}


async def status_loop():
    while True:
        if T().clients:  # a tenant with no page open has nobody to tell
            await broadcast({"type": "status", **status()})
        await anyio.sleep(2)


class Tenants:
    """Every request and WebSocket runs as the tenant its token is (tenant.current), Bearer or the cookie; none: 401
    (a WebSocket closed with 4401). Open any page once with ?token=<its token>: a year-long cookie replaces the browser's
    basic-auth prompt that Android Chrome forgets on every reload. A process of one tenant without a token: no auth
    (loopback only)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope["path"] == "/api/health":  # lifespan; health: no token
            return await self.app(scope, receive, send)
        c = HTTPConnection(scope)
        q = c.query_params.get("token")
        if scope["type"] == "http" and q and (t := tenant.by_token(TENANTS, q)) and t.token:
            u = c.url.remove_query_params("token")
            resp = RedirectResponse(u.path + (f"?{u.query}" if u.query else ""), status_code=303)  # relative: the proxy owns the scheme
            resp.set_cookie("token", t.token, max_age=365 * 86400, httponly=True, samesite="lax")
            return await resp(scope, receive, send)
        t = tenant.by_token(TENANTS, c.headers.get("authorization", "")[7:]) or tenant.by_token(TENANTS, c.cookies.get("token"))
        if t is None:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4401})
            return await Response("token required: open /?token=<your token> once", 401)(scope, receive, send)
        tenant.enter(t)  # this request's task and what it starts
        await self.app(scope, receive, send)


app.add_middleware(Tenants)


NO_CACHE = {"Cache-Control": "no-cache"}  # pages change with deploys; revalidate by etag instead of heuristic caching


DIST = HERE / "static/dist"  # the web app (web/, built by deploy.sh)


@app.get("/")
@app.get("/conversations")
@app.get("/c/{conv_id}")
@app.get("/record")
@app.get("/speakers")
@app.get("/calendar")
@app.get("/progress")
@app.get("/connect")
@app.get("/p/{name}")
@app.get("/device/{name}")
async def page(conv_id: str = "", name: str = ""):
    return FileResponse(DIST / "index.html", headers=NO_CACHE)


@app.get("/assets/{name}")
async def asset(name: str):
    """Build output with a content hash in the name: never changes, so cached for good."""
    p = DIST / "assets" / name
    if not p.is_file():
        raise HTTPException(404, "no such asset")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/app.apk")
async def app_apk():
    """The Android client, for installing from the phone's browser."""
    p = TENANTS[0].data / "live-record.apk"  # every tenant's: the first one's
    if not p.exists():
        raise HTTPException(404, "no apk uploaded")
    return FileResponse(p, media_type="application/vnd.android.package-archive", filename="alldayrec.apk")


@app.get("/api/app/latest")
async def apk_latest():
    """What /app.apk is: the Android app compares versionCode with its own build number and offers the update."""
    p = TENANTS[0].data / "live-record.apk"  # every tenant's: the first one's
    if not p.exists():
        raise HTTPException(404, "no apk uploaded")
    return {**await anyio.to_thread.run_sync(apk.version, p, limiter=threads.DB), "size": p.stat().st_size}


@app.post("/api/chunks")
async def chunk(request: Request, device: str, start: int, n: int | None = None, hold: int | None = None):
    """One self-contained audio file (the apps' Opus or AAC pieces), `start` its device time in unix ms, `n` the 16 kHz
    samples the client wrote into it (the iPhone's AAC: the decoder adds some at both ends). Kept on disk (also
    for playback) and queued; the device's pipeline takes its pieces in time order at the pace finalization keeps up
    with, so a client back from an outage sends its whole backlog at once, whatever other devices are doing. `hold`
    (unix ms): the client still has older audio of this device than that to send (it sends what is said now while it
    reads a backlog off the recorder's card): finalization waits there for it, drafts go on (audio.hold); an upload
    without it lets go."""
    audio.hold(device, hold)  # before the piece is in: the queue must not hand it out ahead of the older audio
    body, kind = await request.body(), request.headers.get("content-type", "")
    seconds = await anyio.to_thread.run_sync(audio.save, device, start, body, kind, n, limiter=threads.MEDIA)
    if seconds is None:  # the app drops it on a 400 (audio.save keeps its bytes in rejected/): say so, it is lost audio
        log.warning("%s: piece at %s (%s, %d bytes) could not be decoded", device, dt.datetime.fromtimestamp(start / 1000), kind, len(body))
        note(f"{device} 的一块音频（起于 {dt.datetime.fromtimestamp(start / 1000).strftime('%H:%M:%S')}）无法解码，客户端收到 400 会丢弃它；原始字节留在 recordings/rejected/", "error")
        raise HTTPException(400, "could not decode")
    T().chunks = {"device": device, "at": time.time()}
    late = "（早于这台设备已处理的位置，只存不转写）" if start <= T().read_to.get(device, -1) else ""
    note(f"收到 {device} 的块 {seconds:.1f} s，起于 {dt.datetime.fromtimestamp(start / 1000).strftime('%H:%M:%S')}{late}", "warn" if late else "info")
    return {"seconds": round(seconds, 2)}


@app.post("/api/chunks/end")
async def chunk_end(device: str, t: int):
    """The recorder stopped at device time t (unix ms): the open conversation closes once its audio is processed."""
    end = dt.datetime.fromtimestamp(t / 1000)
    db.put_kv(f"end_at:{device}", iso(end))  # kept across a restart: the queue may still hold the audio before it
    log.info("%s stopped recording at %s", device, end.time())
    note(f"{device} 停止录音（{end.strftime('%H:%M:%S')}），处理完剩余音频后关闭对话")
    return {"ok": True}


@app.post("/api/hold/release")
async def hold_release(device: str):
    """The phone no longer has the recorder (unbound, or another bound in its place): the older audio on its card will
    not come from it, so finalization stops waiting for it now, not HOLD_MAX_S after the phone last said the hold
    (10-07: LR-P3-116C's 261 pieces waited from 17:47 to 05:47)."""
    if audio.held(device) is not None:
        audio.hold(device, None)
        log.info("%s hold let go: its phone no longer has it", device)
        note(f"{device} 已从手机上解除，不再等它卡里的旧录音，继续定稿")
    return {"ok": True}


@app.post("/api/device-status")
async def device_status_report(device: str, hw: dict):
    """What a client read from a BLE recorder (battery, card, firmware, model): each one kept, the newest shown with
    the device's pipeline."""
    db.add_device_status(device, iso(dt.datetime.now()), hw)
    return {"ok": True}


class DeviceEvent(BaseModel):
    src: str               # "rec": a record of the recorder's own event log (firmware lr_log.h); else the app that wrote it
    seq: int               # the recorder log's sequence number, or the app's own: unique per device and src
    at: int | None = None  # epoch ms, put on the clock by the phone (a record's frame by the boot's TIME records); None: unknown
    data: dict             # rec: {boot, t, code, x, a, b} as recorded; an app's: {kind, text?, ...}


@app.post("/api/device-log")
async def device_log_upload(device: str, events: list[DeviceEvent]):
    """A batch of a device's events, from the phone that read them; one sent again replaces the one kept."""
    db.put_device_log(device, [{**e.model_dump(), "at": None if e.at is None else iso(dt.datetime.fromtimestamp(e.at / 1000))} for e in events])
    return {"ok": True}


@app.get("/api/device-log")
async def device_log(device: str, since: str, until: str | None = None):
    """A device's events, status reports and audio by the minute (audio.minutes) in [since, until)."""
    ms = lambda t: int(dt.datetime.fromisoformat(t).timestamp() * 1000)
    return {"events": db.device_log(device, since, until), "status": db.device_statuses(device, since, until),
            "audio": audio.minutes(device, ms(since), ms(until) if until else 1 << 62)}


@app.get("/api/calls")
async def calls(since: str):
    """The ASR and LLM calls since `since`, by kind and engine (db.calls), and the StepFun balance (db.balance)."""
    return {"calls": db.calls(since), "balance": db.balance("stepfun")}


@app.get("/api/upgrades")
async def upgrades(device: str, since: str):
    """A device's firmware updates begun since `since`, oldest first: what its app logged joined with the recorder's DFU
    and BOOT records (db.upgrades)."""
    return db.upgrades(device, since)


@app.get("/api/client-config")
async def client_config():
    """What the apps ask on start and while recording: piece lengths per app state (the server changes them without
    a new build; short pieces in front make drafts quick, long ones in the background save requests)."""
    return {"slice": {"foreground": SLICE_FOREGROUND_S, "background": SLICE_BACKGROUND_S, "screenOff": SLICE_SCREEN_OFF_S}}


@app.get("/api/app-setup")
async def app_setup():
    """For the web page's QR: the token to hand the app (its tenant's, the one the page sent), so it doesn't need
    typing in by hand. Empty in no-auth mode, same as everywhere else."""
    return {"token": T().token}


@app.get("/api/events")
async def events():
    return T().events


@app.get("/api/live")
async def live_now():
    """Each device's live analysis of its open conversation."""
    return [live.public(st.live) for st in T().devices.values() if st.live]


@app.post("/api/live/{device}")
async def live_ask(device: str):
    """立即总结 (the live page's button): the device's live analysis now; 409 with why not."""
    st = T().devices.get(device)
    ask = st.ask if st else None  # none until its pipeline's task group is ready
    if not ask:
        raise HTTPException(404, "没有这台设备的处理流程")
    if why := ask():
        raise HTTPException(409, why)
    return {"analyzing": True}


@app.get("/api/health")
async def health():
    """No token and no tenant (Tenants lets it through): for reverse proxies and docker-compose.yml's healthcheck."""
    return {"ok": True}


@app.get("/api/status")
async def status_now():
    return status()


@app.get("/api/sentences")
async def sentences(date: str | None = None):
    day = dt.date.fromisoformat(date) if date else dt.date.today()
    lo, hi = day.isoformat(), (day + dt.timedelta(days=1)).isoformat()
    drafts = db.drafts(lo, hi)
    return merge_drafts(db.sentences(lo, hi), drafts, {d: db.kv(f"finalized_to:{d}") for d in {r.get("source") for r in drafts}})


@app.get("/api/conversations")
async def conversations(date: str | None = None):
    day = dt.date.fromisoformat(date) if date else dt.date.today()
    closed = db.conversations(day.isoformat(), (day + dt.timedelta(days=1)).isoformat())
    return closed + [{**st.conversation, "state": "open"} for st in T().devices.values()
                     if st.conversation and st.conversation["start"][:10] == day.isoformat()]


@app.get("/api/days")
async def days():
    """Days that have sentences, newest first."""
    return [r[0] for r in db.conn().execute("SELECT DISTINCT substr(start, 1, 10) d FROM sentences ORDER BY d DESC")]


@app.get("/api/calendar")
async def calendar():
    """Per day with sentences: closed conversations (dictations apart), their speech minutes and the sentence count,
    for the day picker and the calendar page."""
    out = {d: {"conversations": 0, "dictations": 0, "speech_min": 0.0, "sentences": n}
           for d, n in db.conn().execute("SELECT substr(start, 1, 10), count(*) FROM sentences GROUP BY 1")}
    for d, kind, n, s in db.conn().execute("SELECT substr(start, 1, 10), coalesce(kind, '对话'), count(*), coalesce(sum(speech_s), 0) "
                                           "FROM conversations GROUP BY 1, 2"):
        x = out.setdefault(d, {"conversations": 0, "dictations": 0, "speech_min": 0.0, "sentences": 0})
        x["dictations" if kind == "口述" else "conversations"] += n
        x["speech_min"] = round(x["speech_min"] + s / 60, 1)
    return dict(sorted(out.items(), reverse=True))


@app.get("/api/search")
async def search(q: str = "", tag: str = "", kind: str = "", who: str = "", after: str = "", before: str = "",
                 limit: int = 30):
    """db.search; `before` is the start of the last one already shown."""
    return db.search(q, tag, kind, who, after, before, limit)


@app.post("/api/ask")
async def ask_ai(body: dict):
    """A question about my own past answered from this app's data (ask.py), in 10-30 s: {answer, sources, calls, usage}."""
    q = (body.get("q") or "").strip()
    if not q or len(q) > 500:
        raise HTTPException(400, "empty or too long")
    try:
        return await ask.ask(q)
    except Exception as e:  # HTTP errors, timeouts
        log.warning("ask failed: %r", e)
        raise HTTPException(502, f"模型调用失败：{e!r}"[:300])


@app.post("/mcp")
async def mcp(request: Request):
    """Remote MCP for the owner's agents (mcp_server.py), with the same token as every other client: stateless, JSON only."""
    if not mcp_server.origin_ok(request.headers):
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Origin not allowed"}}, 403)
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
    status, out = await anyio.to_thread.run_sync(mcp_server.handle, msg, request.headers, limiter=threads.DB)
    return Response(status_code=202) if out is None else JSONResponse(out, status)


@app.api_route("/mcp", methods=["GET", "DELETE"])
async def mcp_stream():
    """No server-sent stream and no sessions to end (2026-07-28)."""
    return Response(status_code=405, headers={"Allow": "POST"})


@app.get("/api/recap")
async def recap_get(date: str = ""):
    """A day's recap, the newest one when no day is given; null when there is none. A todo marked done or wrong since is left out."""
    if date:
        r = db.kv(f"recap:{date}")
    else:
        row = db.conn().execute("SELECT value FROM kv WHERE key LIKE 'recap:%' ORDER BY key DESC LIMIT 1").fetchone()
        r = json.loads(row[0]) if row else None
    if r:
        closed = db.closed_todos()
        r["todos"] = [t for t in r["todos"] if (t["conv"], t["text"]) not in closed]
    return r


@app.get("/api/todos/due")
async def todos_due():
    """My todos due today or before, not marked done or wrong (完成 / 不对 go to /api/conversation/{id}/feedback)."""
    return db.due_todos(dt.date.today().isoformat())


@app.post("/api/recap/{date}")
async def recap_redo(date: str):
    """Write a day's recap again (15-20 s), e.g. after its summaries were redone."""
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "date is YYYY-MM-DD")
    try:
        r = await write_recap(date)
    except Exception as e:
        log.warning("recap %s failed: %r", date, e)
        raise HTTPException(502, f"模型调用失败：{e!r}"[:300])
    if not r:
        raise HTTPException(404, "那天没有摘要")
    return r


@app.get("/api/tags")
async def tags():
    return tag_counts()


@app.get("/api/conversation/{conv_id}")
async def conversation(conv_id: str):
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    return {**c, "summary": db.summary(conv_id), "timeline": live.public(t) if (t := db.timeline(conv_id)) else None, "feedback": db.feedback(conv_id),
            "rows": conversation_rows(c)}


@app.post("/api/conversation/{conv_id}/feedback")
async def add_feedback(conv_id: str, body: dict):
    """A correction in the owner's words (wrongly greyed as media, a wrong speaker, a bad summary); collected
    and read back in batches to fix the prompt, never acted on one by one."""
    text = (body.get("text") or "").strip()
    if not find_conversation(conv_id):
        raise HTTPException(404, "no such conversation")
    if not text or len(text) > 2000:
        raise HTTPException(400, "empty or too long")
    db.add_feedback(conv_id, text)
    return db.feedback(conv_id)


@app.get("/api/feedback")
async def feedback():
    """Every correction, newest first, with the conversation's time and title for the read-back."""
    return [{**f, "start": (c := find_conversation(f["conv"]) or {}).get("start"), "title": (db.summary(f["conv"]) or {}).get("title")}
            for f in db.feedback()]


@app.post("/api/conversation/{conv_id}/summarize")
async def resummarize(conv_id: str):
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    queue_summary(c)
    return {"status": "pending"}


@app.get("/api/names")
async def names():
    """Names already in use, most conversations first: suggestions for a rename."""
    return list(db.names())


@app.post("/api/conversation/{conv_id}/rename")
async def rename(conv_id: str, body: dict):
    frm, to = body.get("from", ""), (body.get("to") or "").strip()
    return await apply_rename(conv_id, frm, to)


async def apply_rename(conv_id: str, frm: str, to: str) -> dict:
    """One label of one conversation becomes a name: its rows, the participants, its voice and pins change in one
    transaction, the summary is redone, and when the recording is still here up to ENROLL sentences of it teach the
    diarist that voice for later conversations."""
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    if not to or to == "S?" or len(to) > 20 or to == frm:
        raise HTTPException(400, "bad name")
    hit = [r for r in conversation_rows(c) if r.get("speaker") == frm]
    if not hit:
        raise HTTPException(400, f"no sentence of {frm}")
    c["participants"] = list(dict.fromkeys(to if p == frm else p for p in c["participants"]))
    with db.tx() as t:
        t.execute("UPDATE sentences SET speaker = ? WHERE start >= ? AND start <= ? AND speaker = ?", (to, c["start"], c["end"], frm))
        t.execute("UPDATE conversations SET participants = ? WHERE id = ?", (json.dumps(c["participants"], ensure_ascii=False), conv_id))
        if t.execute("SELECT 1 FROM voices WHERE conv = ? AND label = ?", (conv_id, to)).fetchone():
            t.execute("DELETE FROM voices WHERE conv = ? AND label = ?", (conv_id, frm))  # a merge: the label's own voice stays
        else:
            t.execute("UPDATE voices SET label = ? WHERE conv = ? AND label = ?", (to, conv_id, frm))  # voices and pins follow
        t.execute("UPDATE OR REPLACE apart SET voice = ? WHERE voice = ?", (f"{conv_id}|{to}", f"{conv_id}|{frm}"))
        if h := db.name_hints(conv_id).get(conv_id):  # the names it was called go with it
            h["names"] = [{**x, **{k: to for k in ("who", "by") if x.get(k) == frm}} for x in h["names"]]
            db.put_name_hints(h, t)
    T().speakers["at"] = 0.0
    queue_summary(c, keep=True)
    learnt = 0
    if to != "我" and not re.fullmatch(r"S\d+", to) and state.get("embed"):  # S<n> means "not known": nothing to learn
        long = sorted((r for r in hit if (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds() >= 1.5),
                      key=lambda r: r["start"])[:ENROLL]
        es = []
        for r in long:
            try:
                x = np.frombuffer(await cut(r["start"], min(r["end"], (dt.datetime.fromisoformat(r["start"]) + dt.timedelta(seconds=15)).isoformat()), "s16le", r.get("source")), np.int16)
            except HTTPException:  # history conversations have no recording here
                continue
            es.append(await anyio.to_thread.run_sync(state["embed"], x, limiter=threads.INFER))
        if es:
            n0, e0 = db.people().get(to, (0, np.zeros(len(es[0]))))
            n = n0 + len(es)
            mean = (e0 * n0 + np.sum(es, 0)) / n
            db.conn().execute("INSERT OR REPLACE INTO people VALUES (?, ?, ?)", (to, n, db.blob(mean)))
            for dia in T().dias:
                dia.people[to] = mean / np.linalg.norm(mean)
            learnt = len(es)
    note(f"对话 {c['start'][11:19]}：{frm} 改名为 {to}" + (f"，记住了 {learnt} 句的声纹" if learnt else ""), "state")
    return {"participants": c["participants"], "renamed": len(hit), "voiceprints": learnt}


ENROLL = 20

# speakers page: one voiceprint per conversation x label (history ones computed from the old recorder's audio and
# uploaded with a clip each; live ones computed here), grouped across conversations by speakers.py
SPEAKER_LINK = float(os.environ.get("SPEAKER_LINK", "0.5"))  # cosine (device-centred) to join an anonymous group
SPEAKER_SUGGEST = float(os.environ.get("SPEAKER_SUGGEST", "0.6"))  # cosine to offer an anonymous group to a name
# cosine (device-centred) to offer merging two labels of one conversation: in the history's named voices 0.4 % of
# two people's pairs reach 0.45, and 36 % of one person's (10-05)
MERGE_SUGGEST = float(os.environ.get("MERGE_SUGGEST", "0.45"))


def conv_start(conv_id: str) -> str:
    x = conv_id[1:]
    return f"{x[:4]}-{x[4:6]}-{x[6:8]}T{x[8:10]}:{x[10:12]}:{x[12:14]}"


def speaker_groups() -> list[dict]:
    """Takes seconds (every conversation and voice): kept 10 min, redone after a rename or new voices."""
    _speakers = T().speakers
    if _speakers["groups"] is not None and time.time() - _speakers["at"] < 600:
        return _speakers["groups"]
    convs = {c["id"]: c for c in all_conversations()}
    al, called = db.aliases(), {}
    for conv, h in db.name_hints().items():
        for x in hints.shown(h, (convs.get(conv) or {}).get("summary")):
            called.setdefault(f"{conv}|{x['who']}", set()).add(hints.canon(x["name"], al))
    vs, out = db.voices(), []
    for g in speakers.speakers(vs, SPEAKER_LINK, SPEAKER_SUGGEST, db.apart()):
        out.append({"id": g["id"], "name": g["name"], **describe(g["members"], convs, called),
                    "suggested": [{"id": speakers.key(s["members"][0]), "score": s["score"], **describe(s["members"], convs, called)}
                                  for s in g["suggested"]]})
    order = lambda g: (g["name"] is not None, g["name"] == "我", -g["count"])  # unnamed and frequent first, 我 last
    out.sort(key=order)
    _speakers.update(at=time.time(), groups=out, vec=speakers.centered(vs))
    return out


def describe(members: list[dict], convs: dict, called: dict[str, set[str]]) -> dict:
    ms = []
    for v in members:
        c = convs.get(v["conv"]) or {}
        s = (c.get("summary") or {})
        pts = next((p.get("points") or [] for p in s.get("people") or [] if p.get("who") == v["label"]), [])
        ms.append({"conv": v["conv"], "label": v["label"], "start": conv_start(v["conv"]), "n": v["n"], "speech_s": v["speech_s"],
                   "clip": v.get("clip"), "clip_start": v.get("clip_start"), "clip_end": v.get("clip_end"), "clip_text": v.get("clip_text"),
                   "title": s.get("title"), "points": pts[:2],
                   "with": [p for p in c.get("participants") or [] if p != v["label"]]})
    ms.sort(key=lambda m: m["start"], reverse=True)
    co, names = {}, {}
    for m in ms:
        for p in m["with"]:
            if not speakers.ANON.fullmatch(p):
                co[p] = co.get(p, 0) + 1
        for n in called.get(f"{m['conv']}|{m['label']}", ()):
            names.setdefault(n, []).append(m["conv"])
    return {"count": len(ms), "speech_s": round(sum(m["speech_s"] for m in ms), 1), "last": ms[0]["start"] if ms else None,
            "samples": ms[:: max(1, len(ms) // 4)][:4],  # spread over time: a group that mixes people sounds mixed
            "co": sorted(co, key=lambda k: -co[k])[:4], "members": ms,
            # what its voices were called (hints.py, aliases resolved), in the most conversations first
            "called": [{"name": n, "convs": cs} for n, cs in sorted(names.items(), key=lambda kv: -len(kv[1]))][:5]}


@app.get("/api/speakers")
async def speaker_list():
    """Without the member lists (most of the size; the home uplink is slow): GET /api/speakers/{id} has them."""
    bare = lambda g: {k: v for k, v in g.items() if k != "members"}
    return [{**bare(g), "suggested": [bare(x) for x in g["suggested"]]} for g in await anyio.to_thread.run_sync(speaker_groups, limiter=threads.DB)]


def find_group(gid: str, sid: str | None = None) -> dict:
    g = next((g for g in speaker_groups() if g["id"] == gid), None)
    if g and sid:
        g = next((x for x in g["suggested"] if x["id"] == sid), None)
    if not g:
        raise HTTPException(409, "说话人分组已经变了，刷新页面再试")
    return g


@app.get("/api/speakers/{gid}")
async def speaker_one(gid: str):
    return await anyio.to_thread.run_sync(find_group, gid, limiter=threads.DB)


def check_name(to: str):
    if not to or to == "S?" or len(to) > 20 or speakers.ANON.fullmatch(to):
        raise HTTPException(400, "bad name")


@app.post("/api/speakers/name")
async def speaker_name(body: dict):
    """Every member voice of a group (or of one of its suggestions) takes the name, in all of its conversations."""
    to = (body.get("name") or "").strip()
    check_name(to)
    done = 0
    for m in (await anyio.to_thread.run_sync(find_group, body["group"], body.get("suggestion"), limiter=threads.DB))["members"]:
        if m["label"] == to:
            continue
        try:
            await apply_rename(m["conv"], m["label"], to)
        except HTTPException as e:  # the conversation changed since the page loaded
            log.warning("speaker name %s %s: %s", m["conv"], m["label"], e.detail)
            continue
        done += 1
    return {"renamed": done}


@app.post("/api/speakers/detach")
async def speaker_detach(body: dict):
    """A voice that is not this speaker: a named one goes back to a fresh S<n> of its conversation; either way it is
    kept apart from the group from now on."""
    conv, label, group = body["conv"], body["label"], body["group"]
    key = f"{conv}|{label}"
    if not speakers.ANON.fullmatch(label):
        c = find_conversation(conv)
        if not c:
            raise HTTPException(404, "no such conversation")
        used = {int(p[1:]) for p in c["participants"] if re.fullmatch(r"S\d+", p)}
        new = f"S{max(used, default=0) + 1}"
        await apply_rename(conv, label, new)
        key = f"{conv}|{new}"
    db.conn().execute("INSERT OR IGNORE INTO apart VALUES (?, ?)", (key, group))
    T().speakers["at"] = 0.0
    return {"key": key}


def label_suggestions(c: dict, groups: list[dict], vec: dict, hs: dict | None, al: dict[str, str]) -> dict:
    """What the page offers for one conversation's labels, each applied only by a click (a rename): names it was called
    here (hints.py; a named label called by another name offers that name as an alias), two labels to merge (voices
    alike, or both called the same), and per unnamed label the named speaker its voice is near and the names its
    voice was called in other conversations."""
    cid, rows = c["id"], conversation_rows(c)
    n = {}
    for r in rows:
        n[r.get("speaker")] = n.get(r.get("speaker"), 0) + 1
    labels = [p for p in c["participants"] if p != "S?" and p in n]
    anon = lambda p: bool(speakers.ANON.fullmatch(p))
    names = [{**x, "canon": hints.canon(x["name"], al)} for x in hints.shown(hs, db.summary(cid)) if x["who"] in labels]
    names = [x for x in names if x["canon"] != x["who"]]  # a name it already has
    merge, seen = [], set()
    def offer(a, b, **why):
        """One person in two labels: the unnamed one into the named one, else the one with fewer rows into the other;
        two names were told apart by the owner. A label is offered into one other only, the first (most alike)."""
        if not anon(a) and not anon(b):
            return
        frm, to = (a, b) if anon(a) and (not anon(b) or n[a] < n[b]) else (b, a)
        if frm not in seen:
            seen.add(frm)
            merge.append({"from": frm, "to": to, **why})
    for score, a, b in speakers.close_pairs([f"{cid}|{p}" for p in labels], vec, MERGE_SUGGEST):
        offer(a.split("|", 1)[1], b.split("|", 1)[1], score=round(score, 2))
    by_name = {}
    for x in names:
        if anon(x["who"]):
            by_name.setdefault(x["canon"], set()).add(x["who"])
    for name, ls in by_name.items():
        ls = sorted(ls, key=lambda p: -n[p])
        for p in ls[1:]:
            offer(p, ls[0], name=name)
    where = {}  # voice key: (its group, the suggestion of a named group it is in)
    for g in groups:
        for m in g["members"]:
            where[f"{m['conv']}|{m['label']}"] = (g, None)
        for sg in g["suggested"]:
            for m in sg["members"]:
                where[f"{m['conv']}|{m['label']}"] = (g, sg)
    like, elsewhere = [], []
    for p in filter(anon, labels):
        g, sg = where.get(f"{cid}|{p}", (None, None))
        if sg:
            like.append({"who": p, "name": g["name"], "score": sg["score"]})
        for x in (sg or g or {}).get("called", []):
            if len(others := {v for v in x["convs"] if v != cid}) >= 2 and x["name"] != g["name"] and \
                    not any(y["who"] == p and y["canon"] == x["name"] for y in names):  # one other is too often a mixed group
                elsewhere.append({"who": p, "name": x["name"], "convs": len(others)})
    clips = {r[0]: {"clip": r[1], "clip_start": r[2], "clip_end": r[3]} for r in db.conn().execute(
        "SELECT label, clip, clip_start, clip_end FROM voices WHERE conv = ? AND e IS NOT NULL", (cid,))}  # to hear a label
    return {"names": names, "merge": merge, "like": like, "elsewhere": elsewhere, "clips": clips, "asked": hs is not None}


@app.get("/api/conversation/{conv_id}/speakers")
async def conversation_speakers(conv_id: str):
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    groups = await anyio.to_thread.run_sync(speaker_groups, limiter=threads.DB)
    return label_suggestions(c, groups, T().speakers["vec"], db.name_hints(conv_id).get(conv_id), db.aliases())


@app.get("/api/aliases")
async def alias_list():
    return db.aliases()


@app.post("/api/aliases")
async def alias_set(body: dict):
    """Another way a person is called (A哥 for A): hints with it suggest the name. No name drops the alias."""
    alias, name = (body.get("alias") or "").strip(), (body.get("name") or "").strip()
    if not alias or len(alias) > 20 or alias == name:
        raise HTTPException(400, "bad alias")
    if name:
        check_name(name)
        db.conn().execute("INSERT OR REPLACE INTO aliases VALUES (?, ?)", (alias, name))
    else:
        db.conn().execute("DELETE FROM aliases WHERE alias = ?", (alias,))
    T().speakers["at"] = 0.0
    return db.aliases()


@app.get("/api/person/{name}")
async def person(name: str):
    """Everything about one named speaker: their conversations newest first with what they said there, the todos they
    own, and what I promised them."""
    convs = [c for c in all_conversations() if name in (c.get("participants") or [])]
    out, co, tags, theirs, mine = [], {}, {}, [], []
    for c in convs:
        s = c["summary"] if (c["summary"] or {}).get("status") == "done" else {}
        pts = next((p.get("points") or [] for p in s.get("people") or [] if p.get("who") == name), [])
        out.append({"id": c["id"], "start": c["start"], "end": c["end"], "title": s.get("title"), "summary": s.get("summary"),
                    "points": pts, "participants": c["participants"], "speech_s": c.get("speech_s")})
        for p in c["participants"]:
            if p != name and not speakers.ANON.fullmatch(p):
                co[p] = co.get(p, 0) + 1
        for t in s.get("tags") or []:
            tags[t] = tags.get(t, 0) + 1
        for t in s.get("todos") or []:
            t = {**t, "conversation": c["id"], "title": s.get("title"), "start": c["start"]}
            if t.get("owner") == name:
                theirs.append(t)
            elif t.get("owner") == "我" and t.get("to") == name:  # to: prompt v4 on; older summaries have none
                mine.append(t)
    if not convs:
        raise HTTPException(404, "no conversation with this name")
    top = lambda d, n: sorted(d, key=lambda k: -d[k])[:n]
    pf = db.profile(name)
    return {"name": name, "count": len(convs), "first": convs[-1]["start"], "last": convs[0]["start"],
            "aliases": sorted(a for a, n in db.aliases().items() if n == name),
            "profile": pf, "profile_newer": sum(c["start"] > pf["upto"] for c in convs) if pf and pf.get("upto") else 0,
            "with": top(co, 6), "tags": top(tags, 8), "theirs": theirs, "mine": mine, "conversations": out}


def mentions(name: str, limit: int = 30, per_conv: int = 3) -> list[dict]:
    """Rows of other speakers holding the name or one of its aliases, newest first, each with two rows either side and
    its conversation; at most per_conv from one conversation (a name said over and over in one talk)."""
    terms = [name] + [a for a, n in db.aliases().items() if n == name]
    out, per = [], {}
    for r in db.conn().execute(f"SELECT start, source FROM sentences WHERE ({' OR '.join('instr(text, ?)' for _ in terms)}) "
                               "AND coalesce(speaker, '') != ? ORDER BY start DESC", (*terms, name)):
        c = db.conn().execute("SELECT c.id, s.body FROM conversations c LEFT JOIN summaries s ON s.conversation = c.id WHERE c.start <= ? "
                              "AND c.end >= ? AND (c.device IS NULL OR c.device IS ?) ORDER BY c.start DESC", (r[0], r[0], r[1])).fetchone()
        if not c or per.get(c[0], 0) >= per_conv:
            continue
        per[c[0]] = per.get(c[0], 0) + 1
        t = dt.datetime.fromisoformat(r[0])
        near = db.sentences((t - dt.timedelta(minutes=1)).isoformat(), (t + dt.timedelta(minutes=1)).isoformat(), source=r[1])
        i = next((k for k, x in enumerate(near) if x["start"] == r[0]), 0)
        out.append({"conv": c[0], "title": (json.loads(c[1]) if c[1] else {}).get("title"), "at": r[0],
                    "rows": [x for x in near[max(0, i - 2):i + 3] if x.get("text")]})
        if len(out) == limit:
            break
    return out


async def make_profile(name: str) -> dict:
    """The person's profile from their 40 latest conversations, their todos and mine to them, and 30 mentions."""
    p = await person(name)
    ms = await anyio.to_thread.run_sync(mentions, name, limiter=threads.DB)
    pf = await profiles.profile(name, p["conversations"][:40], (p["theirs"] + p["mine"])[:30], ms)
    db.put_profile(pf)
    return pf


@app.post("/api/person/{name}/profile")
async def person_profile(name: str):
    """Made (or made again) now: one chat call, some seconds."""
    try:
        return await make_profile(name)
    except HTTPException:
        raise
    except Exception as e:  # the model: HTTP errors, a reply that is not JSON
        raise HTTPException(502, f"生成失败：{e!r}"[:300])


@app.get("/api/voice-clip/{name}")
async def voice_clip(name: str):
    p = T().data / "voices" / "clips" / name  # the history voices' samples
    if not re.fullmatch(r"[\w.-]+\.ogg", name) or not p.is_file():
        raise HTTPException(404, "no such clip")
    return FileResponse(p, media_type="audio/ogg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def voices_loop():
    """Voiceprints for the labels of closed live conversations (history ones are uploaded): up to 4 sentences of
    >= 1.5 s per label with >= 3 sentences. A conversation without a recording here is marked and not retried."""
    while True:
        await anyio.to_thread.run_sync(speaker_groups, limiter=threads.DB)  # warm: the page's first load would otherwise wait seconds
        await anyio.sleep(300)
        if not state.get("embed"):
            continue
        seen = db.voiced_conversations()
        since = (dt.date.today() - dt.timedelta(days=3)).isoformat()
        for c in all_conversations():
            s = c["summary"]
            if c["start"] < since or c["id"] in seen or not s or s["status"] == "pending":  # the summary tells media apart
                continue
            rows = conversation_rows(c)
            media = summary.media_labels(rows, s.get("media_spans") or [])
            by = {}
            for r in rows:
                if r.get("speaker") and r["speaker"] != "S?" and r["speaker"] not in media:
                    by.setdefault(r["speaker"], []).append(r)
            out = []
            for label, rs in by.items():
                if len(rs) < 3:
                    continue
                dur = lambda r: (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds()
                long = sorted((r for r in rs if dur(r) >= 1.5), key=dur, reverse=True)[:4]
                es, ls = [], []
                for r in long:
                    try:
                        x = np.frombuffer(await cut(r["start"], min(r["end"], (dt.datetime.fromisoformat(r["start"]) + dt.timedelta(seconds=15)).isoformat()), "s16le", r.get("source")), np.int16)
                    except HTTPException:
                        continue
                    es.append(await anyio.to_thread.run_sync(state["embed"], x, limiter=threads.INFER))
                    if (lv := selfvoice.level(x)) is not None:
                        ls.append(lv)
                if es:
                    e = np.mean(es, 0)
                    best = min(long, key=lambda r: abs(dur(r) - 6))
                    out.append({"conv": c["id"], "label": label, "device": "live", "n": len(rs), "speech_s": round(sum(map(dur, rs)), 1),
                                "k": len(es), "e": e / np.linalg.norm(e), "level": round(float(np.median(ls)), 2) if ls else None,
                                "clip_start": best["start"], "clip_end": min(best["end"], (dt.datetime.fromisoformat(best["start"]) + dt.timedelta(seconds=10)).isoformat()),
                                "clip_text": best.get("text")})
            with db.tx() as t:
                db.insert_voices(t, out or [{"conv": c["id"]}])
            T().speakers["at"] = 0.0
        try:
            await self_round()
        except Exception:
            log.exception("self voice round")


# 「哪个是你？」(selfvoice.py): on an instance without 我's voiceprint a card lets the user pick their own voice;
# the pick is written as that voiceprint and the pipelines take it from their next conversation on
CLIP_CONVS = 6  # a candidate's clips come from its biggest conversations: a sentence decoded costs ~5 CPU-s of ReDimNet2


def owner_npz() -> Path:
    return T().data / "owner-redimnet2.npz"


async def self_round():
    """After the voices of closed conversations came in: pop the card when it is due (selfvoice.due). Off while 我 has a
    voiceprint (a hand-made npz, or the one picked here)."""
    st = db.kv("selfvoice") or {}
    if owner_npz().exists() or st.get("card"):  # the card up waits for the user; nothing else changes st meanwhile
        return
    vs = db.voices()
    if not vs:
        return
    now = dt.datetime.now()
    st.setdefault("first_at", now.isoformat(timespec="seconds"))
    cands, n = await anyio.to_thread.run_sync(selfvoice.candidates, vs, limiter=threads.DB)
    if why := selfvoice.due(st, cands, n, now):
        st["card"] = await self_card(cands[:selfvoice.SHOW], vs, why, n)
        st.pop("wait", None)
        log.info("self voice card (%s): %s", why, [(c["p"], c["convs"], len(c["clips"])) for c in st["card"]["candidates"]])
    db.put_kv("selfvoice", st)
    if why:
        note("「哪个是你？」可以确认了：到「对话」页听一听，选出你自己的声音", "state")
        await broadcast({"type": "self"})


async def self_card(cands: list[dict], vs: list[dict], why: str, n: int) -> dict:
    """The card: per candidate its tier, conversations, speech, voiceprint and 3 clips (selfvoice.pick) from its
    biggest conversations, decoded and embedded here (a few minutes of CPU, once per card)."""
    lvl = {(v["conv"], v["label"]): v.get("level") for v in vs}
    banks, out = [c["bank"] for c in cands], []
    for c in cands:
        pool = []
        for m in sorted(c["members"], key=lambda m: -m["speech_s"])[:CLIP_CONVS]:
            if not (conv := db.conversation(m["conv"])):
                continue
            for r in await anyio.to_thread.run_sync(selfvoice.shortlist, conversation_rows(conv), m["label"], limiter=threads.DB):
                try:
                    x = np.frombuffer(await cut(r["start"], r["end"], "s16le", r.get("source")), np.int16)
                except HTTPException:
                    continue
                pool.append({**r, "conv": m["conv"], "lvl": selfvoice.level(x), "lmed": lvl.get((m["conv"], m["label"])),
                             "e": await anyio.to_thread.run_sync(state["embed"], x, limiter=threads.INFER)})
        clips = selfvoice.pick(pool, c["bank"], [b for b in banks if b is not c["bank"]])
        out.append({"tier": selfvoice.tier(c["p"]), "p": round(c["p"], 3), "convs": c["convs"], "speech_s": round(c["speech_s"]),
                    "bank": [float(x) for x in c["bank"]],
                    "clips": [{"start": r["start"], "end": r["end"], "device": r.get("source"), "text": r.get("text")} for r in clips]})
    return {"at": dt.datetime.now().isoformat(timespec="seconds"), "why": why, "convs": n, "candidates": out}


@app.get("/api/self")
async def self_get():
    """The card while it is up: candidates most likely first (none preselected), each with a tier instead of a %."""
    st = db.kv("selfvoice") or {}
    c = None if owner_npz().exists() else st.get("card")
    return {"card": c and {**c, "candidates": [{k: v for k, v in x.items() if k not in ("bank", "p")} for x in c["candidates"]]}}


@app.post("/api/self")
async def self_answer(body: dict):
    """The answer to the card: a candidate's index becomes 我's voiceprint (owner-redimnet2.npz), taken by every
    pipeline from its next conversation on: the conversations before keep their labels and summaries. "none" (都不是)
    and "later" (以后再说) put the card away until selfvoice.due brings it again. Every answer is logged with the card's
    probabilities: what POP_P and the tiers are to be checked against."""
    st = db.kv("selfvoice") or {}
    card, a, now = st.get("card"), body.get("answer"), dt.datetime.now()
    if not card or owner_npz().exists():
        raise HTTPException(409, "没有待确认的声音，刷新页面再试")
    if a == "none":
        st["wait"] = {"convs": len({v["conv"] for v in db.voices()})}
    elif a == "later":
        st["wait"] = {"until": (now + dt.timedelta(seconds=selfvoice.LATER_S)).isoformat(timespec="seconds")}
    elif isinstance(a, int) and not isinstance(a, bool) and 0 <= a < len(card["candidates"]):
        bank = selfvoice.unit(card["candidates"][a]["bank"]).astype(np.float32)
        tmp = T().data / "owner-redimnet2.tmp.npz"
        np.savez(tmp, self=bank)
        os.replace(tmp, owner_npz())  # whole or not at all: a restart loads it like a hand-made one
        T().owner = {"self": bank}
        st["confirmed"] = {"at": now.isoformat(timespec="seconds"), "pick": a}
    else:
        raise HTTPException(400, "bad answer")
    st["log"] = st.get("log", []) + [{"at": now.isoformat(timespec="seconds"), "why": card["why"], "convs": card["convs"],
                                      "p": [x["p"] for x in card["candidates"]], "answer": a}]
    st["card"] = None
    db.put_kv("selfvoice", st)
    if "confirmed" in st:
        note(f"「我」的声音已确认（第 {a + 1} 个）：从下一段对话起认出「我」，已有的对话不变", "state")
    await broadcast({"type": "self"})
    return {"ok": True}


@app.get("/api/audio")
async def audio_wav(start: str, end: str, device: str | None = None):
    """WAV of [start, end] (+0.2 s each side) cut from the uploaded pieces of `device` (any when not given) covering it."""
    wav = await cut(start, end, "wav", device)
    return Response(wav, media_type="audio/wav")


@app.get("/api/listen")
async def listen(device: str, after: int | None = None):
    """Listening in on a device (the live page polls it): its audio after `after` (unix ms, the X-After of the answer
    before; none: its newest piece) as WAV, X-Start the first piece's start; 204 when there is nothing newer. Decoded
    here, as /api/audio is: pieces are Ogg Opus (Safari before 18.4 cannot decode it) or the iPhone's AAC (each with
    its encoder's delay, trimmed only by decode()); WAV plays in every browser."""
    got = await anyio.to_thread.run_sync(audio.newer, device, after, limiter=threads.MEDIA)
    if got is None:
        return Response(status_code=204)
    start, last, x = got
    return Response(audio.wav(x), media_type="audio/wav", headers={"X-Start": str(start), "X-After": str(last), **NO_CACHE})


async def cut(start: str, end: str, fmt: str, device: str | None = None) -> bytes:
    a, b = dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)
    out = await anyio.to_thread.run_sync(audio.cut, a, b, fmt, device, limiter=threads.MEDIA) if 0 < (b - a).total_seconds() <= 120 else None
    if out is None:
        raise HTTPException(404, "no recording covers this time")
    return out


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Its tenant's pushes (Tenants let it in by its token)."""
    await ws.accept()
    T().clients.add(ws)
    try:
        while True:
            try:
                m = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("type") == "watch":  # a page in front: every 20 s; wake: it just came to the front
                T().watch_until = time.time() + 60
                if m.get("wake"):
                    T().wake_at = time.time()
    except WebSocketDisconnect:
        T().clients.discard(ws)


class TenantTag(logging.Filter):
    """%(tenant)s: "[<id>] " of the tenant a line is about when the process has several, else nothing (one tenant's
    log reads as before)."""

    def filter(self, record):
        t = tenant.current.get(None)
        record.tenant = f"[{t.id}] " if t and len(TENANTS) > 1 else ""
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8770")))
    args = ap.parse_args()
    missing = []
    engines = {e for e in (ENGINE, DRAFT_ENGINE, ENGINE_FALLBACK) if e}
    if engines - PROVIDERS.keys() - {"qwen-stream"} or "qwen-stream" in (ENGINE, ENGINE_FALLBACK):
        raise SystemExit(f"ENGINE, DRAFT_ENGINE, ENGINE_FALLBACK must be one of {', '.join(PROVIDERS)} (DRAFT_ENGINE also qwen-stream), "
                         f"not {', '.join(engines - PROVIDERS.keys())}")
    if "volc" in engines and not os.environ.get("VOLC_ASR_KEY"):
        missing.append("VOLC_ASR_KEY")
    if engines & {"stepfun", "stepfun-sse"} and not os.environ.get("STEPFUN_API_KEY"):
        missing.append("STEPFUN_API_KEY")
    if engines & {"qwen", "qwen-stream"} and not os.environ.get("DASHSCOPE_API_KEY"):
        missing.append("DASHSCOPE_API_KEY")
    if file := engines & {"stepfun", "stepfun-file"}:  # diarize(): your own upload bridge and file-mode ASR service
        missing += [f"{k} (for {', '.join(sorted(file))})" for k in
                    ("ASR_UPLOAD_BRIDGE_URL", "ASR_UPLOAD_BRIDGE_TOKEN", "ASR_SERVICE_URL", "ASR_SERVICE_BASIC_AUTH") if not os.environ.get(k)]
    missing += [k for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"not set: {', '.join(missing)} (see .env.example)")
    h = logging.StreamHandler()
    h.addFilter(TenantTag())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(tenant)s%(message)s", handlers=[h])
    infer.fetch()  # a first start: the missing model files, downloaded
    infer.check()  # every model file there and the pinned one, before anything starts
    log.info("models ok: %d files in %s", len(infer.MODELS), infer.DIR)
    TENANTS[:] = tenant.load()
    for t in TENANTS:
        tenant.call(t, db.init)
    firmware.DIR = TENANTS[0].data / "firmware"  # every tenant's: the first one's
    commit = HERE / "COMMIT"  # written by deploy.sh: the server has no git checkout
    state["commit"] = commit.read_text().strip() if commit.exists() else \
        subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    log.info("tenants: %s", ", ".join(t.id for t in TENANTS))
    log.info("ready on %s:%d", os.environ.get("HOST", "127.0.0.1"), args.port)

    import uvicorn
    # a page's WebSocket would hold a stop for good: 5 s, then the lifespan cancels every task (pipelines resume from
    # their finalized time on the next start)
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=args.port, log_level="warning", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
