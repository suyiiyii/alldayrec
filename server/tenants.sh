#!/bin/bash
# Each instance's tenants.json (who a server serves, server/alldayrec/tenant.py), made once from its .env around the first
# deploy of a server that reads it. In order:
#   server/tenants.sh migrate    # before that deploy: <dir>/tenants.json (600) with the instance's own tenant, from its
#                                # .env: id LR_INSTANCE (else friend or main by its LIVE_DATA path, as the metrics had it),
#                                # token AUTH_TOKEN (copied, never printed), data LIVE_DATA, recordings RECORDINGS_DIR. The
#                                # .env is kept as .env.pre-tenants. Nothing restarts: the server now running ignores the file.
#   server/deploy.sh             # the server that reads tenants.json
#   server/tenants.sh cleanup    # once it checks out: AUTH_TOKEN and LR_INSTANCE out of .env (tenants.json has them)
# Back: server/tenants.sh rollback (the .env as it was, AUTH_TOKEN with it), then server/deploy.sh rollback. In that
# order: the server before tenants.json takes its token from .env and runs without auth when there is none (deploy.sh
# refuses to go back to it then). tenants.json stays: that server does not read it.
# One process for all instances (multi-tenant, one SQLite per tenant): the last one serves the others' tenants beside its own:
#   server/tenants.sh merge      # the others' tenants into the last one's tenants.json, their units stopped and disabled
#                                # (deploy.sh leaves them alone then), it restarted; every instance on one commit first
#   server/tenants.sh unmerge    # back: their tenants out of it, it restarted, their units enabled and started
#   server/tenants.sh add <id>   # a new tenant of the last instance: <its parent dir>/lr-<id>/{data,recordings}, a token,
#                                # it restarted; prints the path that logs them in (the only place the token is shown),
#                                # to go after the instance's public URL
# Each change keeps the tenants.json it replaced beside it (tenants.json.<mode>-<time>). Instances are dir:unit as in
# deploy.sh (LR_INSTANCES, default /opt/alldayrec:alldayrec), on the host LR_DEPLOY_HOST names through LR_DEPLOY_SHELL.
set -euo pipefail
INSTANCES=${LR_INSTANCES:-/opt/alldayrec:alldayrec}

if [ "${1:-}" != remote ]; then  # on this machine
  mode=${1:-} arg=${2:--}
  case $mode in migrate | cleanup | rollback | merge | unmerge) ;; add) [[ $arg =~ ^[a-z][a-z0-9-]{0,31}$ ]] || mode= ;; *) mode= ;; esac
  [ -n "$mode" ] || { sed -n 2,21p "$0" >&2; exit 2; }
  HOST=${LR_DEPLOY_HOST:?set LR_DEPLOY_HOST=user@host (the comment at the top of deploy.sh)}
  ssh "$HOST" "${LR_DEPLOY_SHELL:-bash -s} -- remote $mode $arg $INSTANCES" < "$0"
  exit
fi

# on the deploy host, as root: remote <mode> <arg or -> <dir:unit>...
mode=$2 arg=$3; shift 3

changed() {  # mode file arg...: file's tenants as mode changes them, in tenants.json.new beside it (600, a new file)
  python3 - "$@" << 'PY'
import json, os, secrets, sys
mode, f, args = sys.argv[1], sys.argv[2], sys.argv[3:]
ts = json.load(open(f))
if mode == "merge":  # args: the other instances' dirs, their tenants added
    for d in args:
        ts += [x for x in json.load(open(f"{d}/tenants.json")) if x["id"] not in {y["id"] for y in ts}]
elif mode == "unmerge":  # ... taken out
    out = {x["id"] for d in args for x in json.load(open(f"{d}/tenants.json"))}
    ts = [x for x in ts if x["id"] not in out]
else:  # add <id> <dir>: a new one (its dirs: once the server takes the file)
    i, d = args
    if any(x["id"] == i for x in ts) or os.path.exists(d):
        sys.exit(f"  stop: {i} is a tenant already, or {d} is there")
    ts.append({"id": i, "token": secrets.token_urlsafe(24), "data": f"{d}/data", "recordings": f"{d}/recordings"})
fd = os.open(f + ".new", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, (json.dumps(ts, indent=1) + "\n").encode())
os.close(fd)
print(f"  {f}.new: " + ", ".join(x["id"] for x in ts))
PY
  (cd "$(dirname "$2")/server" && .venv/bin/python -c 'import sys; from pathlib import Path; from alldayrec import tenant; tenant.load(Path(sys.argv[1]))' "$2.new") ||
    { echo "  stop: the server would not start with $2.new (look, then remove it); nothing changed"; exit 1; }
}

