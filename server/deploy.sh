#!/bin/bash
# Put the server (the alldayrec package, the web app built into it) live on the deploy host and check it, on every
# instance LR_INSTANCES names (dir:unit, space separated; default /opt/alldayrec:alldayrec; test before production).
#   server/deploy.sh            # HEAD (a clean origin/main): build, back up, install, restart, check
#   server/deploy.sh check      # check what runs now (read only)
#   server/deploy.sh rollback   # put back what the last deploy replaced (code, web, columns it added), restart, check
# The host: LR_DEPLOY_HOST=user@host (ssh and scp, with the caller's ssh config and agent). LR_DEPLOY_SHELL (default
# `bash -s`) is the remote command this script is piped into; it must run as root (systemctl, the instance dirs):
# `sudo bash -s`, `wsl -u root -e bash -s`, ... scp puts the release in the login's home as lr-deploy.tgz; a remote shell
# whose $HOME is another one is told where: LR_TGZ (`wsl -u root -e env LR_TGZ=/mnt/c/Users/me/lr-deploy.tgz bash -s`).
# An instance: <dir>/server/alldayrec (the package), <dir>/server/.venv, <dir>/.env with DATA_DIR=<dir> (and MODELS_DIR
# when the model files are elsewhere), its unit running <dir>/server/.venv/bin/python -m alldayrec in <dir>/server with
# EnvironmentFile=<dir>/.env. It serves the tenants in <dir>/tenants.json (server/alldayrec/tenant.py; made from its .env
# once by server/tenants.sh), asked as them; a deploy installs nothing anywhere unless the new server reads every
# instance's tenants.json where its .env points (a server without one would start a new one, with a new token).
# Each deploy keeps a backup with a copy of every tenant's live.db in <dir>/backup-deploy-<time>, <dir>/backup-last points
# at it; a deploy that passes its check keeps only its own and the one before. An instance whose unit is disabled is
# left alone: another one serves its tenants (server/tenants.sh merge).
set -euo pipefail
INSTANCES=${LR_INSTANCES:-/opt/alldayrec:alldayrec}

