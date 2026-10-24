---
type: how-to
---

# How to deploy without Docker

This page runs the server from source directly on a machine. To run it long-term under systemd and deploy it from another machine, see [Deploy with deploy.sh](./deploy-sh.md).

## Prerequisites

- Python 3.11 and [uv](https://docs.astral.sh/uv/)
- ffmpeg
- Node 22 and pnpm (to build the web UI)

## Steps

1. Get the code and copy the config template:

   ```bash
   git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
   cp .env.example .env
   ```

2. Edit `.env`: fill in the keys (see [Configuration](../reference/configuration.md)), and set `MODELS_DIR` to the absolute path of a writable directory. The default `/opt/alldayrec/models` usually needs root to write to.

3. Build the web UI into `server/static/dist`:

   ```bash
   (cd web && pnpm install && pnpm build)
   ```

4. Generate `tenants.json` at the repository root:

   ```bash
   echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "'$PWD/data/main'", "recordings": "'$PWD/data/main/recordings'"}]' > tenants.json && chmod 600 tenants.json
   ```

5. Install the dependencies, load `.env` and start the server:

   ```bash
   cd server && uv sync && set -a && . ../.env && set +a && uv run python server.py
   ```

   Once the log shows `ready on 127.0.0.1:8770`, open `http://127.0.0.1:8770/?token=<your token>`.

## Notes

- The server reads `tenants.json` from the directory above `server/` (`server/tenant.py`). For the format, see [Configuration](../reference/configuration.md#tenants-json).
- `HOST` defaults to `127.0.0.1`, so only this machine can reach the server. When the reverse proxy runs on another machine, set `HOST=0.0.0.0`.
- When `tenants.json` has a single entry and its token is empty, the server does no authentication. Keep such a server on `127.0.0.1` only.
