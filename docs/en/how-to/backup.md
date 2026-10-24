---
type: how-to
---

# How to back up and restore

Everything you need to back up is in one place: `./data` for a Docker deployment; without Docker, the `tenants.json` in `DATA_DIR` plus each tenant's `data` and `recordings` directories (the tenant `main` a first start makes is in `DATA_DIR` too). What they hold is in [Data layout](../reference/data.md).

You can skip the model files (`data/models`): the server downloads missing ones again at startup.

## Back up with the server stopped (simplest)

```bash
docker compose stop
tar czf alldayrec-backup-$(date +%Y%m%d).tar.gz data
docker compose start
```

## Back up the database without stopping

Recording files are only appended to, so you can copy them as they are. The database `live.db` is written to while the server runs, so copy it with SQLite's backup API. This command, run in the container, copies the database of the quickstart tenant `main` to `data/main/live-backup.db`:

```bash
docker compose exec alldayrec python -c "import sqlite3; sqlite3.connect('/data/main/live.db').backup(sqlite3.connect('/data/main/live-backup.db'))"
```

Do this for every tenant, with the path changed to its `data` directory in `tenants.json`.

## Restore

1. Stop the server:

   ```bash
   docker compose stop
   ```

2. Put the backed-up `data` directory (or one tenant's `live.db` from it) back in place.
3. Start it:

   ```bash
   docker compose start
   ```
