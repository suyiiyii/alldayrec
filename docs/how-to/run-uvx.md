---
type: how-to
description: "不用 Docker、不用克隆代码：用 uvx 或 pip 从 PyPI 运行 alldayrec 服务器，网页已经打包在里面。需要 uv 或 Python 3.11–3.14，以及 ffmpeg。"
---

# 如何用 uvx 或 pip 运行

服务器发布在 PyPI 上，包名 `alldayrec`，网页已经打包在里面：不用 Docker，也不用克隆代码。

::: info 适用版本
PyPI 上的 `alldayrec` 从 v0.1.0 之后的第一个版本标签起发布；v0.1.0 只有 Docker 镜像。
:::

## 前提

- [uv](https://docs.astral.sh/uv/)（用 uvx 时；本机没有 Python 3.11 到 3.14，它会自己下载一个），或者 Python 3.11 到 3.14 和 pip
- ffmpeg：macOS 用 `brew install ffmpeg`，Debian、Ubuntu 用 `sudo apt install ffmpeg`

## 步骤

1. 在一个空目录里建 `.env`，填上 key（全部设置项见[配置项](../reference/configuration.md)）：

   ```ini
   DASHSCOPE_API_KEY=<你的百炼 key>
   LLM_API_KEY=<同一把百炼 key>
   LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
   LLM_MODEL=qwen-plus
   ```

2. 启动：

   ```bash
   uvx --env-file .env alldayrec
   ```

   第一次启动会下载模型文件（约 70 MB）并生成 `tenants.json`，日志里有这样一行：

   ```text
   ... live INFO first start: /home/you/.local/share/alldayrec/tenants.json made, one tenant; on this machine open http://127.0.0.1:8770/connect?token=<token>
   ```

3. 在这台电脑上打开这一行里的链接。页面打开后，地址栏里的 token 会消失：服务器已经用一个 cookie 记住了你。

用 pip 时，把第 2 步换成：

```bash
pip install alldayrec
set -a && . ./.env && set +a && alldayrec
```

## 说明

- 数据都在 `DATA_DIR`：默认是用户数据目录（Linux 是 `~/.local/share/alldayrec`，macOS 是 `~/Library/Application Support/alldayrec`），里面有 `tenants.json`、模型文件 `models/`，以及租户 `main` 的数据库和录音。在 `.env` 里设 `DATA_DIR` 可以换地方，见[数据目录](../reference/data.md)。
- 服务默认只在 `127.0.0.1` 上监听。要让手机直连，在 `.env` 里加 `HOST=0.0.0.0`，见[如何让手机连上服务器](./remote-access.md)。
- `ENGINE=local`（在本机跑语音识别）需要带 `local` 的版本：`uvx --env-file .env --from 'alldayrec[local]' alldayrec`，或者 `pip install 'alldayrec[local]'`。
- 换端口：`alldayrec --port 8771`，或者在 `.env` 里设 `PORT`。
- 升级见[如何升级与回滚](./upgrade.md)。
