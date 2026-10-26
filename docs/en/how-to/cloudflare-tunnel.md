---
type: how-to
description: "Give a home AllDayRec server a public HTTPS address with Cloudflare Tunnel, using a temporary URL without a domain or a fixed URL with your domain."
---

# How to publish your server with Cloudflare Tunnel

Use `cloudflared` to give a home AllDayRec server a public HTTPS address for cloud agents.

## Prepare

- Run AllDayRec with a nonempty token in `tenants.json`. A tunnel exposes even a loopback-only service.
- Run `cloudflared` on the server machine. Replace `8770` throughout if you use another port.
- On macOS, run `brew install cloudflared`. On Linux, configure the vendor repository and install with your OS package manager: [downloads](https://developers.cloudflare.com/tunnel/downloads/).

Tunnel commands below were checked against official documentation, but are untested in this project environment. Run them on your own machine and account; they create public access.

Check the local server:

```bash
curl -fsS http://localhost:8770/api/health
```

Expect `{"ok":true}`.

## Without a domain: try a temporary URL

```bash
cloudflared tunnel --url http://localhost:8770
```

Keep it running and copy the random HTTPS `trycloudflare.com` address. No Cloudflare account or domain is required. **Restarting cloudflared changes the URL; update your agent's connector each time.** Stopping the process ends access. Use it for trials.

Quick Tunnels have no uptime guarantee, allow 200 in-flight requests and do not support SSE. AllDayRec MCP returns JSON to POST requests. Use the command above for cloud agents; email login cannot serve non-interactive clients. Source: [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## With a Cloudflare domain: use a fixed URL

You need a Cloudflare account and a domain using Cloudflare DNS.

1. Log in and create a named tunnel:

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create alldayrec
   ```

2. Put the returned UUID and absolute credentials-file path in `~/.cloudflared/config.yml`, replacing the placeholders:

   ```yaml
   url: http://localhost:8770
   tunnel: <TUNNEL_UUID>
   credentials-file: /absolute/path/to/.cloudflared/<TUNNEL_UUID>.json
   ```

3. Replace the example hostname with yours, create DNS and run:

   ```bash
   cloudflared tunnel route dns alldayrec alldayrec.example.com
   cloudflared tunnel run alldayrec
   ```

   Sources: [named tunnel](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/) and [configuration](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/).

4. After checking access, stop the foreground cloudflared and install a service.

   Linux:

   ```bash
   sudo cloudflared --config "$HOME/.cloudflared/config.yml" service install
   sudo systemctl start cloudflared
   sudo systemctl status cloudflared
   ```

   macOS, starting at user login with `~/.cloudflared/config.yml`:

   ```bash
   cloudflared service install
   ```

   Sources: [Linux service](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/linux/) and [macOS login service](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/macos/). Keep AllDayRec running too.

## Connect your agent

Request `/api/health` at the public address and expect `{"ok":true}`. Other web/API requests still need the tenant token. Avoid interactive login gates that your agent cannot complete.

Select this HTTPS address in AllDayRec's `/connect`, then [create an AI connection link](./connect-mcp.md#create-an-ai-connection-link) for your agent. A named tunnel keeps its hostname across restarts.

The connection link is a key. Reverse-proxy access logs contain its full URL: disable or redact those logs. Cloudflare terminates public TLS and carries the traffic; see [Privacy](../explanation/privacy.md).
