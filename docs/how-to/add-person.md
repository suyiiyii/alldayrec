---
type: how-to
---

# 如何给另一个人开账号

一台服务器可以给好几个人用。每个人是 `tenants.json` 里的一条（一个租户），有自己的 token、数据库和录音目录，互相看不到。

## Docker

1. 在 `data/tenants.json` 的数组里加一条，`id` 和目录换成新的名字，token 用一个新生成的值（`openssl rand -hex 16`）：

   ```json
   [
     {"id": "main", "token": "<原来的 token>", "data": "/data/main", "recordings": "/data/main/recordings"},
     {"id": "alice", "token": "<新 token>", "data": "/data/alice", "recordings": "/data/alice/recordings"}
   ]
   ```

   `data` 和 `recordings` 是容器里的路径，`/data` 对应宿主机上的 `./data`。

2. 重启服务器：

   ```bash
   docker compose restart
   ```

   日志里的 `tenants:` 一行会列出所有租户，比如 `tenants: main, alice`。

3. 把新 token 交给那个人。他用 `http(s)://<你的服务器>/?token=<新 token>` 打开网页，再按[如何安装并连接手机 App](./install-app.md)连上自己的手机。

## 用 deploy.sh 部署的实例

运行 `server/tenants.sh add <id>`，它给最后一个实例加一个租户，生成 token 和目录。细节见脚本开头的注释。

## 注意

- 服务器只在启动时读 `tenants.json`，改完要重启。
- 每个租户的 `id`、`token`、`data`、`recordings` 都不能和别人重复，否则服务器拒绝启动。
- `tenants.json` 的权限必须是 600（只有自己能读），否则服务器拒绝启动。
- 只有一个租户、且它的 token 为空时才不做认证；有了第二个人，每个人都必须有 token。
- 第一个租户的数据目录还放着服务器给手机下载的 APK（`live-record.apk`）和录音设备的固件包。
