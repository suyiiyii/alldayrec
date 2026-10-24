---
type: how-to
---

# How to deploy with deploy.sh (maintainers)

Besides Docker, the server can run as systemd units on a Linux host, deployed over ssh from a clean checkout of `origin/main`. This page is for people who maintain one or a few instances themselves.

## Prerequisites

- Your machine: a clean checkout of the repository with `HEAD` equal to `origin/main`; pnpm (the script builds the web app).
- The target host: you can log in over ssh, and the remote shell can run as root (the script works on systemd and the instance directories).
- Each instance is a directory with its `.env` and `tenants.json`. Its `.env` sets `DATA_DIR=<that directory>` (and `MODELS_DIR` when the model files are elsewhere); its systemd unit runs `<dir>/server/.venv/bin/python -m alldayrec` with the working directory `<dir>/server` and `EnvironmentFile=<dir>/.env`. Before installing anything, the script checks that the new server finds and reads every instance's `tenants.json` the way its `.env` says, and installs nothing when one fails.

## Set the environment variables

```bash
export LR_DEPLOY_HOST=user@host                  # required: the ssh / scp target (your ssh config and agent)
export LR_DEPLOY_SHELL='sudo bash -s'            # default `bash -s`: the remote shell the script is piped into, as root
export LR_INSTANCES='/opt/alldayrec:alldayrec'   # the default: <dir>:<systemd unit> pairs, space separated, test first
```

scp puts the release and the APK in the login user's home directory on the host. When the remote shell's `$HOME` is another directory, `LR_TGZ` and `LR_APK` tell the script their paths there.

## Deploy, check, roll back

```bash
server/deploy.sh            # build the web app, back up, install (uv sync), check the model files, restart and check each instance
server/deploy.sh check      # only read what is running
server/deploy.sh rollback   # put back the code and web app the last deploy replaced, drop the database columns it added, restart and check
```

Each deploy leaves a backup in `<instance-dir>/backup-deploy-<time>`, with a copy of every tenant's `live.db`, and `<instance-dir>/backup-last` points to it. A deploy that passes its checks keeps only its own backup and the one before.

## Other scripts

```bash
server/apk_release.sh app/build/app/outputs/flutter-apk/app-release.apk   # the APK phones are offered at /app.apk
server/tenants.sh add <id>                                                 # a new tenant (its own token and dirs) of the last instance
```

The details are in the comments at the top of each script: [`server/deploy.sh`](https://github.com/suyiiyii/alldayrec/blob/main/server/deploy.sh), [`server/apk_release.sh`](https://github.com/suyiiyii/alldayrec/blob/main/server/apk_release.sh), [`server/tenants.sh`](https://github.com/suyiiyii/alldayrec/blob/main/server/tenants.sh).
