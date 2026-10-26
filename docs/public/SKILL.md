---
name: alldayrec-deploy
description: Deploy AllDayRec (全天录), the self-hosted all-day recorder, with Docker Compose, give its owner a token, connect the phone app, and connect an AI agent to its MCP endpoint. Use when someone asks to install, self-host, set up or connect AllDayRec.
---

# Deploy and connect AllDayRec

AllDayRec is a server (FastAPI, SQLite, Docker image `ghcr.io/suyiiyii/alldayrec`) plus a web UI, a phone app and an
optional Bluetooth recorder. Repo: https://github.com/suyiiyii/alldayrec. The links below are relative to the URL this
file was served from; every page also exists as Markdown at the same path (`.md`). Chinese pages sit at the same
paths without `en/`.

Treat the API keys and the tenant token as passwords: take keys from the user, write them only into `.env`, and never
print them, paste them into chat, commit them or put them on a command line that others can see.

## 1. Check what you need

- A Linux or macOS machine with Docker and Compose, amd64 or arm64.
- An Alibaba Cloud Bailian (DashScope) API key, Beijing region. With the defaults this one key serves both speech
  recognition and the LLM. Other engines: [Change the ASR engine](en/how-to/change-asr-engine.md).
- A model-restricted Bailian key makes the LLM calls fail with 403: use a key without model limits, or point `LLM_*` at
  another OpenAI-compatible endpoint.

## 2. Start the server

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env
```

In `.env`, set `DASHSCOPE_API_KEY` and `LLM_API_KEY` (the same Bailian key with the example LLM settings). Keep
comments on their own lines: Compose reads `VAR=   # comment` as the value `# comment`. `TZ` sets the time zone of
conversation dates and of the daily recap. All settings: [Configuration](en/reference/configuration.md).

Create the tenant file; it holds the token:

```bash
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
docker compose up -d
```

The first start downloads about 70 MB of model files. Wait until `docker compose logs --tail 20` shows
`ready on 0.0.0.0:8770` (don't use `logs -f` in a non-interactive shell: it never returns), then check:

```bash
curl -s http://127.0.0.1:8770/api/health   # {"ok":true}
```

If it stops instead, the log names the cause: `not set: ...` (a required `.env` setting is empty),
`others can read it ... chmod 600` (the mode of `data/tenants.json`), or the `ENGINE` value.

`docker compose up -d` does not pull a newer image; to update, run `docker compose pull && docker compose up -d`.
The `latest` tag is the newest release tag (`v*`); the `main` tag follows the main branch.
Details: [Deploy with Docker](en/how-to/deploy-docker.md).

## 3. Hand the token to the user

The token is the `"token"` value in `data/tenants.json`. Tell the user where it is, or give it to them privately. They
open `http://127.0.0.1:8770/?token=<token>` once in a browser on that machine; the server then sets a cookie. For a
remote machine they first run `ssh -L 8770:127.0.0.1:8770 <user>@<host>` on their own computer.

## 4. Connect the phone

`docker-compose.yml` publishes port 8770 on every interface; the token in `data/tenants.json` is the only lock (the
server refuses to start with an empty one). Choose with the user:

- **HTTPS:** put a TLS reverse proxy in front of port 8770 (Caddy, nginx,
  Cloudflare Tunnel, Tailscale Serve). It must pass WebSockets (`/ws`); its health check is `GET /api/health`. With
  nginx, raise `client_max_body_size` or large imports fail with 413.
- **Plain http on Tailscale or a LAN:** nothing to change; the phone uses `http://<LAN or Tailscale IP>:8770`. To
  publish only on the Tailscale address, change `ports:` to `"<tailscale-ip>:8770:8770"` and run `docker compose up -d`.
  The Android APK accepts `http://` since 1.5.1 (apk-v1.5.1).

Never expose a server whose token is empty.

Then the user opens the web page **at the address the phone will use** (not `127.0.0.1`), goes to 连接手机 (Connect
a phone, `/connect`), and in the app taps 我的 › 设置 › 扫码登录 (Me › Settings › Scan to sign in) to scan the QR code:
the QR code carries the address picked under 手机用哪个地址 (Address for the phone). In Docker the server can't list the
computer's addresses, so open the page at the computer's LAN or Tailscale IP, or type it under 自己填 (Enter it
yourself). App install (APK from the GitHub Releases, iOS built with Xcode):
[Install and connect the phone app](en/how-to/install-app.md). Network details:
[Reach the server from a phone](en/how-to/remote-access.md).

For a home server or a setup without a domain, follow [Cloudflare Tunnel](en/how-to/cloudflare-tunnel.md).

## 5. Connect an AI agent (MCP)

OpenClaw and Hermes also accept bearer headers. For Grok Bot, ChatGPT or Dots, have the user create a separate
**AI connection link** in `/connect` and paste it into the agent's private connector URL field, selecting
**No authentication** where offered. The link grants all MCP tools, including todo updates; it is a credential.
Never print it. Create one per agent and revoke unused links. Reverse-proxy logs must redact credential paths.
Client configs, plan requirements and setup: [Connect over MCP](en/how-to/connect-mcp.md).

The MCP endpoint is `POST <server>/mcp` (Streamable HTTP, stateless, JSON only) with the header
`Authorization: Bearer <token>`. Claude Code:

```bash
claude mcp add --transport http alldayrec https://<your-server>/mcp --header "Authorization: Bearer <token>" --scope user
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.alldayrec]
url = "https://<your-server>/mcp"
http_headers = { "Authorization" = "Bearer <token>" }
```

Check it without an agent; `result.tools` lists the tools:

```bash
curl -s -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}' https://<your-server>/mcp
```

Tell the user before they connect: the transcripts the agent reads (other people's words included) go to the agent's
vendor; don't auto-approve web or shell tools in that session, since words in a recording can act as instructions.
Other clients and the tool list: [Connect an AI agent over MCP](en/how-to/connect-mcp.md),
[MCP tools](en/reference/mcp.md).

## More

- [Troubleshoot common problems](en/how-to/troubleshooting.md)
- [Back up and restore](en/how-to/backup.md), [Upgrade and roll back](en/how-to/upgrade.md)
- [HTTP API](en/reference/api.md), all pages: [llms.txt](llms.txt)
