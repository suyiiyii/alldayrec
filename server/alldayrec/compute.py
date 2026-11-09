"""The heavy pure computations behind one interface (Compute): int16 16 kHz samples in, a voiceprint or a yes/no out,
nothing of a tenant's (no database, no files, no names). Every caller reaches them as core.state's "embed" and "music"
from a thread under threads.INFER, so a backend is only a blocking function.

- local (the default, INFER_URL unset): in this process on onnxruntime, exactly as before (infer.embedder, music.heard).
- remote (INFER_URL=https://infer.example.com): POSTs the samples to alldayrec-infer (infer_server.py) with INFER_TOKEN,
  INFER_TIMEOUT s a try, RETRIES more tries; when they all fail, INFER_FALLBACK=local (the default) computes it here and
  logs a warning, none raises as a local failure would. lr_infer_calls_total{op,result} counts each.
"""
import logging
import os
import threading
from collections import Counter
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

RETRIES = 1  # tries after the first, the same request: a restart or a dropped connection, not a slow model
CALLS: Counter = Counter()  # (op, result) -> calls; result: remote, retried, local (after a remote failure), failed


class Compute(Protocol):
    def embed(self, x: np.ndarray) -> np.ndarray:
        """int16 samples -> unit voiceprint (192 float32), ReDimNet2 B6."""

    def music(self, x: np.ndarray) -> bool:
        """int16 samples -> whether they are music (music.heard)."""

    def segment(self, x: np.ndarray) -> np.ndarray:
        """int16 samples of one 10 s chunk -> pyannote-segmentation-3.0's frames (its 7 powerset log-probabilities a
        ~17 ms frame): reserved for the speaker-change cut, which runs the model in this process until it moves here."""


class Local:
    def __init__(self):
        from alldayrec import infer, music
        self.embed, self.music = infer.embedder(), music.heard  # the session built here, before the event loop starts

    def segment(self, x: np.ndarray) -> np.ndarray:
        raise NotImplementedError("segment: not behind Compute yet")


class Remote:
    def __init__(self, url: str, token: str, timeout: float, fallback: str):
        import httpx2
        self.url, self.fallback, self._local, self._lock = url.rstrip("/"), fallback, None, threading.Lock()
        self.http = httpx2.Client(timeout=timeout, headers={"Authorization": f"Bearer {token}"})  # thread-safe, keeps connections

    def local(self) -> Local:
        with self._lock:  # built at the first fallback: the models stay out of memory while the service answers
            if self._local is None:
                self._local = Local()
            return self._local

    def _post(self, op: str, x: np.ndarray) -> dict | None:
        """The service's answer, None when it failed and INFER_FALLBACK=local (the caller computes it here)."""
        body = np.ascontiguousarray(x, "<i2").tobytes()
        for i in range(1 + RETRIES):
            try:
                r = self.http.post(f"{self.url}/{op}", content=body, headers={"Content-Type": "application/octet-stream"})
                r.raise_for_status()
                CALLS[op, "remote" if i == 0 else "retried"] += 1
                return r.json()
            except Exception as e:  # a timeout, a refused connection, a 401 / 5xx
                err = f"{type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}".strip()  # never the token
                if getattr(getattr(e, "response", None), "status_code", 0) in (400, 401, 413):
                    break  # the same request fails the same way
        if self.fallback == "none":
            CALLS[op, "failed"] += 1
            raise RuntimeError(f"infer service {op}: {err}")
        log.warning("infer service %s: %s; computed here (INFER_FALLBACK=local)", op, err)
        CALLS[op, "local"] += 1
        return None

    def embed(self, x: np.ndarray) -> np.ndarray:
        r = self._post("embed", x)
        return self.local().embed(x) if r is None else np.asarray(r["embedding"], np.float32)

    def music(self, x: np.ndarray) -> bool:
        r = self._post("music", x)
        return self.local().music(x) if r is None else bool(r["music"])

    def segment(self, x: np.ndarray) -> np.ndarray:
        raise NotImplementedError("segment: not behind Compute yet")


def load() -> Compute:
    """INFER_URL set: Remote, else Local. SystemExit on a setting that cannot work."""
    url = os.environ.get("INFER_URL", "")
    if not url:
        return Local()
    fallback = os.environ.get("INFER_FALLBACK", "local")
    if fallback not in ("local", "none") or not os.environ.get("INFER_TOKEN"):
        raise SystemExit("INFER_URL needs INFER_TOKEN, and INFER_FALLBACK must be local or none")
    return Remote(url, os.environ["INFER_TOKEN"], float(os.environ.get("INFER_TIMEOUT", "30")), fallback)
