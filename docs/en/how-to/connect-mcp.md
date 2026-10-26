---
type: how-to
description: "Connect Claude Code, Codex, Cursor or Cherry Studio to AllDayRec's remote MCP endpoint /mcp to search your conversations, summaries and to-dos."
---

# How to connect an AI agent over MCP

The server offers a remote MCP endpoint at `/mcp`. AI agents such as Claude Code, Codex, Cursor and Cherry Studio use it to search your conversations, summaries and to-dos, and to mark a to-do as done or wrong. Clients that accept headers use the app’s bearer token; cloud agents use a separate AI connection link.

## Before you start

- Once connected, the transcripts and summaries the AI finds (including what other people said) go to the AI vendor you picked.
- In sessions that use this connection, don't auto-approve web or shell tools: words in a recording can be taken as instructions and use those tools to send content out.
- The tenant token reads and writes all your data; an AI connection link grants all your MCP tools, including todo updates. Treat both as keys.

The folded **高级：接入 AI** (Advanced: Connect AI) section of the web page **连接手机** (Connect a phone, `/connect`) lists the settings below with the address and token filled in. Click **复制** (Copy).

## Claude Code

Run this once in a terminal:

```bash
claude mcp add --transport http alldayrec https://<your-server>/mcp --header "Authorization: Bearer <token>" --scope user
```

## Codex

Add this to `~/.codex/config.toml`:

```toml
[mcp_servers.alldayrec]
url = "https://<your-server>/mcp"
http_headers = { "Authorization" = "Bearer <token>" }
```

## Cursor

Add this to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "alldayrec": {
      "url": "https://<your-server>/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

## Cherry Studio

Paste this in **Settings › MCP › Add › Import from JSON**:

```json
{
  "mcpServers": {
    "alldayrec": {
      "type": "streamableHttp",
      "url": "https://<your-server>/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

## Check the connection

List the tools with curl, without an AI agent:

```bash
curl -s -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}' https://<your-server>/mcp
```

In the JSON that comes back, `result.tools` is the list of tools. What each tool does is in [MCP tools](../reference/mcp.md).

## OpenClaw and Hermes

Both open-source clients accept bearer headers. Their native MCP clients passed initialization, tool discovery and a `todos` query; model-driven tool selection requires a separate check.

### OpenClaw

Merge into `openclaw.json`:

```json
{
  "mcp": {
    "servers": {
      "alldayrec": {
        "url": "https://alldayrec.example.com/mcp",
        "transport": "streamable-http",
        "headers": { "Authorization": "Bearer ${ALLDAYREC_TOKEN}" }
      }
    }
  }
}
```

Set `ALLDAYREC_TOKEN` in the agent's environment, then run `openclaw mcp doctor alldayrec --probe`. Configuration: [OpenClaw MCP](https://docs.openclaw.ai/tools/mcp).

### Hermes

Merge into `~/.hermes/config.yaml` and set `ALLDAYREC_TOKEN` in Hermes's environment:

```yaml
mcp_servers:
  alldayrec:
    url: "https://alldayrec.example.com/mcp"
    headers:
      Authorization: "Bearer ${ALLDAYREC_TOKEN}"
```

Configuration: [Hermes MCP](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference). Ask the agent for todos in a date range and check that it calls `todos`.

## Cloud personal agents: Grok Bot and Dots

### Create an AI connection link

1. Obtain a public HTTPS address. For a home machine or a setup without a domain, use [Cloudflare Tunnel](./cloudflare-tunnel.md).
2. Sign in to AllDayRec and select that public address at the top of `/connect`.
3. Open **Advanced: connect an AI → AI connection links** and create a link named after your agent.
4. Copy the full `https://alldayrec.example.com/mcp/k/<secret>` link. It appears once; the list then keeps its name, prefix, creation time and last use.

Each account can keep up to 20 links; revoke an old one before adding another at the limit. Last use records successful MCP calls, at most once per minute per link. Revocation blocks new calls and waits up to 2 seconds for existing calls to finish. If calls remain active, the web page says they may still finish. An already-computed response may still be in transit.

The link is a key. Create one per agent and revoke it when finished. It grants all MCP tools, including todo updates, but cannot log in to the web UI or use other HTTP APIs. The server does not log credential paths; reverse-proxy access logs contain the full URL. Disable or redact logs for this path, and keep the link out of chats, screenshots and shared configs.

### Grok Bot

Requires paid Cursor individual / Teams, SuperGrok / Plus / Heavy linked to Cursor, or a self-serve Grok Business seat: [eligibility](https://docs.x.ai/grok-bot/get-started).

Open **Connect apps** in the sidebar (mobile: top-left avatar → Connect apps). In the custom connector form, set **Name** to AllDayRec and **Server URL** to the full connection link. Save, check Installed, then ask the Bot for todos. The [official guide](https://cursor.com/help/grok-bot/connect-apps) covers the entry point; the two custom fields reflect the current UI. Menu labels can vary by account.

### ChatGPT / Dots

On the web, open **ChatGPT Plugins → + → Add custom MCP server**. Enter AllDayRec, paste the full link under Connection's Server URL, select **No authentication**, accept the risk prompt and **Create as a plugin**, then install it. The [official instructions](https://developers.openai.com/api/docs/guides/custom-mcp-server) now use Plugins; accounts with a developer-mode / custom-connector entry should follow their current UI. Workspace controls apply.

Dots rolls out to Pro 100/200/500 (adults outside the EEA, UK and Switzerland), Business Premium and Enterprise. Enterprise admins must enable it: [eligibility](https://learn.chatgpt.com/docs/dots#access). Dots uses [supported installed plugins](https://learn.chatgpt.com/docs/dots/computers-and-apps). Select the plugin and request `todos` for a date range to verify your cloud account's connection.
