---
type: how-to
---

# How to upgrade and roll back

[Back up](./backup.md) before you upgrade. A new version may add columns to the database; going back to an old version with Docker doesn't remove them. To be added: whether the old version reads the newer database correctly.

## Docker: upgrade

In the `alldayrec` directory:

```bash
git pull
docker compose pull
docker compose up -d
```

- `git pull` gets the new `docker-compose.yml` and `.env.example`. Compare `.env.example` for new required settings.
- `docker compose pull` pulls the new image. `docker compose up -d` alone doesn't update an image of the same name that is already on the machine.
- The upgrade is done when `docker compose logs -f` shows `ready on 0.0.0.0:8770`.

To stay on one version, change the tag of `image:` as in [Pin an image version](./deploy-docker.md#pin-an-image-version).

## Docker: roll back

1. Change the tag of `image:` in `docker-compose.yml` to the earlier version, for example `ghcr.io/suyiiyii/alldayrec:0.1.0`, or `sha-<7-char commit>` for a commit on main.
2. Run:

   ```bash
   docker compose up -d
   ```

If the data has problems, put back the backup from before the upgrade, as in [Back up and restore](./backup.md#restore).

## uvx or pip

```bash
uvx --env-file .env alldayrec@latest    # uvx: start the newest version on PyPI
pip install -U alldayrec                              # pip: then restart alldayrec
```

## From source

```bash
git pull
(cd web && pnpm install && pnpm build)
cd server && uv sync
```

Then restart the server process.

## Instances deployed with deploy.sh

`server/deploy.sh` backs up each instance's code and every tenant's `live.db` when it deploys. `server/deploy.sh rollback` puts back what the last deploy replaced, and drops the database columns that deploy added. See [Deploy with deploy.sh (maintainers)](./deploy-sh.md).
