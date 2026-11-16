"""The scene models (sound scores of AudioSet's classes) on onnxruntime, without torch, like music.py: int16 16 kHz
samples in, each frame's scores out, nothing of a tenant's (sounds.py keeps them, docs: reference/models).

SOUND_MODEL=mn10_as,yamnet (default empty: none) names the ones that run (MODELS); the first is the one trim.py's gate
looks at. A model's file is downloaded and checked only when named (infer.unused). scene() runs here (1 thread), or
with INFER_URL on the service's POST /scene/<model> (infer_server.py) through a compute.Remote of its own: its
backoff and its health check are not the voiceprints', a service without /scene holds up only scene scores.
"""
import base64
import math
import os
from functools import cache

import numpy as np

from alldayrec import compute, infer

SR = 16000
# name: its file (infer.MODELS), classes (AudioSet's, the model's order), frames (window and hop, samples at SR),
# days its frames' top K are kept (None: always; the minutes always).
MODELS = {
    "mn10_as": dict(file=infer.MN10, classes=527, window=10 * SR, hop=10 * SR, keep_days=None),  # EfficientAT, 10 s windows
    "yamnet": dict(file=infer.YAMNET, classes=521, window=15360, hop=7680, keep_days=30),  # 0.96 s frames every 0.48 s
}
YAMNET_CALL = 125  # YAMNet frames per call (60 s): a 300 s call peaked 540 MB over the model, 60 s 135 MB (P0)


def names() -> list[str]:
    return [n for n in os.environ.get("SOUND_MODEL", "").replace(" ", "").split(",") if n]


def check():
    """SystemExit on a SOUND_MODEL that names no model of MODELS."""
    if bad := [n for n in names() if n not in MODELS]:
        raise SystemExit(f"SOUND_MODEL: unknown {', '.join(bad)} (one or more of {', '.join(MODELS)})")


def model_id(name: str) -> str:
    return f"{name}@{infer.MODELS[MODELS[name]['file']][:12]}"


def files() -> dict:
    """The model files of SOUND_MODEL and their sha256: the service's /health "scene" must have them."""
    return {MODELS[n]["file"]: infer.MODELS[MODELS[n]["file"]] for n in names()}


def windows(name: str, n: int) -> list[tuple[int, int]]:
    """(start, length) in samples of each frame the model gives for n samples. YAMNet: one every hop, as its own
    framing pads (features.py: 0.975 s at least, then whole hops). mn10_as: windows of 10 s; a last one shorter than
    2 s goes into the one before (10-12 s), alone (all of n under 2 s) it is n."""
    m = MODELS[name]
    if name == "yamnet":
        return [(i * m["hop"], m["window"]) for i in range(1 + math.ceil(max(0, n - 15600) / m["hop"]))]
    w = [(a, min(m["window"], n - a)) for a in range(0, n, m["hop"])]
    if len(w) > 1 and w[-1][1] < 2 * SR:
        w[-2:] = [(w[-2][0], w[-2][1] + w[-1][1])]
    return w


@cache
def _session(name: str):
    return infer.session(MODELS[name]["file"], 1)


def scores(name: str, x: np.ndarray) -> np.ndarray:
    """int16 samples -> the model's scores, len(windows(name, len(x))) x classes float32: compute.Local's scene."""
    s, f = _session(name), x.astype(np.float32) / 32768
    if name == "yamnet":  # in calls of YAMNET_CALL frames, each with the next frame's overlap: the same frames as one call
        step = YAMNET_CALL * MODELS[name]["hop"]
        n = len(windows(name, len(x)))
        return np.concatenate([s.run(None, {"wav": f[a:a + step + 15600 - MODELS[name]["hop"]]})[0][:YAMNET_CALL]
                               for a in range(0, max(len(f), 1), step)])[:n]
    return np.stack([s.run(None, {"wav": f[None, a:a + n]})[0].reshape(-1) for a, n in windows(name, len(x))])


def parse(name: str, n: int):
    """The service's answer to /scene/<name> for n samples -> scores (compute.Remote), ValueError when it is not those."""
    def f(r: dict) -> np.ndarray:
        shape = tuple(r["shape"])
        if shape != (len(windows(name, n)), MODELS[name]["classes"]):
            raise ValueError(f"not {len(windows(name, n))} x {MODELS[name]['classes']} scores: shape {shape}")
        v = np.frombuffer(base64.b64decode(r["scores"]), "<f2").astype(np.float32).reshape(shape)
        if not np.isfinite(v).all() or v.min() < 0 or v.max() > 1:
            raise ValueError("scores out of [0, 1]")
        return v
    return f


def encode(v: np.ndarray) -> dict:
    """scores -> /scene's answer."""
    return {"shape": list(v.shape), "scores": base64.b64encode(np.ascontiguousarray(v, "<f2").tobytes()).decode()}


@cache
def _remote() -> compute.Remote:
    return compute.Remote(os.environ["INFER_URL"], os.environ.get("INFER_TOKEN", ""), float(os.environ.get("INFER_TIMEOUT", "30")),
                          scene=files())


def scene(name: str, x: np.ndarray) -> np.ndarray:
    return _remote().scene(name, x) if os.environ.get("INFER_URL") else scores(name, x)
