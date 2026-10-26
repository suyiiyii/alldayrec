---
type: how-to
---

# 如何从源码运行

这篇讲克隆代码、直接在一台机器上运行服务器，适合要改代码的人。只想跑起来，[用 uvx 或 pip](./run-uvx.md) 更省事。要用 systemd 长期运行并从别的机器部署，见[如何用 deploy.sh 部署](./deploy-sh.md)。

## 前提

- [uv](https://docs.astral.sh/uv/)（它会自己准备 Python 3.11）
- ffmpeg
- Node 22 和 pnpm（构建网页用）

## 步骤

1. 取得代码并复制配置模板：

   ```bash
   git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
   cp .env.example .env
   ```

2. 编辑 `.env`，填 key（见[配置项](../reference/configuration.md)）。

3. 构建网页，输出到 `server/alldayrec/static/dist`：

   ```bash
   (cd web && pnpm install && pnpm build)
   ```

   不构建也能启动，API 照常工作，网页地址只回一句「the web app is not built」。

4. 安装依赖，读入 `.env`，启动：

   ```bash
   cd server && uv sync && set -a && . ../.env && set +a && uv run alldayrec
   ```

   第一次启动时，日志里有一行 `first start: ... open http://127.0.0.1:8770/connect?token=<token>`。日志出现 `ready on 127.0.0.1:8770` 后，在这台电脑上打开那个链接。

## 说明

- 服务器从 `DATA_DIR/tenants.json` 读租户（`server/alldayrec/tenant.py`）。`DATA_DIR` 默认是用户数据目录，文件不存在时第一次启动会生成一个；格式见[配置项](../reference/configuration.md#tenants-json)。
- `uv run alldayrec` 和 `uv run python -m alldayrec` 是一回事。用 `ENGINE=local` 时把 `uv sync` 换成 `uv sync --extra local`。
- `HOST` 默认是 `127.0.0.1`，只有这台机器能访问。反向代理在另一台机器上时，设 `HOST=0.0.0.0`。
- `tenants.json` 里只有一条、且 token 为空时，服务器不做认证。这样的服务只能放在 `127.0.0.1` 上。

没有公网服务器或域名时，可按 [Cloudflare Tunnel](./cloudflare-tunnel.md) 获取公网 HTTPS 地址，再连接手机或云端 Agent。
