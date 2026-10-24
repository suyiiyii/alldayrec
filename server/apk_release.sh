#!/bin/bash
# Put a release APK at an instance's /app.apk: the Android app offers it to every phone on an older build
# (/api/app/latest reads the version from the file). The APK it replaces stays beside it as live-record.apk.prev-<version>.
#   server/apk_release.sh <apk>     # e.g. app/build/app/outputs/flutter-apk/app-release.apk; its build number must be higher
# On every instance LR_INSTANCES names, on the host LR_DEPLOY_HOST names through LR_DEPLOY_SHELL, as deploy.sh (see there);
# each one's APK is its first tenant's, for all its tenants. scp puts the APK in the login's home as lr-app.apk; a remote
# shell whose $HOME is another one is told where: LR_APK.
set -euo pipefail
INSTANCES=${LR_INSTANCES:-/opt/alldayrec:alldayrec}

if [ "${1:-}" != remote ]; then  # on this machine
  [ -f "${1:-}" ] || { sed -n 4p "$0" >&2; exit 2; }
  HOST=${LR_DEPLOY_HOST:?set LR_DEPLOY_HOST=user@host (the comment at the top of deploy.sh)}
  new=$(uv run --no-project python "$(dirname "$0")/alldayrec/apk.py" "$1")
  echo "$1: $new"
  scp -q "$1" "$HOST:lr-app.apk"
  ssh "$HOST" "${LR_DEPLOY_SHELL:-bash -s} -- remote $new $INSTANCES" < "$0"
  exit
fi

# on the deploy host, as root: remote <versionName+versionCode> <dir:unit>...
new=$2; shift 2
TMP=${LR_APK:-$HOME/lr-app.apk}  # where scp put it (the top of this file)
trap 'rm -f "$TMP"' EXIT
env_of() { grep "^$1=" "$d/.env" | tail -1 | cut -d= -f2-; }
first() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[0][sys.argv[2]])' "$d/tenants.json" "$1"; }  # the first tenant's (its data has the APK)
for i in "$@"; do
  d=${i%%:*} old=
  apk=$(first data)/live-record.apk
  if [ -f "$apk" ]; then
    old=$("$d/server/.venv/bin/python" "$d/server/alldayrec/apk.py" "$apk")
    [ "${old#*+}" -lt "${new#*+}" ] || { echo "$d has $old: $new is not newer, nothing changed there" >&2; exit 1; }
    cp -a "$apk" "$apk.prev-$old"
  fi
  install -m 644 "$TMP" "$apk.new" && mv "$apk.new" "$apk"  # a rename: a download under way goes on reading the old file
  # what the phones now get (the token through stdin: not in the process list)
  got=$(echo "Authorization: Bearer $(first token)" | curl -sf -m 10 -H @- "http://127.0.0.1:$(env_of PORT)/api/app/latest")
  echo "$got" | grep -q "\"versionCode\":${new#*+}[,}]" || { echo "FAIL: $d: /api/app/latest says $got" >&2; exit 1; }
  echo "ok: $d /app.apk is $new${old:+ (was $old, kept as ${apk##*/}.prev-$old)}"
done
