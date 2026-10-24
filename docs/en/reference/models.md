---
type: reference
---

# Model files

The server runs two models on the local CPU (onnxruntime, no GPU needed). Their files live in `MODELS_DIR` (`data/models` in Docker).

| File | Model | Use | License | Downloaded from |
|---|---|---|---|---|
| `fireredvad-stream-vad.onnx`, `fireredvad-cmvn.ark` | [FireRedVAD](https://github.com/FireRedTeam/FireRedVAD) | Speech detection | Apache-2.0 | FireRedVAD's upstream repository |
| `redimnet2-b6.onnx` | [ReDimNet2](https://github.com/PalabraAI/redimnet2) B6 | Voiceprints | MIT (training data below) | This repository's [`models-v1`](https://github.com/suyiiyii/alldayrec/releases/tag/models-v1) release |

With `ENGINE=local` there is a third one: sherpa-onnx's X-ASR zh-en model (`x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03/`). At its first use, the server downloads the model tarball, about 136 MB, from the k2-fsa/sherpa-onnx GitHub Release (or `LOCAL_ASR_URL`) and unpacks it into the same directory.

## Download and verification

- At startup the server downloads the missing files, about 55 MB at the first start.
- The sha256 of each file is pinned in [`server/infer.py`](https://github.com/suyiiyii/alldayrec/blob/main/server/infer.py) and [`server/local_asr.py`](https://github.com/suyiiyii/alldayrec/blob/main/server/local_asr.py), and the server accepts only matching files. When a file doesn't match or is missing, the server refuses to start.
- A file that is already there is never downloaded again.
- `server/export_onnx.py` is how maintainers make new release files from the upstream weights. ReDimNet2's export differs byte for byte between machines, so the server refuses a file you export yourself.

## The voiceprint model's training data

ReDimNet2's code and weights are MIT, but its training data includes VoxBlink2 (CC BY-NC-SA 4.0). See [THIRD_PARTY_NOTICES.md](https://github.com/suyiiyii/alldayrec/blob/main/THIRD_PARTY_NOTICES.md) and decide for yourself whether that fits your use.

The LLM isn't here: the project doesn't bundle or run an LLM, and uses the external service that `LLM_BASE_URL` points at.
