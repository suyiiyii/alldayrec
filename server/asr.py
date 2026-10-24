"""The ASR providers (PROVIDERS) and the one way the pipelines call them (engine_segs): the audio squeezed and lifted,
sentences with times and speakers back, ENGINE_FALLBACK on a failure, every try in the calls table."""
import base64
import json
import logging
import os
import time

import anyio
import numpy as np

import db
import local_asr
import net
import qwen_asr
import squeeze
import stepfun_asr
import threads
from segmenter import SR

log = logging.getLogger("live")


ENGINE = os.environ.get("ENGINE") or "qwen"  # final layer's ASR provider (PROVIDERS): volc | qwen | stepfun | stepfun-sse | stepfun-file | local
DRAFT_ENGINE = os.environ.get("DRAFT_ENGINE") or ENGINE  # draft layer's (stepfun-sse: fast, no speakers, drafts are replaced anyway;
                                                         # qwen-stream: one streaming session while a page watches, stream_loop)
ENGINE_FALLBACK = os.environ.get("ENGINE_FALLBACK", "")  # a window the provider fails on (402, 451, 5xx, timeout) goes here; empty: none
SQUEEZE_KEEP_S = float(os.environ.get("SQUEEZE_KEEP_S", "3"))  # VAD silences longer than this go to the engine cut to this; 0: whole


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
