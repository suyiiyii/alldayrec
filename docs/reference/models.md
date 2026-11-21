---
type: reference
---

# 模型文件

服务器在本机 CPU 上跑四个模型（`SOUND_MODEL` 里有 `mn10_as`、`QUALITY=1` 时各多一个）（onnxruntime，不需要显卡），文件放在 `MODELS_DIR`（默认是 `DATA_DIR/models`，Docker 里是 `data/models`）。

| 文件 | 模型 | 用途 | 许可 | 下载自 |
|---|---|---|---|---|
| `fireredvad-stream-vad.onnx`、`fireredvad-cmvn.ark` | [FireRedVAD](https://github.com/FireRedTeam/FireRedVAD) | 语音检测 | Apache-2.0 | FireRedVAD 上游仓库 |
| `redimnet2-b6.onnx` | [ReDimNet2](https://github.com/PalabraAI/redimnet2) B6 | 声纹 | MIT（训练数据见下） | 本仓库的 [`models-v1`](https://github.com/suyiiyii/alldayrec/releases/tag/models-v1) Release |
| `pyannote-segmentation-3.0.onnx` | [pyannote segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)（sherpa-onnx 的 ONNX 导出） | 找出一句话里换人的地方（`SPLIT_TURNS`） | MIT | 本仓库的 `models-v1` Release |
| `yamnet.onnx` | [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet) | 听出音乐：外放的歌不取声纹（`MUSIC_DETECT`）；环境声音（`SOUND_MODEL` 里有 `yamnet` 时） | Apache-2.0 | 本仓库的 `models-v1` Release |
| `mn10_as.onnx` | [EfficientAT](https://github.com/fschmid56/EfficientAT) mn10_as | 环境声音（[`SOUND_MODEL`](./configuration.md#可选-环境声音) 里有 `mn10_as` 时才下载） | MIT | 本仓库的 `models-v1` Release |
| `dnsmos-p808.onnx` | [DNSMOS](https://github.com/microsoft/DNS-Challenge/tree/master/DNSMOS) P.808（`DNSMOS/model_v8.onnx` 原样，上游提交 `785ab12`） | 音质日报（[`QUALITY=1`](./configuration.md#可选-音质日报) 时才下载） | CC BY 4.0（Microsoft） | 本仓库的 `models-v1` Release |

用 `ENGINE=local` 时还有一个：sherpa-onnx 的 X-ASR 中英模型（`x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03/`），第一次用到时从 k2-fsa/sherpa-onnx 的 GitHub Release 下载约 136 MB 的模型包（或 `LOCAL_ASR_URL`），解开放进同一个目录。

## 下载和校验

- 服务器启动时下载缺的文件，第一次启动约 76 MB（加上 `mn10_as.onnx` 约 96 MB，`dnsmos-p808.onnx` 0.2 MB）。
- 每个文件的 sha256 钉在 [`server/alldayrec/infer.py`](https://github.com/suyiiyii/alldayrec/blob/main/server/alldayrec/infer.py) 和 [`server/alldayrec/local_asr.py`](https://github.com/suyiiyii/alldayrec/blob/main/server/alldayrec/local_asr.py) 里，只接受一致的文件；不一致或缺文件时服务器拒绝启动。
- 已经有的文件不会重复下载。
- `server/alldayrec/export_onnx.py` 是维护者从上游权重制作新的 Release 文件用的。ReDimNet2 的导出在不同机器上逐字节不一致，自己导出的文件会被拒绝。`mn10_as.onnx` 是从 EfficientAT 的 `mn10_as_mAP_471.pt` 导出的，图里包含 16→32 kHz 重采样、梅尔谱和 sigmoid：输入 16 kHz 的波形（任意长度），输出 527 类的分数。

## 声纹模型的训练数据

ReDimNet2 的代码和权重是 MIT，但训练数据包含 VoxBlink2（CC BY-NC-SA 4.0）。详见 [THIRD_PARTY_NOTICES.md](https://github.com/suyiiyii/alldayrec/blob/main/THIRD_PARTY_NOTICES.md)，是否适合你的用途请自己判断。

大模型不在这里：项目不打包、不运行大模型，用的是 `LLM_BASE_URL` 指向的外部服务。
