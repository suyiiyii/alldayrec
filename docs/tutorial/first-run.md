---
type: tutorial
description: "教程：用 Docker 在自己的电脑上跑起 alldayrec，在浏览器里录一段话，再在网页上看到它的转写，全程大约 15 分钟。"
---

# 第一次跑起来

在这个教程里，我们用 Docker 在你自己的电脑上跑起 alldayrec，用浏览器录一段话，然后在网页上看到它的转写。全程大约 15 分钟。

## 准备

- 一台装了 Docker 的电脑（amd64 或 arm64），能访问 GitHub。
- 电脑上有麦克风，浏览器用 Chrome 或 Edge：网页录音页按 WebM/Opus 格式录音，浏览器要支持这种格式。
- 一把[阿里云百炼（DashScope）](https://bailian.console.aliyun.com/)北京区的 API key。这一把 key 同时用于语音识别和大模型。

## 1. 取得代码

```bash
git clone https://github.com/suyiiyii/alldayrec
cd alldayrec
```

## 2. 填上 key

复制配置模板：

```bash
cp .env.example .env
```

用编辑器打开 `.env`，找到下面两行，在等号后面填上你的百炼 key，其他行保持不变：

```ini
DASHSCOPE_API_KEY=<你的百炼 key>
LLM_API_KEY=<同一把百炼 key>
```

## 3. 生成你的 token

服务器靠 `data/tenants.json` 里的 token 认人。生成一个：

```bash
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
cat data/tenants.json
```

你会看到一行 JSON，`"token"` 后面是 32 位十六进制字符。把它记下来，后面要用。

## 4. 启动服务器

```bash
docker compose up -d
docker compose logs -f
```

第一次启动时，服务器会下载 4 个模型文件（约 70 MB）并校验，然后开始监听。日志的最后几行类似这样：

```text
alldayrec-1  | ... alldayrec.infer INFO models: downloading fireredvad-stream-vad.onnx from https://raw.githubusercontent.com/FireRedTeam/FireRedVAD/...
alldayrec-1  | ... alldayrec.infer INFO models: downloading fireredvad-cmvn.ark from https://raw.githubusercontent.com/FireRedTeam/FireRedVAD/...
alldayrec-1  | ... alldayrec.infer INFO models: downloading redimnet2-b6.onnx from https://github.com/suyiiyii/alldayrec/releases/download/models-v1/redimnet2-b6.onnx
alldayrec-1  | ... alldayrec.infer INFO models: downloading yamnet.onnx from https://github.com/suyiiyii/alldayrec/releases/download/models-v1/yamnet.onnx
alldayrec-1  | ... live INFO models ok: 4 files in /data/models
alldayrec-1  | ... live INFO tenants: main
alldayrec-1  | ... live INFO ready on 0.0.0.0:8770
```

看到 `ready on 0.0.0.0:8770` 后，按 <kbd>Ctrl</kbd>+<kbd>C</kbd> 停止跟随日志。服务器会继续在后台运行。

## 5. 打开网页

在浏览器里打开下面的地址，把 `<token>` 换成第 3 步记下的 token：

```text
http://127.0.0.1:8770/?token=<token>
```

页面打开后，地址栏里的 token 会消失：服务器已经用一个 cookie 记住了你，之后在这个浏览器里直接打开 `http://127.0.0.1:8770/` 就行。

## 6. 录一段话

1. 打开 `http://127.0.0.1:8770/record`。这是网页录音页。
2. 音源保持 **本机麦克风**，点 **开始录音**。浏览器询问是否允许使用麦克风时，选择允许。
3. 正常说一两分钟话，比如讲讲今天要做的事。页面顶部会显示「录音中 · 已传 N 块 · 待传 0」，每 20 秒多一块。
4. 点 **停止**。

## 7. 看到转写

页面下半部分是实时字幕。服务器把音频交给语音识别，转写好的句子会出现在这里，每一句前面标着说话人。

再打开 `http://127.0.0.1:8770/conversations`，你刚才录的这段已经是一段对话，点开它可以看到原文。

## 你做到了什么

你在自己的电脑上跑起了 alldayrec，录了一段话，并在网页上看到了带说话人的转写。录音和转写都在 `data/` 目录里。

接下来可以：

- [让手机连上服务器](../how-to/remote-access.md)，再[安装并连接手机 App](../how-to/install-app.md)，用手机全天录音。
- 看看[架构与数据流](../explanation/architecture.md)，了解音频进来后发生了什么。
- 不用了的话，在 `alldayrec` 目录里运行 `docker compose down` 停掉服务器；`data/` 目录会留着。