answers() {  # dir: within 3 min every tenant of its tenants.json is answered as itself (its token) on its port
  python3 - "$1" "$(grep '^PORT=' "$1/.env" | tail -1 | cut -d= -f2-)" << 'PY'
import json, sys, time, urllib.request
d, port, web = sys.argv[1], sys.argv[2], urllib.request.build_opener(urllib.request.ProxyHandler({}))
ts = json.load(open(f"{d}/tenants.json"))
left = list(ts)
for _ in range(90):
    for t in list(left):
        try:
            web.open(urllib.request.Request(f"http://127.0.0.1:{port}/api/status", headers={"Authorization": f"Bearer {t['token']}"}), timeout=5)
            left.remove(t)
        except OSError:
            pass
    if not left:
        break
    time.sleep(2)
print(f"  {d}: answers as " + ", ".join(t["id"] for t in ts) if not left else f"  {d}: no answer as " + ", ".join(t["id"] for t in left))
sys.exit(1 if left else 0)
PY
}

put() {  # dir unit: its tenants.json.new in place (the one before kept), the unit restarted, every tenant answered
  # (chained: set -e does not stop a function called under ||)
  cp -p "$1/tenants.json" "$1/tenants.json.$mode-$(date +%Y%m%d-%H%M%S)" && mv "$1/tenants.json.new" "$1/tenants.json" &&
    systemctl restart "$2" && answers "$1"
}

case $mode in merge | unmerge | add)
  last=${!#} && t=${last%%:*} tu=${last#*:}
  set -- "${@:1:$#-1}"  # the others
  [ $# -gt 0 ] || [ "$mode" = add ] || { echo "  stop: $mode needs the others before the one that stays (LR_INSTANCES)"; exit 1; }
  case $mode in
    merge)
      for i in "$@"; do
        d=${i%%:*}
        [ -e "$d/tenants.json" ] || { echo "  stop: $d has no tenants.json (server/tenants.sh migrate first)"; exit 1; }
        [ "$(cat "$d/server/alldayrec/COMMIT")" = "$(cat "$t/server/alldayrec/COMMIT")" ] ||
          { echo "  stop: $d runs $(cat "$d/server/alldayrec/COMMIT"), $t $(cat "$t/server/alldayrec/COMMIT"): server/deploy.sh one commit to all first"; exit 1; }
      done
      changed merge "$t/tenants.json" "${@%%:*}"
      for i in "$@"; do  # before the restart: never two processes on one store
        systemctl disable --now "${i#*:}" || { echo "  stop: ${i#*:} did not stop; $t/tenants.json unchanged, $t serves its own" \
          "tenants only. To finish: remove $t/tenants.json.new, then server/tenants.sh unmerge (starts again those stopped)"; exit 1; }
        echo "  ${i%%:*}: ${i#*:} stopped and disabled"
      done
      put "$t" "$tu" || { echo "  back: server/tenants.sh unmerge"; exit 1; }
      ;;
    unmerge)
      changed unmerge "$t/tenants.json" "${@%%:*}"
      put "$t" "$tu"
      for i in "$@"; do
        d=${i%%:*}
        systemctl enable --now "${i#*:}" || { echo "  stop: ${i#*:} did not start: its tenants are served by nobody now;" \
          "systemctl enable --now ${i#*:} once it is fixed, or server/tenants.sh merge to serve them from $t again"; exit 1; }
        echo "  $d: ${i#*:} enabled and started"
        answers "$d"
        [ "$(cat "$d/server/alldayrec/COMMIT")" = "$(cat "$t/server/alldayrec/COMMIT")" ] ||
          echo "  $d runs $(cat "$d/server/alldayrec/COMMIT"), $t $(cat "$t/server/alldayrec/COMMIT"): LR_INSTANCES=$i server/deploy.sh"
      done
      ;;
    add)
      n=$(dirname "$t")/lr-$arg
      changed add "$t/tenants.json" "$arg" "$n"
      install -d -m 700 "$n" "$n/data" "$n/recordings"
      put "$t" "$tu"
      python3 - "$t/tenants.json" "$arg" << 'PY'
