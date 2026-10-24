---
type: how-to
---

# 如何用 deploy.sh 部署（维护者）

除了 Docker，服务器也可以作为 systemd 单元跑在 Linux 主机上，从 `origin/main` 的干净检出通过 ssh 部署。这篇适合自己维护一台或几台实例的人。

## 前提

- 本机：仓库的干净检出，`HEAD` 等于 `origin/main`；pnpm（脚本会构建网页）。
- 目标主机：能用 ssh 登录，远程 shell 能以 root 运行（脚本要操作 systemd 和实例目录）。
- 每个实例是一个目录，里面有它的 `.env` 和 `tenants.json`，由对应的 systemd 单元运行。

## 设置环境变量

```bash
export LR_DEPLOY_HOST=user@host                  # 必填：ssh / scp 的目标（用你自己的 ssh 配置和 agent）
export LR_DEPLOY_SHELL='sudo bash -s'            # 默认 `bash -s`：脚本通过管道送进去、以 root 运行的远程 shell
export LR_INSTANCES='/opt/alldayrec:alldayrec'   # 默认值：<目录>:<systemd 单元>，空格分隔，测试实例在前
```

scp 会把发布包和 APK 放到主机上登录用户的 home 目录。远程 shell 的 `$HOME` 是另一个目录时，用 `LR_TGZ` 和 `LR_APK` 告诉脚本它们在那里的路径。

## 部署、检查、回滚

```bash
server/deploy.sh            # 构建网页、备份、安装（uv sync）、检查模型文件、逐个重启实例并检查
server/deploy.sh check      # 只读取正在运行的状态
server/deploy.sh rollback   # 把上一次部署替换掉的代码、网页和它加的数据库列放回去，重启并检查
```

每次部署在 `<实例目录>/backup-deploy-<时间>` 留一份备份，里面有每个租户 `live.db` 的副本，`<实例目录>/backup-last` 指向它。通过检查的部署只保留自己和上一次的备份。

## 其他脚本

```bash
server/apk_release.sh app/build/app/outputs/flutter-apk/app-release.apk   # 发布手机从 /app.apk 下载的 APK
server/tenants.sh add <id>                                                 # 给最后一个实例加一个新租户（自己的 token 和目录）
```

细节见每个脚本开头的注释：[`server/deploy.sh`](https://github.com/suyiiyii/alldayrec/blob/main/server/deploy.sh)、[`server/apk_release.sh`](https://github.com/suyiiyii/alldayrec/blob/main/server/apk_release.sh)、[`server/tenants.sh`](https://github.com/suyiiyii/alldayrec/blob/main/server/tenants.sh)。
