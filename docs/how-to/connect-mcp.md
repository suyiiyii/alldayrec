---
type: how-to
description: "把 Claude Code、Codex、Cursor、Cherry Studio 接入 AllDayRec 的远程 MCP 端点 /mcp，让 AI 助手查你的对话、摘要和待办。"
---

# 如何把 AI 助手接入 MCP

服务器在 `/mcp` 提供一个远程 MCP 端点，让 Claude Code、Codex、Cursor、Cherry Studio 这类 AI 助手查你的对话、摘要和待办，也能把待办标成完成或不对。支持 header 的客户端使用和 App 一样的 bearer token；云端 Agent 使用单独的 AI 连接网址。

## 开始之前

- 接入后，AI 查到的转写和摘要（包括别人说的话）会发给你自己选的 AI 厂商。
- 在用这个连接的会话里，不要给联网和 Shell 工具开自动批准：录音里的话可能被当成指令，借这些工具把内容发出去。
- token 能读写你的全部数据；AI 连接网址可调用你的全部 MCP 工具，包括修改待办。两者都是密钥。

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

## OpenClaw 和 Hermes

这两个开源客户端支持 bearer header。原版 MCP 客户端已通过初始化、工具发现和 `todos` 查询；模型自主选工具的对话另行验证。

### OpenClaw

合并到 `openclaw.json`：

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

在启动 Agent 的环境中设置 `ALLDAYREC_TOKEN`，然后运行 `openclaw mcp doctor alldayrec --probe`。配置依据：[OpenClaw MCP](https://docs.openclaw.ai/tools/mcp)。

### Hermes

合并到 `~/.hermes/config.yaml`，在启动 Hermes 的环境中设置 `ALLDAYREC_TOKEN`：

```yaml
mcp_servers:
  alldayrec:
    url: "https://alldayrec.example.com/mcp"
    headers:
      Authorization: "Bearer ${ALLDAYREC_TOKEN}"
```

配置依据：[Hermes MCP](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference)。让 Agent 查询某段日期的待办，确认调用了 `todos`。

## Grok Bot、Dots 等云端个人 Agent

### 创建 AI 连接网址

1. 准备云端可访问的 HTTPS 地址。家用电脑或没有域名时，可用 [Cloudflare Tunnel](./cloudflare-tunnel.md)。
2. 登录 AllDayRec 网页，在 `/connect` 上方的地址选择中填入这个公网地址。
3. 展开 **高级：接入 AI → AI 连接网址**，按 Agent 名称创建网址，例如 Grok Bot。
4. 复制完整的 `https://alldayrec.example.com/mcp/k/<secret>`。完整网址只显示一次；之后列表保留名称、前缀、创建和上次使用时间。

每个账号最多保留 20 条连接网址，达到上限时先作废旧网址。上次使用时间只记录成功的 MCP 调用，每条网址每分钟最多更新一次。作废会停用新调用，最多等待 2 秒让已有调用结束；仍在执行时，网页会提示已有调用可能继续完成。已生成的响应可能仍在传输。

网址就是密钥。每个 Agent 创建一个，不再使用时点 **作废**。持有网址可查询对话、摘要和修改待办，不能用它登录网页或调用其他 HTTP API。服务器不记录凭据路径；反向代理的访问日志会包含完整网址，应关闭该路径的访问日志或脱敏，并避免把网址放进聊天、截图或共享配置。

### Grok Bot

需要付费 Cursor 个人 / Teams、与 Cursor 绑定的 SuperGrok / Plus / Heavy，或 self-serve Grok Business 席位，见 [Grok Bot 入门](https://docs.x.ai/grok-bot/get-started)。

打开侧栏 **Connect apps**（手机：左上角头像 → Connect apps），进入自定义连接器表单，**名称** 填 AllDayRec，**服务器 URL** 填完整的 AI 连接网址。保存后在 Installed 确认，再让 Bot 用 `todos` 查询待办。[官方连接入口](https://cursor.com/help/grok-bot/connect-apps)说明通用入口；自定义表单的两字段依据当前界面，菜单名称以账号实际显示为准。

### ChatGPT / Dots

网页端打开 **ChatGPT Plugins → + → Add custom MCP server**，名称填 AllDayRec，Connection 的 Server URL 填完整连接网址，认证选 **No authentication**，确认风险提示后 **Create as a plugin**，安装并启用。[官方步骤](https://developers.openai.com/api/docs/guides/custom-mcp-server)现使用 Plugins 名称；原开发者模式 / 自定义连接器入口以账号当前界面为准，工作区权限可能限制添加。

Dots 分批面向 Pro 100/200/500（18 岁以上，EEA、英国、瑞士以外）、Business Premium、Enterprise 开放；Enterprise 默认关闭，需管理员启用，见 [Dots 资格](https://learn.chatgpt.com/docs/dots#access)。Dots 使用账号已安装且支持的插件，见 [连接应用](https://learn.chatgpt.com/docs/dots/computers-and-apps)。在聊天中选用插件并要求查询 `todos`，确认出现对应日期的结果。云端账号的实际接入仍需在你的账号完成此验收。
