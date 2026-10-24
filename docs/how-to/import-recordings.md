---
type: how-to
---

# 如何导入已有录音

App 和网页里还没有导入按钮，导入走 HTTP 接口 `POST /api/chunks`：一个文件一个请求，请求体是一个完整的音频文件，服务器用 ffmpeg 解码。

## 能导入什么

| 你手上的 | 能导入吗 | 怎么做 |
|---|---|---|
| 录音笔、手机录音（MP3、WAV） | 能 | 直接上传 |
| `.m4a`（比如 iPhone 语音备忘录） | 能，先转码 | 长的 `.m4a` 从管道解码不了，先运行 `ffmpeg -i in.m4a out.mp3` |
| Plaud | 能 | 在 Plaud App 里导出 MP3 或 WAV，再上传 |
| 飞书录音豆 | 麻烦 | 音频只能在飞书妙记网页端逐条下载，下载后再上传 |
| OPPO 心力球 | 不能 | 它不保留原音，没有音频可导 |

第三方产品的情况见[简介与定位](../explanation/intro.md#和市面产品比)里的出处。

## 规则

- **用一个单独的设备名**，比如 `import`。每一批用一个新名字（比如 `import-2026-10`）最省心。
- **按时间从早到晚传。** 一个设备名下，早于服务器已经处理到的位置的音频只保存、不转写。
- **传完最后一个文件后，告诉服务器这一批结束了。** 不然最后一段会一直等后面的音频，不会定稿。

## 步骤

1. 把 token 放进环境变量：

   ```bash
   TOKEN=<你的 token>
   ```

2. 上传一个文件。`start` 是这段录音开始的时刻（Unix 毫秒），对话按它落到哪一天、几点。

   Linux（GNU date）：

   ```bash
   curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/mpeg" --data-binary @rec.mp3 \
     "https://<你的服务器>/api/chunks?device=import&start=$(date -d '2026-10-20 09:30:00' +%s)000"
   ```

   macOS（BSD date）：

   ```bash
   curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/mpeg" --data-binary @rec.mp3 \
     "https://<你的服务器>/api/chunks?device=import&start=$(date -j -f '%Y-%m-%d %H:%M:%S' '2026-10-20 09:30:00' +%s)000"
   ```

   成功时返回这个文件的秒数，比如 `{"seconds":14.19}`。返回 400 表示服务器解码不了这个文件。

3. 按时间顺序传完这一批的其他文件。

4. 结束这一批。`t` 是最后一个文件结束的时刻（Unix 毫秒），也就是它的 `start` 加上它的时长：

   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" "https://<你的服务器>/api/chunks/end?device=import&t=<结束时刻>"
   ```

   返回 `{"ok":true}`。服务器处理完剩下的音频后，转写会出现在那一天的页面上。

## Content-Type

App 发的 Ogg Opus 用 `audio/ogg`，也收 `audio/webm` 和 `audio/aac`。其他类型（比如 MP3 的 `audio/mpeg`、WAV 的 `audio/wav`）原样保存，由 ffmpeg 自己识别格式。

## 参考客户端

[`firmware/devkit/lr_sync.py`](https://github.com/suyiiyii/alldayrec/blob/main/firmware/devkit/lr_sync.py) 是一个从录音设备读卡再上传的 Python 客户端。接口参数见 [HTTP API](../reference/api.md#api-chunks)。
