---
type: how-to
description: "Connect Claude Code, Codex, Cursor or Cherry Studio to alldayrec's remote MCP endpoint /mcp to search your conversations, summaries and to-dos."
---

# How to connect an AI agent over MCP

The server offers a remote MCP endpoint at `/mcp`. AI agents such as Claude Code, Codex, Cursor and Cherry Studio use it to search your conversations, summaries and to-dos, and to mark a to-do as done or wrong. It uses the same token as the app.

## Before you start

- Once connected, the transcripts and summaries the AI finds (including what other people said) go to the AI vendor you picked.
- In sessions that use this connection, don't auto-approve web or shell tools: words in a recording can be taken as instructions and use those tools to send content out.
- The token reads and writes all your data on this server. Keep it on your own computer only.

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