if [ "${1:-}" != remote ]; then  # on this machine
  mode=${1:-deploy}
  case $mode in deploy | check | rollback) ;; *) sed -n 2,10p "$0" >&2; exit 2 ;; esac
  HOST=${LR_DEPLOY_HOST:?set LR_DEPLOY_HOST=user@host (the comment at the top of $0)}
  root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
  if [ "$mode" = deploy ]; then
    git -C "$root" fetch -q origin main
    if [ -n "$(git -C "$root" status --porcelain -- server web)" ] || [ "$(git -C "$root" rev-parse HEAD)" != "$(git -C "$root" rev-parse origin/main)" ]; then
      echo "deploy only a clean checkout of origin/main" >&2; exit 1
    fi
    (cd "$root/web" && pnpm install --frozen-lockfile && pnpm exec vp run build)
    tmp=$(mktemp -d)
    mkdir "$tmp/alldayrec"
    git -C "$root" rev-parse --short HEAD > "$tmp/alldayrec/COMMIT"
    # explicit list, no excludes: bsdtar's * crosses /, an exclude once dropped dist/index.html; no ._ files
    (cd "$root/server" && COPYFILE_DISABLE=1 tar --no-xattrs -czf "$tmp/release.tgz" alldayrec/*.py alldayrec/static/dist pyproject.toml uv.lock -C "$tmp" alldayrec/COMMIT)
    tar -tzf "$tmp/release.tgz" | grep -qx alldayrec/static/dist/index.html || { echo "alldayrec/static/dist/index.html is not in the release" >&2; exit 1; }
    scp -q "$tmp/release.tgz" "$HOST:lr-deploy.tgz"
  fi
  ssh "$HOST" "${LR_DEPLOY_SHELL:-bash -s} -- remote $mode $INSTANCES" < "$0"
  exit
fi

# on the deploy host, as root: remote <mode> <dir:unit>...
mode=$2; shift 2
RUN=$(date +%Y%m%d-%H%M%S)
TGZ=${LR_TGZ:-$HOME/lr-deploy.tgz}  # where scp put it (the top of this file); test_deploy.py's too
UV=$(command -v uv || echo "$HOME/.local/bin/uv")  # ~/.local/bin is not on a non-interactive shell's PATH
set -- $(for i in "$@"; do  # without the instances whose unit is disabled (merged into another one: not started again)
  if [ "$(systemctl is-enabled "${i#*:}" 2> /dev/null)" = enabled ]; then echo "$i"; else echo "  skip ${i%%:*}: ${i#*:} is disabled" >&2; fi
done)

env_of() { grep "^$2=" "$1/.env" | tail -1 | cut -d= -f2-; }  # dir key: the last one wins, as in systemd

tenants() {  # dir: its tenants, an "id data-dir" a line (the tokens stay in the file)
  python3 -c 'import json, sys; [print(t["id"], t["data"]) for t in json.load(open(sys.argv[1]))]' "$1/tenants.json"
}

first() { tenants "$1" | head -1 | cut -d' ' -f1; }  # dir: its first tenant (the pages and the status are asked as it)

get() {  # dir tenant path: GET from the instance on loopback as that tenant (its token through stdin: not in the process list)
  python3 -c 'import json, sys; print("Authorization: Bearer " + next(t["token"] for t in json.load(open(sys.argv[1])) if t["id"] == sys.argv[2]))' \
    "$1/tenants.json" "$2" | curl -sf -m 10 -H @- "http://127.0.0.1:$(env_of "$1" PORT)$3"
}

started() {  # dir unit log-offset (after a restart): answering within 3 min, not restarted since, no errors logged
  local d=$1 u=$2 off=$3 pid
  pid=$(systemctl show -p MainPID --value "$u")
  for _ in $(seq 90); do  # until it answers or dies
    { get "$d" "$(first "$d")" /api/status > /dev/null || [ "$(systemctl show -p MainPID --value "$u")" != "$pid" ]; } && break; sleep 2
  done
  sleep 20  # the pipelines start after the port is up: a failure there shows within seconds
  [ "$(systemctl show -p MainPID --value "$u")" = "$pid" ] || { echo "  FAIL $d: $u restarted while starting"; return 1; }
  if tail -c +$((off + 1)) "$d/data/server.log" | grep -E 'Traceback|task .* ended'; then
    echo "  FAIL $d: errors in data/server.log since the restart"; return 1
  fi
}

check() {  # dir unit: active, running the commit in its COMMIT, serving the pages, each asset index.html names, every tenant's API
  local d=$1 u=$2 dist=$1/server/alldayrec/static/dist want got a p t n=0 bad= id data
  want=$(cat "$d/server/alldayrec/COMMIT" 2> /dev/null || true)
  t=$(first "$d")
  [ "$(systemctl is-active "$u")" = active ] || bad+=" $u-not-active"
  got=$(get "$d" "$t" /api/status | python3 -c 'import json, sys; print(json.load(sys.stdin)["commit"])') || got="(no /api/status)"
  [ "$got" = "$want" ] || bad+=" running:'$got'-not-'$want'"
  for p in / /conversations; do get "$d" "$t" "$p" | cmp -s - "$dist/index.html" || bad+=" $p"; done
  for a in $(grep -oE '(src|href)="/assets/[^"]+"' "$dist/index.html" | sed 's/^[^"]*"//; s/"$//'); do
    get "$d" "$t" "$a" | cmp -s - "$dist$a" || bad+=" $a"; n=$((n + 1))
  done
  [ $n -gt 0 ] || bad+=" no-assets-in-index.html"
  while read -r id data; do
    for p in /api/client-config /api/days /api/conversations; do get "$d" "$id" "$p" > /dev/null || bad+=" $id:$p"; done
  done < <(tenants "$d")
  [ -z "$bad" ] || { echo "  FAIL $d:$bad"; return 1; }
  echo "  ok $d ($u): runs ${want:-(no COMMIT)}; /, /conversations, $n assets, the API of $(tenants "$d" | wc -l) tenant(s)"
}

deploy() {  # dir unit: back up, install the release, restart, check
  local d=$1 u=$2 b off o
  b=$d/backup-deploy-$RUN
  mkdir -p "$b/old" "$b/new"
  tar -xzf "$TGZ" -C "$b/new" --no-same-owner
  # the release's packages (its uv.lock, the [local] extra too) into the instance's venv first: added or changed, none
  # removed (--inexact), so the code running now and a rollback's run on them too; not the project (the unit runs the
  # package from server/); a failure stops here, nothing installed, nothing restarted
  (cd "$b/new" && UV_PROJECT_ENVIRONMENT="$d/server/.venv" "$UV" sync --frozen --inexact --no-install-project --extra local -q)
  # and every model file the release pins (infer.MODELS) where the instance's .env puts them (MODELS_DIR, else
  # DATA_DIR/models), or stop here the same way (a restart without them would download them first)
  (cd "$b/new" && DATA_DIR="$(env_of "$d" DATA_DIR)" MODELS_DIR="$(env_of "$d" MODELS_DIR)" "$d/server/.venv/bin/python" -c "from alldayrec import infer; infer.check()")
  for f in pyproject.toml uv.lock; do [ ! -f "$d/server/$f" ] || cp -a "$d/server/$f" "$b/old/"; done
  # per tenant, the columns now (rollback drops those a release adds: old code inserts whole rows) and an online copy of the db
  while read -r id data; do
    mkdir -p "$b/old/db/$id"
    "$d/server/.venv/bin/python" - "$data/live.db" "$b/old/db/$id" << 'PY'
import json, sqlite3, sys
c = sqlite3.connect(sys.argv[1])
tables = [t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
json.dump({t: [r[1] for r in c.execute(f"PRAGMA table_info({t})")] for t in tables}, open(f"{sys.argv[2]}/columns.json", "w"))
c.backup(sqlite3.connect(f"{sys.argv[2]}/live.db"))
PY
  done < <(tenants "$d")
  INSTALLED+=" $d"
  ln -sfn "$b" "$d/backup-last"  # once the backup is whole; rollback goes by it and by the package's COMMIT
  # the package (code, web app, COMMIT) swapped whole, the old one into the backup: copied beside it, then two renames
  rm -rf "$d/server/alldayrec.new"
  cp -a "$b/new/alldayrec" "$d/server/alldayrec.new"
  [ ! -d "$d/server/alldayrec" ] || mv "$d/server/alldayrec" "$b/old/alldayrec"
  mv "$d/server/alldayrec.new" "$d/server/alldayrec"
  cp "$b/new/pyproject.toml" "$b/new/uv.lock" "$d/server/"
  off=$(stat -c %s "$d/data/server.log")
  systemctl restart "$u"
  echo "  $d: $(cat "$b/old/alldayrec/COMMIT" 2> /dev/null || echo '(no COMMIT)') -> $(cat "$b/new/alldayrec/COMMIT"), backup $b"
  started "$d" "$u" "$off" && check "$d" "$u" || return 1
  for o in $(printf '%s\n' "$d"/backup-deploy-* | head -n -2); do rm -rf -- "$o"; echo "  removed ${o##*/}"; done  # time-named: sorted
}

