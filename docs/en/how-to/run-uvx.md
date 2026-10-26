---
type: how-to
description: "Run the alldayrec server from PyPI with uvx or pip, web pages included: no Docker, no clone of the code. Needs uv or Python 3.11–3.14, and ffmpeg."
---

# How to run with uvx or pip

The server is published on PyPI as `alldayrec`, with the web pages packed in: no Docker, no clone of the code.

::: info Version
`alldayrec` is on PyPI from v0.2.0; v0.1.0 is a Docker image only.
:::

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (for uvx; it downloads a Python itself when there is no 3.11 to 3.14), or Python 3.11 to 3.14 and pip
- ffmpeg: `brew install ffmpeg` on macOS, `sudo apt install ffmpeg` on Debian and Ubuntu

## Steps

1. In an empty directory, make a `.env` with your keys (every setting is in [Configuration](../reference/configuration.md)):

   ```ini
   DASHSCOPE_API_KEY=<your Bailian key>
   LLM_API_KEY=<the same Bailian key>
   LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
   LLM_MODEL=qwen-plus
   ```

2. Start it:

   ```bash
   uvx --env-file .env alldayrec
   ```

   The first start downloads the model files (about 70 MB) and makes `tenants.json`. The log has a line like this:

   ```text
   ... live INFO first start: /home/you/.local/share/alldayrec/tenants.json made, one tenant; on this machine open http://127.0.0.1:8770/connect?token=<token>
   ```

3. Open the link in that line on this computer. Once the page is open, the token disappears from the address bar: the server remembers you with a cookie.

With pip, step 2 is:

```bash
pip install alldayrec
set -a && . ./.env && set +a && alldayrec
```

## Notes

- All data is in `DATA_DIR`: by default your user data directory (`~/.local/share/alldayrec` on Linux, `~/Library/Application Support/alldayrec` on macOS), with `tenants.json`, the model files in `models/`, and the database and recordings of the tenant `main`. Set `DATA_DIR` in `.env` to put it elsewhere; see [Data layout](../reference/data.md).
- The server listens on `127.0.0.1` only by default. For phones to connect directly, add `HOST=0.0.0.0` to `.env`; see [Reach the server from a phone](./remote-access.md).
- `ENGINE=local` (speech recognition on this machine) needs the `local` extra: `uvx --env-file .env --from 'alldayrec[local]' alldayrec`, or `pip install 'alldayrec[local]'`.
- Another port: `alldayrec --port 8771`, or `PORT` in `.env`.
- To upgrade, see [Upgrade and roll back](./upgrade.md).
