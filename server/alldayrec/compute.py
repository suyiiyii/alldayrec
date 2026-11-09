"""The heavy pure computations behind one interface (Compute): int16 16 kHz samples in, a voiceprint, a yes/no or a
chunk's speaker-change frames out, nothing of a tenant's (no database, no files, no names). Every caller reaches them as
core.state's "embed", "music" and "turns" (transcript.turns over segment) from a thread under threads.INFER, so a
backend is only a blocking function.

- local (the default, INFER_URL unset): in this process on onnxruntime, exactly as before (infer.embedder, music.heard,
  transcript.segment).
- remote (INFER_URL=https://infer.example.com): POSTs the samples to alldayrec --infer (infer_server.py) with
  INFER_TOKEN, 2 s to connect and INFER_TIMEOUT s to answer. This process then never loads those models (nor downloads
  them, served()). A call that fails (refused, a timeout, any 4xx or 5xx, an answer that is no unit DIM-float
  voiceprint, no SEG_FRAMES x 7 finite frames) waits for the service: it tries again after BACKOFF_S, doubling to its
  last, until one is answered; no sentence is lost or skipped, its device's pipeline waits (uploads are still taken and
  stored). Calls meanwhile sleep to the next try instead of each waiting out a timeout. An error is logged at most every LOG_EVERY_S; lr_infer_down_seconds says how long the
  service has been failing, lr_infer_calls_total{op,result} counts calls (remote, retried) and failed tries (error).
  At start load() asks the service's /health (with the token) for its model files and DIM: other than this server's,
  every call fails as above until the service is fixed (each try asks again: no restart needed). The server stopping
  (STOP) ends a call waiting for the service with Stopping instead of waiting on to a SIGKILL. A 413 (samples over the
  service's limit: no try can help) is no failure of the service: an error logged, the call raises.
"""
import logging
import os
import threading
import time
from collections import Counter
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

DIM = 192  # ReDimNet2 B6's voiceprint
BACKOFF_S = (2.0, 60.0)  # the wait after a first failure, doubling up to the last
LOG_EVERY_S = 60
CALLS: Counter = Counter()  # (op, result) -> calls; result: remote, retried (after failed tries), error (a failed try)
REMOTE = None  # the process's Remote when INFER_URL is set (load()): its down time for monitor
STOP = threading.Event()  # set by the server's lifespan as it stops: a call waiting for the service raises Stopping


class Stopping(BaseException):
    """The server stops while a call waits for the service. No Exception: no caller takes it for a failure of its
    own (late.processor's except would spend the span's tries)."""


class Compute(Protocol):
    def embed(self, x: np.ndarray) -> np.ndarray:
        """int16 samples -> unit voiceprint (DIM float32), ReDimNet2 B6."""

    def music(self, x: np.ndarray) -> bool:
        """int16 samples -> whether they are music (music.heard)."""

    def segment(self, x: np.ndarray) -> np.ndarray:
        """int16 samples, at most 10 s -> pyannote's frames (transcript.segment)."""


class Local:
    def __init__(self):
        from alldayrec import infer, music, transcript
        self.embed, self.music = infer.embedder(), music.heard  # the session built here, before the event loop starts
        self.segment = transcript.segment  # its session at the first call, as before


def models() -> dict:
    """The model files behind Compute and their sha256: the service's must be this server's."""
    from alldayrec import infer
    return {n: infer.MODELS[n] for n in (infer.EMB, infer.YAMNET, infer.SEG)}


def served() -> set:
    """The model files this process does without: those behind Compute when INFER_URL is set."""
    return set(models()) if os.environ.get("INFER_URL") else set()


def voiceprint(r: dict) -> np.ndarray:
    v = np.asarray(r["embedding"], np.float32)
    if v.shape != (DIM,) or not np.isfinite(v).all() or abs(float(np.linalg.norm(v)) - 1) > 1e-3:
        raise ValueError(f"not a unit {DIM}-float voiceprint: shape {v.shape}")
    return v


def frames(r: dict) -> np.ndarray:
    from alldayrec.transcript import SEG_FRAMES
    f = np.asarray(r["frames"], np.float32)
    if f.shape != (SEG_FRAMES, 7) or not np.isfinite(f).all():
        raise ValueError(f"not {SEG_FRAMES} x 7 frames: shape {f.shape}")
    return f