rollback() {  # dir unit: undo the newest deploy run, where it got to installing and is still what is installed
  local d=$1 u=$2 b off
  b=$(readlink "$d/backup-last" || true)
  if [ "${b##*/}" != "$NEWEST" ] || ! cmp -s "$b/new/alldayrec/COMMIT" "$d/server/alldayrec/COMMIT"; then
    echo "  skip $d: not on the release of ${NEWEST:-any deploy}"; return 0
  fi
  if [ ! -d "$b/old/alldayrec" ]; then  # the first deploy of the package (a new instance, or one that ran server/*.py)
    echo "  stop $d: that deploy replaced no alldayrec package, there is none to put back"; return 1
  fi
  off=$(stat -c %s "$d/data/server.log")
  systemctl stop "$u"
  while read -r id data; do
    "$d/server/.venv/bin/python" - "$data/live.db" "$b/old/db/$id/columns.json" << 'PY'
import json, sqlite3, sys
c, before = sqlite3.connect(sys.argv[1]), json.load(open(sys.argv[2]))
for t, cols in before.items():
    for col in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
        if col not in cols:
            print(f"  drop column {t}.{col}")
            c.execute(f"ALTER TABLE {t} DROP COLUMN {col}")
PY
  done < <(tenants "$d")
  mv "$d/server/alldayrec" "$b/rolled-back-alldayrec"
  cp -a "$b/old/alldayrec" "$d/server/alldayrec"
  for f in pyproject.toml uv.lock; do [ ! -f "$b/old/$f" ] || cp -a "$b/old/$f" "$d/server/"; done  # the venv keeps what came
  systemctl start "$u"
  echo "  $d: back to $(cat "$b/old/alldayrec/COMMIT" 2> /dev/null || echo '(no COMMIT)') from $b"
  started "$d" "$u" "$off" && check "$d" "$u"
}

# deploy and rollback stop at the first failure (called under || a function would run on past one); check goes on
stopped() {  # on exit: what a failed deploy left
  [ "$1" != 0 ] && [ "$mode" = deploy ] || return 0
  if [ -n "${INSTALLED:-}" ]; then echo "stopped; installed on$INSTALLED, undo with: server/deploy.sh rollback"; else echo "stopped; nothing installed"; fi
}
trap 'stopped $?' EXIT
if [ "$mode" = deploy ]; then  # every instance's tenants.json as its new server finds and reads it: the release's tenant.py, then load()
  for i in "$@"; do
    { tar -xOzf "$TGZ" alldayrec/tenant.py; echo 'load()'; } | DATA_DIR="$(env_of "${i%%:*}" DATA_DIR)" "${i%%:*}/server/.venv/bin/python" - ||
      { echo "  stop: the new server cannot read ${i%%:*}/tenants.json (DATA_DIR=${i%%:*} in its .env; server/tenants.sh migrate first)"; exit 1; }
  done
fi
NEWEST=$(for i in "$@"; do readlink "${i%%:*}/backup-last" || true; done | sed 's|.*/||' | sort | tail -1)
rc=0
for i in "$@"; do
  if [ "$mode" = check ]; then check "${i%%:*}" "${i#*:}" || rc=1; else "$mode" "${i%%:*}" "${i#*:}"; fi
done
exit $rc
