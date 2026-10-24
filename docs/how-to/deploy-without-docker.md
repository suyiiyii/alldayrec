---
type: how-to
---

# 如何不用 Docker 部署

这篇讲直接在一台机器上从源码运行服务器。要用 systemd 长期运行并从别的机器部署，见[如何用 deploy.sh 部署](./deploy-sh.md)。

## 前提

- Python 3.11 和 [uv](https://docs.astral.sh/uv/)
- ffmpeg
- Node 22 和 pnpm（构建网页用）

## 步骤

1. 取得代码并复制配置模板：

   ```bash
   git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
   cp .env.example .env
   ```

2. 编辑 `.env`：填 key（见[配置项](../reference/configuration.md)），并把 `MODELS_DIR` 设成一个可写目录的绝对路径。默认的 `/opt/alldayrec/models` 一般需要 root 才能写。

3. 构建网页，输出到 `server/static/dist`：

   ```bash
   (cd web && pnpm install && pnpm build)
   ```

4. 在仓库根目录生成 `tenants.json`：

   ```bash
   echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "'$PWD/data/main'", "recordings": "'$PWD/data/main/recordings'"}]' > tenants.json && chmod 600 tenants.json
   ```

5. 安装依赖，读入 `.env`，启动：

   ```bash
   cd server && uv sync && set -a && . ../.env && set +a && uv run python server.py
   ```

   日志出现 `ready on 127.0.0.1:8770` 后，打开 `http://127.0.0.1:8770/?token=<你的 token>`。

## 说明

- 服务器从 `server/` 的上一级目录读 `tenants.json`（`server/tenant.py`）。格式见[配置项](../reference/configuration.md#tenants-json)。
- `HOST` 默认是 `127.0.0.1`，只有这台机器能访问。反向代理在另一台机器上时，设 `HOST=0.0.0.0`。
- `tenants.json` 里只有一条、且 token 为空时，服务器不做认证。这样的服务只能放在 `127.0.0.1` 上。
