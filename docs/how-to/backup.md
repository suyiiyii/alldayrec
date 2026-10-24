---
type: how-to
---

# 如何备份与恢复

需要备份的东西都在一个目录里：Docker 部署是 `./data`，不用 Docker 时是 `tenants.json` 加上每个租户的 `data` 和 `recordings` 目录。里面有什么见[数据目录](../reference/data.md)。

模型文件（`data/models`）可以不备份，缺了服务器启动时会重新下载。

## 停机备份（最简单）

```bash
docker compose stop
tar czf alldayrec-backup-$(date +%Y%m%d).tar.gz data
docker compose start
```

## 不停机备份数据库

录音文件只会追加，可以直接复制；数据库 `live.db` 在运行中会被写，要用 SQLite 的备份接口复制。下面的命令在容器里把快速开始的租户 `main` 的数据库复制成 `data/main/live-backup.db`：

```bash
docker compose exec alldayrec python -c "import sqlite3; sqlite3.connect('/data/main/live.db').backup(sqlite3.connect('/data/main/live-backup.db'))"
```

每个租户都要做一遍，路径换成它在 `tenants.json` 里的 `data` 目录。

## 恢复

1. 停掉服务器：

   ```bash
   docker compose stop
   ```

2. 把备份的 `data` 目录（或其中某个租户的 `live.db`）放回原处。
3. 启动：

   ```bash
   docker compose start
   ```
