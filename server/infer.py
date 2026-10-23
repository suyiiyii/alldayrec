"""Speech detection and voiceprints on onnxruntime, in this process, without torch: FireRedVAD Stream-VAD and
ReDimNet2 B6, the same models as before (on the main instance's audio: the same utterances and squeezed seconds, cosine
>= 0.9999998, the same diarist labels). The files live in MODELS_DIR, each pinned by its sha256 below; at start fetch()
downloads the missing ones (sha256-checked), then check() stops the server when one is missing or different,
there is no fallback. Where they come from and how to make them: export_onnx.py.
"""
import hashlib
import logging
import os
import struct
import urllib.request
from functools import cache
from pathlib import Path

import kaldi_native_fbank as knf
import numpy as np
import onnxruntime as ort

from vad_postprocessor import StreamVadPostprocessor

DIR = Path(os.environ.get("MODELS_DIR") or "/opt/lr-models")
THREADS = int(os.environ.get("INFER_THREADS", "4"))  # onnxruntime threads per voiceprint call (threads.INFER: calls at once)
# The VAD hears its stream lifted to a level: its features take a fixed global CMVN, so speech 6-10 dB below the
# usual (a far lecture through a PA, a quieter speaker) had p > 0.5 on no frame at all and was never sent. Each frame is
# lifted by what brings the 90th percentile of the last 10 s's frame levels to VAD_TARGET_DB, 0 to VAD_GAIN_MAX_DB;
# only the VAD's features: the audio every other path takes (engines, voiceprints, playback) stays as it came.
VAD_TARGET_DB = -42.0
VAD_GAIN_MAX_DB = float(os.environ.get("VAD_GAIN_MAX_DB", "16"))  # 0: off, the VAD as before
LEVEL_FRAMES = 1000  # 10 s of frames
VAD, CMVN, EMB = "fireredvad-stream-vad.onnx", "fireredvad-cmvn.ark", "redimnet2-b6.onnx"
MODELS = {  # file: sha256 (sources and licenses: export_onnx.py)
    VAD: "b3c97836130dc34fc32d56fab551e88cf9454511de2b3c250a5e6578dee74b93",
    CMVN: "c87f6f13edf0f0ec7535ddfc9cc3387d9268cb234b70182d566c5e2edf3ca473",
    EMB: "0a17d6472e4e7a31ffe254858574f6f1203acd5a5fa9a153a12556f4aaa72d81",
}
FIRERED = "https://raw.githubusercontent.com/FireRedTeam/FireRedVAD/71352f2c93c110410189e0ed98ab85ea24e8ad1c/pretrained_models/onnx_models/"
SOURCES = {  # file: where fetch() downloads it from when it is missing
    VAD: FIRERED + "fireredvad_stream_vad_with_cache.onnx",
    CMVN: FIRERED + "cmvn.ark",
    EMB: "https://github.com/suyiiyii/alldayrec/releases/download/models-v1/redimnet2-b6.onnx",  # export_onnx.py's export
}
MAKE = ("cd server && uv run --group export python export_onnx.py {d}; "
        "in Docker: docker compose run --rm alldayrec uv run --group export python export_onnx.py")


def fetch(d: Path = DIR):
    """Download every model file missing in d, once: one that fails to come, or is not the pinned one, stops the
    start (a file already there is left to check())."""
    for name, url in SOURCES.items():
        if (d / name).is_file():
            continue
        logging.getLogger(__name__).info("models: downloading %s from %s", name, url)
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
            if (got := hashlib.sha256(data).hexdigest()) != MODELS[name]:
                raise SystemExit(f"models: {url} has sha256 {got}, not the pinned {MODELS[name]}")
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.part").write_bytes(data)
            (d / f"{name}.part").replace(d / name)
        except Exception as e:  # 401/404, no network, cut short, d not writable
            raise SystemExit(f"models: downloading {name} from {url} failed: {e} (make it: {MAKE.format(d=d)}; "
                             f"or put the file in {d})")


def check(d: Path = DIR):
    """SystemExit naming every model file that is missing in d or not the pinned one."""
    bad = [f"{d / n} {'is missing' if not (d / n).is_file() else 'has another sha256'}" for n, h in MODELS.items()
           if not (d / n).is_file() or hashlib.sha256((d / n).read_bytes()).hexdigest() != h]
    if bad:
        raise SystemExit(f"models: {'; '.join(bad)} (make them: {MAKE.format(d=d)})")


