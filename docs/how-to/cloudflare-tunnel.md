---
type: how-to
description: "用 Cloudflare Tunnel 给家里的 AllDayRec 服务器提供公网 HTTPS 地址：无域名时试用临时网址，有域名时使用固定网址。"
---

# 如何用 Cloudflare Tunnel 提供公网地址

服务器在家里、没有公网服务器或域名时，可以用 `cloudflared` 把本地 AllDayRec 接到公网 HTTPS 地址，再连接云端 Agent。

## 准备

- 本机的 AllDayRec 已运行，`tenants.json` 中的 token 必须非空。即使服务只监听本机，隧道也会让外部访问它。
- `cloudflared` 与服务器在同一台机器上运行。下文以端口 `8770` 为例；自定义端口时全部替换。
- macOS 用 `brew install cloudflared`；Linux 按 [官方安装说明](https://developers.cloudflare.com/tunnel/downloads/)配置软件源后，用系统包管理器安装。

以下隧道命令已按 Cloudflare 官方文档核对，未在本项目环境实测；会创建公网入口，请在你自己的机器和账号操作。

先检查本地服务：

```bash
curl -fsS http://localhost:8770/api/health
```

应返回 `{"ok":true}`。

## 没有域名：试用临时网址

```bash
cloudflared tunnel --url http://localhost:8770
```

保持进程运行，复制输出中的随机 `https://….trycloudflare.com` 地址。无需 Cloudflare 账号或域名。停止进程后网址失效，**每次重启 cloudflared 都会换网址，Agent 的连接器也必须更新**；适合试用。

这个模式没有可用性保证，最多 200 个并发请求，不支持 SSE；AllDayRec 的 MCP 使用 POST 返回 JSON。云端 Agent 需要直接请求服务，请使用上面的命令，邮箱登录限制无法用于非交互式客户端。依据：[Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)。

## 有 Cloudflare 域名：使用固定网址

需要 Cloudflare 账号，且域名的 DNS 已托管到 Cloudflare。

1. 登录并创建命名隧道：

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create alldayrec
   ```

2. 记下输出的 UUID 和凭据文件路径，写入 `~/.cloudflared/config.yml`，把占位值换成实际值。凭据路径必须是绝对路径：

   ```yaml
   url: http://localhost:8770
   tunnel: <TUNNEL_UUID>
   credentials-file: /absolute/path/to/.cloudflared/<TUNNEL_UUID>.json
   ```

3. 用自己的域名替换示例地址，创建 DNS 记录，再运行：

   ```bash
   cloudflared tunnel route dns alldayrec alldayrec.example.com
   cloudflared tunnel run alldayrec
   ```

   操作依据：[创建命名隧道](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/)、[配置文件](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/)。

4. 确认可访问后，停止前台的 cloudflared，安装后台服务。

   Linux：

   ```bash
   sudo cloudflared --config "$HOME/.cloudflared/config.yml" service install
   sudo systemctl start cloudflared
   sudo systemctl status cloudflared
   ```

   macOS（用户登录时启动，使用 `~/.cloudflared/config.yml`）：

   ```bash
   cloudflared service install
   ```

   依据：[Linux 服务](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/linux/)、[macOS 登录启动](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/macos/)。AllDayRec 本身也需要持续运行。

## 连接 Agent

用公网地址请求 `/api/health`，确认返回 `{"ok":true}`。普通网页或 API 请求仍需 tenant token；不要再加 Agent 无法完成的交互式登录门禁。

在 AllDayRec `/connect` 选择这个 HTTPS 地址，再按 [接入 MCP](./connect-mcp.md#创建-ai-连接网址) 创建 AI 连接网址并填入 Agent。命名隧道的地址可跨重启保留。

连接网址就是密钥；反向代理访问日志会包含完整 URL，应关闭该路径日志或脱敏。Cloudflare 会终止公网 TLS，流量经过它的网络，见 [隐私与数据去向](../explanation/privacy.md)。
