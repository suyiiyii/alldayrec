---
type: how-to
---

# 如何排查常见问题

先看日志：Docker 里运行 `docker compose logs -f`。网页的 **处理进度** 页（`/progress`）也会显示转写、摘要做到哪了和系统日志。

## 服务器启动就退出

- 日志写 `not set: ...`：`.env` 里缺这些变量。见[配置项](../reference/configuration.md)。
- 日志写 `ENGINE, DRAFT_ENGINE, ENGINE_FALLBACK must be one of ...`：这三项里有一个值写错了，日志里列出了可选的值。如果你写的值在[如何更换语音识别引擎](./change-asr-engine.md)里有、日志里却没有，说明本机的镜像比文档旧，先运行 `docker compose pull`。
- 日志写 `ffmpeg is not on PATH`：装上 ffmpeg（macOS：`brew install ffmpeg`；Debian、Ubuntu：`sudo apt install ffmpeg`）。Docker 镜像里已经有。
- 日志写 `ENGINE=local needs sherpa-onnx`：装带 `local` 的版本：`pip install 'alldayrec[local]'`，uvx 加 `--from 'alldayrec[local]'`，从源码运行时 `uv sync --extra local`。Docker 镜像里已经有。
- 日志写 `others can read it, and it holds every token: chmod 600`：对 `DATA_DIR` 里的 `tenants.json` 运行 `chmod 600`（Docker 里是 `chmod 600 data/tenants.json`）。
- 日志里说某个模型文件下载失败、`is missing` 或 `has another sha256`：删掉那个文件再重启，或者从日志里给出的同一个地址下载这个文件，放进 `MODELS_DIR`（Docker 里是 `data/models`）。自己用 `export_onnx.py` 导出的 ReDimNet2 文件和发布的文件逐字节不同，会被拒绝。

## 打开网页只看到 `token required`

这个浏览器还没登录。用 `http(s)://<你的服务器>/?token=<你的 token>` 打开一次，之后靠 cookie 保持登录。

## 摘要失败，或者「问 AI」报「模型调用失败」

- 百炼的 key 在控制台里限定了可用模型时，调对话模型会返回 403。换一把没有限定模型的 key。
- 用别的大模型服务时，它必须支持工具调用和 JSON 输出（`response_format` 为 `json_object`）。

## 有的对话没有摘要

说话不到 60 秒的对话不调大模型：标题取第一句，没有摘要和待办（`server/alldayrec/summary.py` 里的 `MIN_SPEECH_S`）。

## 用 `ENGINE=local` 时第一条转写很慢

第一段音频到达时，服务器要先下载约 136 MB 的模型包，第一条转写要等一分钟左右。之后就不用再下载。

## 导入的录音最后一段一直没有转写

传完一批文件后要调用一次 `POST /api/chunks/end`，见[如何导入已有录音](./import-recordings.md)。调用之前，这一批的最后一段对话也可能还显示「进行中」。

## 手机扫码后连不上

- 二维码里的服务器地址是 **连接手机** 页「手机用哪个地址」里选中的那个。选的是 `127.0.0.1` 或者手机访问不到的地址时，换一个手机能访问的地址，或者在「自己填」里填，再扫。
- 地址是 `http://` 时，1.5.0 及更早的 APK 连不上，升级到 1.5.1 以上。见[如何让手机连上服务器](./remote-access.md)。

## iPhone 上的 App 打不开了

用免费 Apple ID 装的 App 7 天后会失效，用 Xcode 重新安装一次。付费开发者账号没有这个限制。

## 用 nginx 反向代理时导入文件返回 413

nginx 默认只收 1 MB 以内的请求体，调大 `client_max_body_size`。

## 旧版 Safari 能播放录音吗

能。网页播放和收听的音频由服务器解码成 WAV 再给浏览器（`/api/audio`、`/api/listen`），因为 Safari 18.4 以前解码不了录音的 Ogg Opus 格式。

## macOS 上存储卡的 OMI 盘一直不出现

App 的 **录音设备 › U 盘模式 › 存储卡当 U 盘** 会把录音设备的存储卡当成 U 盘给电脑，正常约 20 秒后出现 OMI 盘。

维护者在 macOS 26 上测到：系统挂载 FAT32 卷有约 20 秒的时限，读得慢的大容量 FAT32 卡（比如 32 GB）可能一直挂不上；同样的卡格式化成 exFAT 后约 2 秒挂上。固件认 FAT32 和 exFAT 两种格式。另外，Mac 锁屏时系统不挂载任何磁盘。
