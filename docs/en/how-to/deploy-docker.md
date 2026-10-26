---
type: how-to
description: "Run alldayrec long-term with Docker Compose on Linux or macOS: fill in .env, start it, pin an image version, set the time zone, find the data."
---

# How to deploy with Docker

This page runs alldayrec long-term with Docker Compose on a Linux or macOS machine. If you are new to it, go through [Your first transcript](../tutorial/first-run.md) first.

::: info Version
`docker-compose.yml` uses the image `ghcr.io/suyiiyii/alldayrec:latest` by default, the newest version tag; the `main` tag follows the main branch. To choose a version, see [Pin an image version](#pin-an-image-version) below.
:::

## Prerequisites

- Docker (with Compose), amd64 or arm64.
- An [Alibaba Cloud Bailian](https://bailian.console.aliyun.com/) API key, Beijing region, or a key for another ASR engine (see [Change the ASR engine](./change-asr-engine.md)).
- An OpenAI-compatible LLM endpoint; with Bailian, the same key works.

## Steps

1. Get the code and copy the config template:

   ```bash
   git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
   cp .env.example .env
   ```

2. Edit `.env` and fill in at least `DASHSCOPE_API_KEY` and `LLM_API_KEY`. With the LLM settings in `.env.example` (Bailian compatible mode, `qwen-plus`), put the same key in both. Every setting is in [Configuration](../reference/configuration.md).

   Put each comment on a line of its own: Compose reads `VAR=   # comment` as the value `# comment`.

3. Generate `data/tenants.json`, which holds your token:

   ```bash
   mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
   ```

4. Start it and watch the logs:

   ```bash
   docker compose up -d
   docker compose logs -f
   ```

   Once the log shows `ready on 0.0.0.0:8770`, the server is ready. The first start downloads the model files first (about 70 MB).

5. Open `http://<server IP>:8770/?token=<your token>` in a browser: `127.0.0.1` on this machine, this machine's LAN or Tailscale address from another computer. A cookie keeps you signed in after that.

`docker-compose.yml` publishes port 8770 on every interface of this machine, so a phone on the same LAN or Tailscale network can connect directly; see [Reach the server from a phone](./remote-access.md). The token in `data/tenants.json` is the only lock, and the server refuses to start with an empty one. To keep it on this machine only, change the line under `ports:` to `"127.0.0.1:8770:8770"`; to reach it over the public internet, put a reverse proxy with TLS in front.

## Common adjustments

### The key gets 403 on the chat model

If this Bailian key is restricted to specific models in the console, calls to the chat model (`qwen-plus`) return 403. Use a key without a model restriction, or point `LLM_*` at another LLM service.

### Time zone

The container uses UTC by default. `.env.example` has `TZ=Asia/Shanghai`: conversation dates and the daily recap time `RECAP_AT` follow this time zone. Delete that line to use UTC.

### Pin an image version

Replace the tag on the `image:` line of `docker-compose.yml` with any of the tags below, then run `docker compose up -d`:

| Tag | What it is |
|---|---|
| `latest` | The newest version tag (`v*`) |
| `0.1.0`, `0.1` | One version, or the newest version of a minor series |
| `main` | The latest build of the main branch |
| `sha-<7-character commit hash>` | One commit on main, e.g. `sha-1971bca` |

`.github/workflows/docker.yml` publishes these tags.

### Build the image locally

Uncomment the `# build: .` line in `docker-compose.yml`, then run:

```bash
docker compose up -d --build
```

## Where the data lives

All data is in `./data`: `tenants.json`, the model files (`data/models`), and each person's database and original audio (`data/main` in the quickstart). The full layout is in [Data layout](../reference/data.md); for backups, see [Back up and restore](./backup.md).
