---
type: tutorial
description: "Tutorial: run alldayrec with Docker on your computer, record a few sentences in the browser and see their transcript in the web UI. About 15 minutes."
---

# Your first transcript

In this tutorial, you run alldayrec with Docker on your own computer, record a few sentences in the browser, and then see their transcript in the web UI. It takes about 15 minutes.

## What you need

- A computer with Docker (amd64 or arm64) that can reach GitHub.
- A microphone on the computer, and Chrome or Edge as the browser: the web recording page records in WebM/Opus, and the browser must support that format.
- An [Alibaba Cloud Bailian (DashScope)](https://bailian.console.aliyun.com/) API key for the Beijing region. This one key serves both the ASR and the LLM.

## 1. Get the code

```bash
git clone https://github.com/suyiiyii/alldayrec
cd alldayrec
```

## 2. Fill in the key

Copy the configuration template:

```bash
cp .env.example .env
```

Open `.env` in an editor, find the two lines below, and put your Bailian key after the equals sign. Leave the other lines as they are:

```ini
DASHSCOPE_API_KEY=<your Bailian key>
LLM_API_KEY=<the same Bailian key>
```

## 3. Generate your token

The server knows who you are by the token in `data/tenants.json`. Generate one:

```bash
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
cat data/tenants.json
```

You see one line of JSON, with 32 hexadecimal characters after `"token"`. Note them down: you need the token later.

## 4. Start the server

```bash
docker compose up -d
docker compose logs -f
```

At the first start, the server downloads 4 model files (about 70 MB), checks them, and then starts listening. The last lines of the log look like this:

```text
alldayrec-1  | ... alldayrec.infer INFO models: downloading fireredvad-stream-vad.onnx from https://raw.githubusercontent.com/FireRedTeam/FireRedVAD/...
alldayrec-1  | ... alldayrec.infer INFO models: downloading fireredvad-cmvn.ark from https://raw.githubusercontent.com/FireRedTeam/FireRedVAD/...
alldayrec-1  | ... alldayrec.infer INFO models: downloading redimnet2-b6.onnx from https://github.com/suyiiyii/alldayrec/releases/download/models-v1/redimnet2-b6.onnx
alldayrec-1  | ... alldayrec.infer INFO models: downloading yamnet.onnx from https://github.com/suyiiyii/alldayrec/releases/download/models-v1/yamnet.onnx
alldayrec-1  | ... live INFO models ok: 4 files in /data/models
alldayrec-1  | ... live INFO tenants: main
alldayrec-1  | ... live INFO ready on 0.0.0.0:8770
```

When you see `ready on 0.0.0.0:8770`, press <kbd>Ctrl</kbd>+<kbd>C</kbd> to stop following the log. The server keeps running in the background.

## 5. Open the web UI

Open the address below in your browser, with `<token>` replaced by the token you noted in step 3:

```text
http://127.0.0.1:8770/?token=<token>
```

Once the page opens, the token disappears from the address bar: the server now remembers you with a cookie. From now on, open `http://127.0.0.1:8770/` directly in this browser.

## 6. Record a few sentences

1. Open `http://127.0.0.1:8770/record`. This is the web recording page.
2. Leave the source at **本机麦克风** (This device's microphone) and click **开始录音** (Start recording). When the browser asks whether to allow the microphone, allow it.
3. Talk normally for a minute or two, for example about what you plan to do today. The top of the page shows 「录音中 · 已传 N 块 · 待传 0」 (Recording · N pieces sent · 0 waiting), with one more piece every 20 seconds.
4. Click **停止** (Stop).

## 7. See the transcript

The lower half of the page shows live captions. The server hands the audio to the ASR engine, and the transcribed sentences appear here, each with its speaker in front.

Then open `http://127.0.0.1:8770/conversations`. What you just recorded is already a conversation; open it to see its transcript.

## What you've done

You ran alldayrec on your own computer, recorded a few sentences, and saw their transcript with speakers in the web UI. The recording and the transcript are both in the `data/` directory.

Next, you can:

- [Reach the server from a phone](../how-to/remote-access.md), then [install and connect the phone app](../how-to/install-app.md), to record all day with your phone.
- Read [Architecture and data flow](../explanation/architecture.md) to learn what happens after the audio comes in.
- When you no longer need it, run `docker compose down` in the `alldayrec` directory to stop the server; the `data/` directory stays.
