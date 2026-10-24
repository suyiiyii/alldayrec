---
type: how-to
---

# 如何升级与回滚

升级前先[备份](./backup.md)。新版本可能给数据库加列；用 Docker 回到旧版本时不会撤掉这些列，旧版本能否正常读新的数据库：待补充。

## Docker：升级

在 `alldayrec` 目录里：

```bash
git pull
docker compose pull
docker compose up -d
```

- `git pull` 取得新的 `docker-compose.yml` 和 `.env.example`。对比一下 `.env.example` 有没有新的必填项。
- `docker compose pull` 拉取新镜像。只运行 `docker compose up -d` 不会更新本机已有的同名镜像。
- 用 `docker compose logs -f` 看到 `ready on 0.0.0.0:8770` 就升级完了。

想停在某个版本，按[固定镜像版本](./deploy-docker.md#固定镜像版本)改 `image:` 的标签。

## Docker：回滚

1. 把 `docker-compose.yml` 里 `image:` 的标签改成之前的版本，比如 `ghcr.io/suyiiyii/alldayrec:0.1.0`，或者 main 上某次提交的 `sha-<7 位提交号>`。
2. 运行：

   ```bash
   docker compose up -d
   ```

数据有问题时，按[如何备份与恢复](./backup.md#恢复)换回升级前的备份。

## uvx 或 pip

```bash
uvx --env-file .env alldayrec@latest    # uvx：用 PyPI 上的最新版启动
pip install -U alldayrec                              # pip：升级后重启 alldayrec
```

## 从源码运行

```bash
git pull
(cd web && pnpm install && pnpm build)
cd server && uv sync
```

然后重启服务器进程。

## 用 deploy.sh 部署的实例

`server/deploy.sh` 部署时会备份每个实例的代码和每个租户的 `live.db`；`server/deploy.sh rollback` 把上一次部署替换掉的东西放回去，包括删掉那次部署给数据库加的列。见[如何用 deploy.sh 部署](./deploy-sh.md)。
