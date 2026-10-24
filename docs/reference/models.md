---
type: reference
---

# 模型文件

服务器在本机 CPU 上跑三个模型（onnxruntime，不需要显卡），文件放在 `MODELS_DIR`（默认是 `DATA_DIR/models`，Docker 里是 `data/models`）。

| 文件 | 模型 | 用途 | 许可 | 下载自 |
|---|---|---|---|---|
| `fireredvad-stream-vad.onnx`、`fireredvad-cmvn.ark` | [FireRedVAD](https://github.com/FireRedTeam/FireRedVAD) | 语音检测 | Apache-2.0 | FireRedVAD 上游仓库 |
| `redimnet2-b6.onnx` | [ReDimNet2](https://github.com/PalabraAI/redimnet2) B6 | 声纹 | MIT（训练数据见下） | 本仓库的 [`models-v1`](https://github.com/suyiiyii/alldayrec/releases/tag/models-v1) Release |
| `yamnet.onnx` | [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet) | 听出音乐：外放的歌不取声纹（`MUSIC_DETECT`） | Apache-2.0 | 本仓库的 `models-v1` Release |

用 `ENGINE=local` 时还有第四个：sherpa-onnx 的 X-ASR 中英模型（`x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03/`），第一次用到时从 k2-fsa/sherpa-onnx 的 GitHub Release 下载约 136 MB 的模型包（或 `LOCAL_ASR_URL`），解开放进同一个目录。

## 下载和校验

- 服务器启动时下载缺的文件，第一次启动约 70 MB。
- 每个文件的 sha256 钉在 [`server/alldayrec/infer.py`](https://github.com/suyiiyii/alldayrec/blob/main/server/alldayrec/infer.py) 和 [`server/alldayrec/local_asr.py`](https://github.com/suyiiyii/alldayrec/blob/main/server/alldayrec/local_asr.py) 里，只接受一致的文件；不一致或缺文件时服务器拒绝启动。
- 已经有的文件不会重复下载。
- `server/alldayrec/export_onnx.py` 是维护者从上游权重制作新的 Release 文件用的。ReDimNet2 的导出在不同机器上逐字节不一致，自己导出的文件会被拒绝。

## 声纹模型的训练数据

ReDimNet2 的代码和权重是 MIT，但训练数据包含 VoxBlink2（CC BY-NC-SA 4.0）。详见 [THIRD_PARTY_NOTICES.md](https://github.com/suyiiyii/alldayrec/blob/main/THIRD_PARTY_NOTICES.md)，是否适合你的用途请自己判断。

大模型不在这里：项目不打包、不运行大模型，用的是 `LLM_BASE_URL` 指向的外部服务。
