---
type: how-to
---

# 如何让手机连上服务器

服务器默认只在本机的 `127.0.0.1:8770` 上监听。手机要上传录音，就得能访问到它。这篇按网络情况分两种做法。

::: warning App 版本
App 从 `apk-v1.5.1` 起能连 `http://` 地址：在 Tailscale 或局域网里直接填 `http://` 地址即可（iOS 自己构建的 App 同样，见[如何安装并连接手机 App](./install-app.md)）。更早的 APK（1.5.0 及之前）只能连 `https://`，请升级，或按下面的[手机要从公网访问](#手机要从公网访问)做。

:::

## 手机和服务器在同一个 Tailscale 网络或局域网里

1. 让服务器在 Tailscale 或局域网地址上可访问。

   - Docker：把 `docker-compose.yml` 里的端口改成在所有网卡上发布，或者只发布在 Tailscale 地址上，然后 `docker compose up -d`：

     ```yaml
     ports:
       - "8770:8770"               # 所有网卡
       # - "100.x.y.z:8770:8770"  # 只在这台机器的 Tailscale 地址上
     ```

   - 不用 Docker：在 `.env` 里设 `HOST=0.0.0.0`，重启服务器。

2. 在电脑浏览器里，用手机将要用的地址打开网页，比如 `http://100.x.y.z:8770/?token=<你的 token>`（Tailscale 地址）或 `http://192.168.1.10:8770/?token=<你的 token>`（局域网地址）。

3. 打开 **连接手机**（`/connect`），用 App 扫码。二维码里的服务器地址就是浏览器地址栏里的地址，所以第 2 步不能用 `127.0.0.1` 打开。

## 手机要从公网访问

1. 在 8770 端口前面放一个带 TLS 的反向代理，比如 Caddy、nginx、Cloudflare Tunnel 或 Tailscale Serve。代理要转发 WebSocket（网页用 `/ws` 接收推送）。
2. 代理的健康检查用 `GET /api/health`：它不需要 token，返回 `{"ok":true}`。
3. 用 `https://<你的域名>/?token=<你的 token>` 打开网页，再到 **连接手机** 扫码。

不要把 token 为空的服务器暴露出去：那样任何人都能读写你的数据。

用 nginx 时，它默认只收 1 MB 以内的请求体（`client_max_body_size`），[导入](./import-recordings.md)较大的录音文件会被拒绝（413），需要调大。
