---
type: how-to
---

# How to run from source

This page clones the code and runs the server directly on a machine, for when you want to change the code. To just run it, [uvx or pip](./run-uvx.md) is quicker. To run it long-term under systemd and deploy it from another machine, see [Deploy with deploy.sh](./deploy-sh.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (it fetches Python 3.11 itself)
- ffmpeg
- Node 22 and pnpm (to build the web UI)

## Steps

1. Get the code and copy the config template:

   ```bash
   git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
   cp .env.example .env
   ```

2. Edit `.env` and fill in the keys (see [Configuration](../reference/configuration.md)).

3. Build the web UI into `server/alldayrec/static/dist`:

   ```bash
   (cd web && pnpm install && pnpm build)
   ```

   Without it the server still starts and its API works; the page addresses only answer "the web app is not built".

4. Install the dependencies, load `.env` and start the server:

   ```bash
   cd server && uv sync && set -a && . ../.env && set +a && uv run alldayrec
   ```

   At the first start the log has a line `first start: ... open http://127.0.0.1:8770/connect?token=<token>`. Once it shows `ready on 127.0.0.1:8770`, open that link on this computer.

## Notes

- The server reads its tenants from `DATA_DIR/tenants.json` (`server/alldayrec/tenant.py`). `DATA_DIR` defaults to your user data directory, and a first start makes the file when it is missing; for the format, see [Configuration](../reference/configuration.md#tenants-json).
- `uv run alldayrec` is the same as `uv run python -m alldayrec`. For `ENGINE=local`, run `uv sync --extra local` instead of `uv sync`.
- `HOST` defaults to `127.0.0.1`, so only this machine can reach the server. When the reverse proxy runs on another machine, set `HOST=0.0.0.0`.
- When `tenants.json` has a single entry and its token is empty, the server does no authentication. Keep such a server on `127.0.0.1` only.
