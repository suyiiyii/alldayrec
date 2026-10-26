---
type: how-to
description: "Let the phone reach your AllDayRec server: an http:// address inside Tailscale or a LAN, or https:// from the internet via a TLS reverse proxy."
---

# How to reach the server from a phone

Run with uvx, pip or from source, the server listens only on `127.0.0.1:8770` on its own machine by default; with Docker Compose the port is published on every interface. To upload recordings, the phone has to reach it. This page gives two ways to do that, depending on your network.

::: warning App version
Since `apk-v1.5.1` the app connects to `http://` addresses: inside Tailscale or a LAN, enter the `http://` address directly (an iOS build from main too, see [Install and connect the phone app](./install-app.md)). Older APKs (1.5.0 and before) connect only to `https://`: upgrade, or follow [The phone connects over the public internet](#the-phone-connects-over-the-public-internet) below.

:::

## The phone and the server are on the same Tailscale network or LAN

1. Make the server reachable on its Tailscale or LAN address.

   - Docker: `docker-compose.yml` already publishes the port on every interface; nothing to change. To publish it only on the Tailscale address, change the port line to this, then run `docker compose up -d`:

     ```yaml
     ports:
       - "100.x.y.z:8770:8770"  # only on this machine's Tailscale address
     ```

   - Without Docker: set `HOST=0.0.0.0` in `.env` and restart the server.

2. In a browser on your computer, open the web UI at the address the phone will use, e.g. `http://100.x.y.z:8770/?token=<your token>` (a Tailscale address) or `http://192.168.1.10:8770/?token=<your token>` (a LAN address).

3. Open **连接手机** (Connect a phone, `/connect`) and scan the QR code with the app. The QR code carries the address picked under 「手机用哪个地址」 (Address for the phone): with the page opened at `127.0.0.1`, the first Tailscale or LAN address the server finds is picked by default. In Docker the server can't see the computer's interfaces, so the list has only this page's own address: open the page at the computer's LAN or Tailscale address, or pick 「自己填」 (Enter it yourself).

## The phone connects over the public internet

1. Put a reverse proxy with TLS in front of port 8770, such as Caddy, nginx, a Cloudflare Tunnel or Tailscale Serve. The proxy must forward WebSockets (the web UI receives pushes on `/ws`).
2. Point the proxy's health check at `GET /api/health`: it needs no token and returns `{"ok":true}`.
3. Open the web UI at `https://<your-domain>/?token=<your token>`, then scan the QR code on **连接手机**.

Don't expose a server whose token is empty: anyone could then read and write your data.

With nginx: by default it accepts request bodies of up to 1 MB only (`client_max_body_size`), so it rejects larger recording files with 413 when you [import recordings](./import-recordings.md). Raise the limit.

For a home server or a setup without a domain, use [Cloudflare Tunnel](./cloudflare-tunnel.md) to get a public HTTPS address for phones or cloud agents.