import json, sys
t = next(x for x in json.load(open(sys.argv[1])) if x["id"] == sys.argv[2])
print(f"  {t['id']}: data {t['data']}, recordings {t['recordings']}; their link, to them only (it logs in as them; on a")
print(f"  computer it shows the code their app scans): <the instance's public URL>/connect?token={t['token']}")
PY
      ;;
  esac
  exit
esac

for i in "$@"; do
  d=${i%%:*}
  case $mode in
    migrate)
      if [ -e "$d/tenants.json" ]; then echo "  skip $d: it has tenants.json"; continue; fi
      [ -e "$d/.env.pre-tenants" ] || cp -p "$d/.env" "$d/.env.pre-tenants"
      python3 - "$d" << 'PY'
import json, os, sys
d = sys.argv[1]
env = {}
for line in open(f"{d}/.env"):  # as systemd reads an EnvironmentFile: the last one wins, quotes around a value go
    k, eq, v = line.strip().partition("=")
    if eq and not k.startswith("#"):
        env[k.strip()] = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v
data = env["LIVE_DATA"]
t = {"id": env.get("LR_INSTANCE") or ("friend" if "friend" in data else "main"), "token": env.get("AUTH_TOKEN", ""),
     "data": data, "recordings": env["RECORDINGS_DIR"]}
fd = os.open(f"{d}/tenants.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, (json.dumps([t], indent=1) + "\n").encode())
os.close(fd)
print(f"  {d}: tenants.json (600): tenant {t['id']}, data {data}, recordings {t['recordings']}, token {'copied' if t['token'] else 'EMPTY'}")
PY
      ;;
    cleanup)
      # only when tenants.json's token is the one in .env (then .env's is a copy, not the only one)
      python3 - "$d" << 'PY'
import json, sys
d = sys.argv[1]
env = [l for l in open(f"{d}/.env") if l.startswith("AUTH_TOKEN=")]
token = env[-1].strip().partition("=")[2].strip("'\"") if env else None
if token is not None and token not in [t["token"] for t in json.load(open(f"{d}/tenants.json"))]:
    sys.exit(f"  stop {d}: tenants.json has not .env's AUTH_TOKEN")
PY
      grep -v -E '^(AUTH_TOKEN|LR_INSTANCE)=' "$d/.env" > "$d/.env.new" || true
      chmod --reference="$d/.env" "$d/.env.new" && mv "$d/.env.new" "$d/.env"
      echo "  $d: AUTH_TOKEN and LR_INSTANCE out of .env (in tenants.json); the server reads it at its next start"
      ;;
    rollback)
      if [ ! -e "$d/.env.pre-tenants" ]; then echo "  skip $d: no .env.pre-tenants"; continue; fi
      cp -p "$d/.env" "$d/.env.tenants-rolled-back" && cp -p "$d/.env.pre-tenants" "$d/.env"
      echo "  $d: .env as before tenants.json (the one replaced kept as .env.tenants-rolled-back); now server/deploy.sh rollback"
      ;;
  esac
done
