"""The local ASR engine for engine_segs: sherpa-onnx X-ASR zh-en (zipformer transducer with punctuation, int8,
2026-06-03, Apache-2.0) in this process, no network and no key. Text with per-token times, no speakers: the diarist
labels the sentences, as for stepfun-sse. The 10-23 bench on 5 far-field recordings: in-sentence CER 19.7 (Bailian
qwen-audio-3.1 13.5, StepFun SSE 18.2), RTF 0.024 at 4 threads on a 16-core desktop (0.045 at 1), 330 MB after load; its
rows span two speakers 11 % of the time cut at 。？！ alone, 4 % cut also at ， and pauses, so sentences() cuts at both.
A window is decoded in pieces of at most PIECE_S cut where it is quietest (what the bench measured). One recognizer
for the process, built at the first call with INFER_THREADS threads; its files live in MODELS_DIR/NAME, taken then
out of the release tarball (LOCAL_ASR_URL: GitHub's, or a mirror's copy of the same tarball; sha256-checked) when
missing or not the pinned ones (FILES).

    uv run --extra local python -m alldayrec.local_asr file.wav     decode a 16 kHz mono wav and print its sentences (fetches the model)
"""
import logging
import os
import re
import shutil
import tarfile
import threading
from pathlib import Path

import anyio.to_thread
import numpy as np

from alldayrec import infer
from alldayrec import threads

SR = 16000
TAR = "sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03"  # the release tarball and its top directory
NAME = TAR.removeprefix("sherpa-onnx-")  # MODELS_DIR/NAME
URL = os.environ.get("LOCAL_ASR_URL") or f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{TAR}.tar.bz2"
# GitHub's digest of the tarball; ModelScope adaada88/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8 has the same
TAR_SHA = "5d02c36d7b44e886b7c8f0d8e051f8713acab96c264bb6ef9e718be39a6a2224"
FILES = {  # file in the tarball: sha256
    "encoder-epoch-99-avg-1.int8.onnx": "7f6aa62056efd8af9da13e0faa81cd3f284d2fb2e3b63de56fd2dfd3450910dc",
    "decoder-epoch-99-avg-1.onnx": "72f47405d3c1033bebccbef82f90071e7b4ba3e71b9c986f2b74244b25723aed",
    "joiner-epoch-99-avg-1.int8.onnx": "aedb7fa697b2ab43f20499826fff7c997eea7d67db77be97769aeeeb726e63b3",
    "tokens.txt": "b818a60878b9aae978cbb8ad594acbd403d76d1af2e31ef4197c84e2dbdba27c",
    "bpe.model": "f87a38025a5fdd1e4e9591f6a44bb81295097ce0b80df6f4ab9f44e52c64ca5f",
}
NEEDS = ("ENGINE=local needs sherpa-onnx, the [local] extra: pip install 'alldayrec[local]', "
         "uvx --from 'alldayrec[local]' alldayrec, or in a checkout uv sync --extra local")
PIECE_S = 20.0   # most audio in one decode (the bench's); longer is cut at the quietest 100 ms past half of it
TOKEN_MS = 300   # a token ends where the next starts, at most this much later (a pause after it is not it)
PAUSE_MS = 300   # a sentence ends before a token starting this long after the last one ended ...
END = "。？！，?!,"  # ... and after a token ending in one of these
PUNCT = re.compile(rf"[{END}、…]+")  # a token of punctuation alone joins the sentence before it, whatever the pause
log = logging.getLogger("live")

_lock = threading.Lock()
_rec = None


def fetch(d: Path):
    """FILES into d when any is missing there or not the pinned one: the tarball (infer.download: the pinned one or a
    RuntimeError) to d, the files out of it (one cut short is not the pinned one: taken out again next time)."""
    need = [n for n, h in FILES.items() if not (d / n).is_file() or infer.sha(d / n) != h]
    if not need:
        return
    tar = d / f"{TAR}.tar.bz2"
    log.info("local asr: fetching %s for %s", URL, ", ".join(need))
    try:
        infer.download(URL, tar, TAR_SHA)
        with tarfile.open(tar, "r:bz2") as t:
            for n in need:
                with t.extractfile(f"{TAR}/{n}") as f, open(d / n, "wb") as o:
                    shutil.copyfileobj(f, o)
    finally:
        tar.unlink(missing_ok=True)


