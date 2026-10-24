"""Make the model files infer.py runs, into DIR (default MODELS_DIR, else /opt/alldayrec/models), and check each against the
sha256 infer.MODELS pins. Needs torch, only here: the export dependency group.

    uv run --group export python -m alldayrec.export_onnx [DIR]

- fireredvad-stream-vad.onnx, fireredvad-cmvn.ark: FireRedVAD's own ONNX of Stream-VAD (the FSMN caches as an input and
  an output) and its CMVN stats, from github.com/FireRedTeam/FireRedVAD at 71352f2 (pretrained_models/onnx_models;
  the same weights and CMVN as the Stream-VAD checkpoint on HuggingFace that ran before), Apache-2.0. Downloaded
  (infer.SOURCES; the server also fetches them itself at start).
- redimnet2-b6.onnx: ReDimNet2 B6, VoxBlink2 + VoxCeleb2 + CN-Celeb, large-margin fine-tuned
  (b6-vb2+vox2+cnc2_v0-lm.pt of the v1.0.0 release of github.com/PalabraAI/redimnet2, code at c5bbe0b), MIT.
  Exported here (the TorchScript exporter, opset 17, any length; the front end is already a conv1d STFT), then compared
  with the torch model on noise of several lengths. The models-v1 release file the server fetches when it is missing
  (infer.SOURCES) is this export, made with torch 2.5.1; its training data VoxBlink2 is CC BY-NC-SA 4.0
  (THIRD_PARTY_NOTICES).
"""
import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

from alldayrec import infer

REDIMNET = "PalabraAI/redimnet2:c5bbe0b76e37df698c403f8844e41304ceab6307"
WEIGHTS = ("b6-vb2+vox2+cnc2_v0-lm.pt", "287365f6f485b19e65e5176554f8f7123bfa8d85185f3d2c040eab51acec9868")  # the release's digest


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def redimnet(out: Path):
    import torch
    torch.hub.set_dir(tempfile.mkdtemp())  # this commit's code and the release weights, nothing cached from before
    m = torch.hub.load(REDIMNET, "redimnet2", model_name="b6", train_type="lm", dataset="vb2+vox2+cnc2_v0",
                       pretrained=True, trust_repo=True, skip_validation=True).eval()
    w = Path(torch.hub.get_dir()) / "checkpoints" / WEIGHTS[0]
    if sha256(w) != WEIGHTS[1]:
        raise SystemExit(f"{WEIGHTS[0]}: sha256 {sha256(w)}, the release says {WEIGHTS[1]}")
    torch.onnx.export(m, (torch.zeros(1, 4 * 16000),), str(out), input_names=["wav"], output_names=["emb"],
                      dynamic_axes={"wav": {0: "batch", 1: "samples"}, "emb": {0: "batch"}}, opset_version=17,
                      dynamo=False)  # the release file's exporter; the 2.9+ default needs onnxscript
    import onnxruntime as ort
    s = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    for n in (0.3, 1, 4, 15):
        x = np.random.default_rng(0).standard_normal((1, int(n * 16000))).astype(np.float32) * 0.1
        with torch.no_grad():
            a = m(torch.from_numpy(x)).numpy()[0]
        b = s.run(None, {"wav": x})[0][0]
        c = float(a @ b / np.linalg.norm(a) / np.linalg.norm(b))
        print(f"  {n} s: cosine torch / onnxruntime {c:.7f}")
        if c < 0.99999:
            raise SystemExit(f"redimnet2 export differs from torch at {n} s: cosine {c}")


def main():
    d = Path(sys.argv[1]) if sys.argv[1:] else infer.DIR
    d.mkdir(parents=True, exist_ok=True)
    for name in (infer.VAD, infer.CMVN):
        urllib.request.urlretrieve(infer.SOURCES[name], d / name)
    redimnet(d / infer.EMB)
    for name, want in infer.MODELS.items():
        got = sha256(d / name)
        print(f"{name}: {got} {'ok' if got == want else f'DIFFERS from the pinned {want}'}")
    infer.check(d)


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    main()