def session(name: str, threads: int) -> ort.InferenceSession:
    o = ort.SessionOptions()
    o.intra_op_num_threads, o.inter_op_num_threads = threads, 1
    # No arena: it grows to the biggest calls (voiceprints of 15 s, 2 at once) and never gives back, 2.65 GB after 600 of
    # them; without it 0.6 GB idle, ~10 % slower (16-core desktop, the main instance's lengths). kSameAsRequested kept 3.5 GB.
    o.enable_cpu_mem_arena = False
    return ort.InferenceSession(str(DIR / name), o, providers=["CPUExecutionProvider"])


def cmvn(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Means and inverse deviations from a Kaldi binary stats matrix (2 x dim+1, doubles), as FireRedVAD computes them."""
    b = path.read_bytes()
    if b[:6] != b"\0BDM \4" or b[10:11] != b"\4":
        raise ValueError(f"{path}: not a Kaldi binary double matrix")
    rows, cols = struct.unpack_from("<i", b, 6)[0], struct.unpack_from("<i", b, 11)[0]
    s = np.frombuffer(b, "<f8", rows * cols, 15).reshape(rows, cols)
    mean = s[0, :-1] / s[0, -1]
    return mean, 1.0 / np.sqrt(np.maximum(s[1, :-1] / s[0, -1] - mean * mean, 1e-20))


@cache
def _vad() -> tuple[ort.InferenceSession, np.ndarray, np.ndarray]:
    return session(VAD, 1), *cmvn(DIR / CMVN)  # one session for every stream: run() is thread-safe, a stream's state is its caches


def fbank(x: np.ndarray, gain_db: np.ndarray | float = 0.0) -> np.ndarray:
    """80 log-mel frames (25 ms / 10 ms, snip edges, no dither) of int16 samples, CMVN applied: FireRedVAD's features.
    gain_db (per frame): as if x were that much louder there (a log-mel is the log of a power, without clipping)."""
    o = knf.FbankOptions()
    o.frame_opts.samp_freq, o.frame_opts.dither, o.frame_opts.snip_edges, o.mel_opts.num_bins = 16000, 0, True, 80
    f = knf.OnlineFbank(o)
    f.accept_waveform(16000, x.tolist())
    _, mean, istd = _vad()
    feat = np.vstack([f.get_frame(i) for i in range(f.num_frames_ready)]) + np.reshape(gain_db, (-1, 1)) * (np.log(10) / 10)
    return ((feat - mean) * istd).astype(np.float32)


def levels(x: np.ndarray) -> np.ndarray:
    """dBFS of each fbank frame of int16 samples (mean square over its 25 ms)."""
    fr = np.lib.stride_tricks.sliding_window_view(x, 400)[::160].astype(np.float64)
    return 10 * np.log10((fr * fr).mean(1) / 32768 ** 2 + 1e-12)


class StreamVad:
    """FireRedStreamVad as the Segmenter uses it (detect_chunk, postprocessor) on onnxruntime: the 8 FSMN layers' caches
    carry the stream from one chunk to the next (zeros at the start, the same as none)."""

    def __init__(self):
        self.postprocessor = StreamVadPostprocessor(5, 0.5, 5, 8, 2000, 20)  # FireRedStreamVadConfig()'s defaults
        self.caches = np.zeros((8, 1, 128, 19), np.float32)
        self.levels = np.full(LEVEL_FRAMES - 1, VAD_TARGET_DB)  # its last frames' levels; at the start as if at the target: no lift

    def gain(self, x: np.ndarray) -> np.ndarray:
        """dB for each frame of x (whole frames, a chunk of the stream): up to the target, from the levels of the
        LEVEL_FRAMES up to and including it. The 90th percentile is the speech when there is some, 10 % of the frames."""
        lv = np.concatenate([self.levels, levels(x)])
        self.levels = lv[len(lv) - LEVEL_FRAMES + 1:]
        p90 = np.partition(np.lib.stride_tricks.sliding_window_view(lv, LEVEL_FRAMES), LEVEL_FRAMES * 9 // 10, axis=1)[:, LEVEL_FRAMES * 9 // 10]
        return np.clip(VAD_TARGET_DB - p90, 0, VAD_GAIN_MAX_DB)

    def detect_chunk(self, x: np.ndarray) -> list:
        probs, self.caches = _vad()[0].run(None, {"feat": fbank(x, self.gain(x))[None], "caches_in": self.caches})
        return [self.postprocessor.process_one_frame(p) for p in probs.reshape(-1).tolist()]


def embedder():
    """ReDimNet2 B6: int16 audio -> unit voiceprint (192)."""
    s = session(EMB, THREADS)

    def embed(x: np.ndarray) -> np.ndarray:
        e = s.run(None, {"wav": (x.astype(np.float32) / 32768)[None]})[0][0]
        return e / np.linalg.norm(e)
    return embed
