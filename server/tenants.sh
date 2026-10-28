#!/bin/bash
# Each instance's tenants.json (who a server serves, server/alldayrec/tenant.py). One process for all instances
# (multi-tenant, one SQLite per tenant): the last one serves the others' tenants beside its own:
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
  case $mode in merge | unmerge) ;; add) [[ $arg =~ ^[a-z][a-z0-9-]{0,31}$ ]] || mode= ;; *) mode= ;; esac
  [ -n "$mode" ] || { sed -n 2,11p "$0" >&2; exit 2; }
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
        [ -e "$d/tenants.json" ] || { echo "  stop: $d has no tenants.json"; exit 1; }
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
print(f"  {t['id']}: data {t['data']}, recordings {t['recordings']}; their link, to them only (a browser opening it logs in as")
print(f"  them; made into a QR code, the app scans it to sign in): <the instance's public URL>/?token={t['token']}")
PY
      ;;
  esac
  exit
esac
