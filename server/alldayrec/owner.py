"""Build the owner voiceprint per input device from 闪电说 dictation windows (certainly me).

    uv run --extra local python -m alldayrec.owner [--per-device 400] [--exclude 2026-09-09,2026-09-21]

Writes data/owner.npz {device: unit vector}; server.py scores StepFun speaker clusters against it.
"""
import argparse
import collections
import json
import os
import random
import subprocess
from pathlib import Path

import numpy as np
import sherpa_onnx

HERE = Path(__file__).resolve().parent
LABELS = Path(os.environ["OWNER_LABELS"])  # jsonl of dictation windows known to be the owner: {source, start_iso, end_iso, device, ...}
EMB_MODEL = Path(os.environ["EMB_MODEL"])
SR = 16000
SILENT_DB = -60.0  # a dictation while the DJI transmitter was off records digital silence


def extractor():
    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL), num_threads=4))


def embed(ex, x: np.ndarray) -> np.ndarray:
    s = ex.create_stream()
    s.accept_waveform(SR, x.astype(np.float32) / 32768)
    s.input_finished()
    e = np.array(ex.compute(s))
    return e / np.linalg.norm(e)


def decode(path: str) -> np.ndarray:
    return np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                                        capture_output=True, check=True).stdout, np.int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-device", type=int, default=400)
    ap.add_argument("--exclude", default="")
    args = ap.parse_args()
    skip = set(filter(None, args.exclude.split(",")))
    rows = [r for r in map(json.loads, open(LABELS)) if r["source"] == "shandianshuo" and r["start_iso"][:10] not in skip
            and Path(r["file"]).exists()]
    random.seed(0)
    by_dev = collections.defaultdict(list)
    for r in rows:
        by_dev[r["device"]].append(r)
    ex, out = extractor(), {}
    for dev, rs in by_dev.items():
        pick = sorted(random.sample(rs, min(args.per_device, len(rs))), key=lambda r: r["file"])
        vecs, cur, x = [], None, None
        for r in pick:
            if r["file"] != cur:
                cur, x = r["file"], decode(r["file"])
            seg = x[int(r["a"] * SR):int(min(r["b"], r["a"] + 15) * SR)]
            if len(seg) < SR or 20 * np.log10(np.sqrt(np.mean((seg / 32768.0) ** 2)) + 1e-12) < SILENT_DB:
                continue
            vecs.append(embed(ex, seg))
        if len(vecs) >= 20:
            v = np.mean(vecs, 0)
            out[dev] = v / np.linalg.norm(v)
        print(f"{dev}: {len(vecs)} windows")
    (HERE / "data").mkdir(exist_ok=True)
    out_dir = Path(os.environ.get("LIVE_DATA", HERE / "data"))  # same dir server.py reads
    out_dir.mkdir(exist_ok=True)
    np.savez(out_dir / "owner.npz", **out)


if __name__ == "__main__":
    main()
