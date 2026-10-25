---
type: how-to
description: "把 Claude Code、Codex、Cursor、Cherry Studio 接入 alldayrec 的远程 MCP 端点 /mcp，让 AI 助手查你的对话、摘要和待办。"
---

# 如何把 AI 助手接入 MCP

服务器在 `/mcp` 提供一个远程 MCP 端点，让 Claude Code、Codex、Cursor、Cherry Studio 这类 AI 助手查你的对话、摘要和待办，也能把待办标成完成或不对。它用的 token 和 App 一样。

## 开始之前

- 接入后，AI 查到的转写和摘要（包括别人说的话）会发给你自己选的 AI 厂商。
- 在用这个连接的会话里，不要给联网和 Shell 工具开自动批准：录音里的话可能被当成指令，借这些工具把内容发出去。
- token 能读写这台服务器上你的全部数据，只放在自己的电脑上。

网页 **连接手机** 页（`/connect`）里折叠着的 **高级：接入 AI** 一节会列出下面这些配置，地址和 token 已经填好，点 **复制** 就行。

## Claude Code

在终端里运行一次：

```bash
claude mcp add --transport http alldayrec https://<你的服务器>/mcp --header "Authorization: Bearer <token>" --scope user
```

## Codex

加进 `~/.codex/config.toml`：

```toml
[mcp_servers.alldayrec]
url = "https://<你的服务器>/mcp"
http_headers = { "Authorization" = "Bearer <token>" }
```

## Cursor

加进 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "alldayrec": {
      "url": "https://<你的服务器>/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

## Cherry Studio

在 **设置 › MCP › 添加 › 从 JSON 导入** 里粘贴：

```json
{
  "mcpServers": {
    "alldayrec": {
      "type": "streamableHttp",
      "url": "https://<你的服务器>/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

## 检查连接

不经过 AI 助手，直接用 curl 列出工具：

```bash
curl -s -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}' https://<你的服务器>/mcp
```

返回的 JSON 里 `result.tools` 是工具列表。每个工具是做什么的，见 [MCP 工具](../reference/mcp.md)。
