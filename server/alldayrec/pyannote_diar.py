"""Diarize a case's audio with pyannote's community-1 pipeline -> <case>/work/pyannote-community1.json
(same [{start_ms, end_ms, speaker, text: ""}] shape as the sherpa-onnx diarizer, for replay_case DIAR_SEGS).

    HF_TOKEN=... .venv-pyannote/bin/python pyannote_diar.py <case-dir> [--device mps|cpu] [--window 240 --step 30]

Whole file by default; with --window it also writes work/pyannote-community1-<window>/<a>-<e>.json per
window, the layout rows_from_windows reads from DIAR_SEGS_DIR.
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import torch

SR = 16000


def segs_of(ann) -> list[dict]:
    out = []
    for turn, _, spk in ann.itertracks(yield_label=True):
        out.append(dict(start_ms=int(turn.start * 1000), end_ms=int(turn.end * 1000), speaker=str(spk), text=""))
    return sorted(out, key=lambda g: g["start_ms"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=Path)
    ap.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    ap.add_argument("--window", type=float, default=0)
    ap.add_argument("--step", type=float, default=30)
    a = ap.parse_args()
    from pyannote.audio import Pipeline
    pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=os.environ["HF_TOKEN"])
    pipe.to(torch.device(a.device))
    meta = json.load(open(a.case / "meta.json"))
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(a.case / meta["audio"]), "-f", "s16le", "-ac", "1",
                                     "-ar", str(SR), "-"], capture_output=True, check=True).stdout, np.int16)
    wav = torch.from_numpy(x.astype(np.float32) / 32768).unsqueeze(0)
    work = a.case / "work"
    work.mkdir(exist_ok=True)
    if not a.window:
        t = time.time()
        out = pipe({"waveform": wav, "sample_rate": SR})
        ann = getattr(out, "speaker_diarization", out)
        segs = segs_of(ann)
        json.dump(segs, open(work / "pyannote-community1.json", "w"))
        print(f"whole file: {len(x) / SR:.0f}s audio in {time.time() - t:.0f}s on {a.device}, "
              f"{len(segs)} turns, {len({g['speaker'] for g in segs})} speakers", flush=True)
        return
    d = work / f"pyannote-community1-{a.window:.0f}"
    d.mkdir(exist_ok=True)
    total = len(x) / SR
    ends = list(np.arange(a.window, total, a.step)) + [total]
    t = time.time()
    for n, e in enumerate(ends):
        s = max(0.0, e - a.window)
        p = d / f"{s:.1f}-{e:.1f}.json"
        if p.exists():
            continue
        out = pipe({"waveform": wav[:, int(s * SR):int(e * SR)], "sample_rate": SR})
        json.dump(segs_of(getattr(out, "speaker_diarization", out)), open(p, "w"))
        if n % 10 == 0:
            print(f"  window {n}/{len(ends)} {time.time() - t:.0f}s", flush=True)
    print(f"windows done in {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