class Remote:
    def __init__(self, url: str, token: str, timeout: float):
        import httpx2
        self.url, self._lock, self.ok = url.rstrip("/"), threading.Lock(), True  # ok: the models checked (same_models)
        self.down_since, self.next_try, self.wait, self.logged = None, 0.0, 0.0, -LOG_EVERY_S
        # trust_env=False: the service is on a private network, never through a proxy from the environment
        self.http = httpx2.Client(timeout=httpx2.Timeout(timeout, connect=2), trust_env=False,
                                  headers={"Authorization": f"Bearer {token}"})  # thread-safe, keeps connections

    def down_seconds(self) -> float:
        return 0.0 if self.down_since is None else time.monotonic() - self.down_since

    def failed(self, op: str, e: Exception):
        """A failed try: the next one after the backoff (one doubling per round of tries), an error log a minute."""
        status = getattr(getattr(e, "response", None), "status_code", 0)
        err = f"{type(e).__name__} {status or ''}".strip() if not isinstance(e, ValueError) else str(e)  # never the token
        with self._lock:
            now = time.monotonic()
            if self.down_since is None:
                self.down_since, self.wait = now, BACKOFF_S[0]
            elif now >= self.next_try:  # not another of the same round's calls
                self.wait = min(self.wait * 2, BACKOFF_S[1])
            self.next_try = max(self.next_try, now + self.wait)
            CALLS[op, "error"] += 1
            if now - self.logged >= LOG_EVERY_S:
                self.logged = now
                log.error("infer service %s: %s%s; failing for %.0f s, calls wait (next try in %.0f s)", op, err,
                          ", INFER_TOKEN is not the service's" if status == 401 else ", check INFER_URL" if status == 404
                          else "", now - self.down_since, self.wait)

    def _post(self, op: str, x: np.ndarray, parse):
        """parse(the service's answer), once one comes: every failure is tried again (failed())."""
        body, tries = np.ascontiguousarray(x, "<i2").tobytes(), 0
        while True:
            while (w := self.next_try - time.monotonic()) > 0:  # the service failing: no try before the backoff's
                if STOP.wait(w):
                    raise Stopping
            try:
                if not self.ok:
                    self.ok = self.same_models()
                r = self.http.post(f"{self.url}/{op}", content=body, headers={"Content-Type": "application/octet-stream"})
                r.raise_for_status()
                out = parse(r.json())
            except Exception as e:  # refused, a timeout, a 4xx / 5xx, another model, a wrong answer
                if getattr(getattr(e, "response", None), "status_code", 0) == 413:  # this call's samples: no try helps
                    CALLS[op, "error"] += 1
                    log.error("infer service %s: 413, %.1f s of samples over its limit; not tried again", op,
                              len(x) / 16000)
                    raise
                self.failed(op, e)
                tries += 1
                continue
            self._back()
            CALLS[op, "retried" if tries else "remote"] += 1
            return out

    def _back(self):
        with self._lock:
            if self.down_since is not None:
                log.warning("infer service back after %.0f s", time.monotonic() - self.down_since)
            self.down_since, self.next_try, self.wait = None, 0.0, 0.0

    def up(self) -> bool:
        """For a caller waiting for the service with no call of its own (the late processor, before it claims a span):
        True unless it is failing or unchecked since start (ok). Then, a try due, /health asked once (a failure is a
        failed try, the backoff as for a call)."""
        if self.ok and self.down_since is None:
            return True
        if time.monotonic() < self.next_try:
            return False
        try:
            self.ok = self.same_models()
        except Exception as e:
            self.failed("health", e)
            return False
        self._back()
        return True

    def embed(self, x: np.ndarray) -> np.ndarray:
        return self._post("embed", x, voiceprint)

    def music(self, x: np.ndarray) -> bool:
        return self._post("music", x, lambda r: bool(r["music"]))

    def segment(self, x: np.ndarray) -> np.ndarray:
        return self._post("segment", x, frames)

    def same_models(self) -> bool:
        """GET /health with the token; ValueError when its model files or DIM are not this server's."""
        r = self.http.get(f"{self.url}/health")
        r.raise_for_status()
        h = r.json()
        if h.get("models") != models() or h.get("dim") != DIM:
            raise ValueError(f"the service has other models ({h.get('models')}, dim {h.get('dim')}) than this server "
                             f"({models()}, dim {DIM})")
        return True


def load() -> Compute:
    """INFER_URL set: Remote (its models checked once here: a failure is logged, and the calls wait), else Local.
    SystemExit on a setting that cannot work."""
    global REMOTE
    url = os.environ.get("INFER_URL", "")
    if not url:
        return Local()
    if not os.environ.get("INFER_TOKEN"):
        raise SystemExit("INFER_URL needs INFER_TOKEN")
    REMOTE = r = Remote(url, os.environ["INFER_TOKEN"], float(os.environ.get("INFER_TIMEOUT", "30")))
    try:
        r.same_models()
    except Exception as e:
        r.ok = False
        r.failed("health", e)
        r.down_since = None  # down once a call fails: a service started after this server, no call yet, is not down
    return r