def recognizer():
    """The process's one recognizer, built (and its files fetched) at the first call; decode_stream is thread-safe."""
    global _rec
    with _lock:
        if _rec is None:
            try:
                import sherpa_onnx
            except ImportError as e:
                raise ImportError(NEEDS) from e
            d = infer.DIR / NAME
            fetch(d)
            f = lambda n: str(d / n)
            _rec = sherpa_onnx.OfflineRecognizer.from_transducer(
                f("encoder-epoch-99-avg-1.int8.onnx"), f("decoder-epoch-99-avg-1.onnx"), f("joiner-epoch-99-avg-1.int8.onnx"),
                f("tokens.txt"), modeling_unit="cjkchar+bpe", bpe_vocab=f("bpe.model"), num_threads=infer.THREADS)
        return _rec


def pieces(x: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) sample spans of x at most PIECE_S long: a longer stretch is cut in the quietest 100 ms past half
    of PIECE_S (bench.py's rule; the audio comes squeezed, its silences at most SQUEEZE_KEEP_S)."""
    out, s, hop, n = [], 0, SR // 10, int(PIECE_S * SR)
    while len(x) - s > n:
        lo = s + n // 2
        en = (x[lo:s + n].astype(np.float32) ** 2).reshape(-1, hop).sum(1)
        c = lo + int(np.argmin(en)) * hop + hop // 2
        out.append((s, c))
        s = c
    return out + [(s, len(x))]


def tokens(rec, x: np.ndarray) -> list[tuple[str, int, int]]:
    """x decoded piece by piece: its tokens (text, start_ms, end_ms) in x, a token ending where the next starts (at
    most TOKEN_MS later), the last at the piece's end. A CJK token comes as ' 字', a word's first BPE piece as ' Git'."""
    out, xf = [], x.astype(np.float32) / 32768
    for a, b in pieces(x):
        st = rec.create_stream()
        st.accept_waveform(SR, xf[a:b])
        rec.decode_stream(st)
        toks, ts = list(st.result.tokens), list(st.result.timestamps)
        if len(ts) != len(toks):  # no times (not seen with X-ASR): the tokens spread over the piece
            ts = np.linspace(0, (b - a) / SR, len(toks), endpoint=False).tolist()
        for tok, t0, t1 in zip(toks, ts, ts[1:] + [(b - a) / SR]):
            out.append((tok, round(a / 16 + t0 * 1000), round(a / 16 + min(t1, t0 + TOKEN_MS / 1000) * 1000)))
    return out


def text(toks: list[str]) -> str:
    """The tokens as one string: CJK characters joined, words kept apart (' 今', ' 天', ' G', 'it' -> '今天 Git')."""
    return re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", "".join(toks)).strip()


def sentences(toks: list[tuple[str, int, int]]) -> list[dict]:
    """Tokens -> [{start_ms, end_ms, text}]: a sentence ends after a token ending in END, or before one starting over
    PAUSE_MS after the last ended; punctuation alone joins the sentence before it."""
    segs, cur = [], None
    for tok, a, b in toks:
        if PUNCT.fullmatch(tok.strip()):
            if cur:
                cur["toks"].append(tok)
                if tok.rstrip()[-1] in END:
                    segs.append(cur)
                    cur = None
            continue
        if cur and a - cur["end_ms"] > PAUSE_MS:
            segs.append(cur)
            cur = None
        if cur is None:
            cur = {"toks": [], "start_ms": a, "end_ms": b}
        cur["toks"].append(tok)
        cur["end_ms"] = b
        if tok.rstrip()[-1:] in tuple(END):
            segs.append(cur)
            cur = None
    if cur:
        segs.append(cur)
    return [{"start_ms": g["start_ms"], "end_ms": g["end_ms"], "text": t} for g in segs if (t := text(g["toks"]))]


def _transcribe(x: np.ndarray) -> list[dict]:
    return sentences(tokens(recognizer(), x))


async def transcribe(x: np.ndarray, partial=None) -> list[dict]:
    """x: 16 kHz int16 PCM -> [{start_ms, end_ms, text}] (no speaker), in a thread under threads.ASR; no partial answers."""
    return await anyio.to_thread.run_sync(_transcribe, x, limiter=threads.ASR)


if __name__ == "__main__":
    import sys
    import time
    import wave

    with wave.open(sys.argv[1]) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SR, 1, 2), "16 kHz mono 16-bit wav"
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    t = time.perf_counter()
    rec = recognizer()
    print(f"load {time.perf_counter() - t:.1f} s")
    t = time.perf_counter()
    for g in _transcribe(x):
        print(f"{g['start_ms'] / 1000:7.2f} {g['end_ms'] / 1000:7.2f}  {g['text']}")
    print(f"decode {time.perf_counter() - t:.2f} s for {len(x) / SR:.1f} s")
