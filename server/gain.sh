#!/bin/bash
# Each device's calibration offset (device_gain, server/alldayrec/gain.py), on the deploy host:
#   server/gain.sh list [<tenant>]                       # every row, and each device's offset and capture gain now
#   server/gain.sh set <tenant> <device> <offset_db> [--since <ISO time or unix ms>] [--capture <dB>] [--note <text>]
#                                                        # from --since on (default 0: all of its audio); the same
#                                                        # device and --since again replaces it. Takes effect at once.
#                                                        # A device's own sensitivity: from 0, so history voice clips
#                                                        # (no clip_start) are calibrated too
# Instances are dir:unit as in deploy.sh (LR_INSTANCES, default /opt/alldayrec:alldayrec), on the host LR_DEPLOY_HOST
# names through LR_DEPLOY_SHELL (root, as for deploy.sh: systemd-run); each instance's tenants.json says which tenants
# are its own. What gain.py prints on stderr is not shown (it may hold the .env's values): a failure says its exit code.
set -euo pipefail
INSTANCES=${LR_INSTANCES:-/opt/alldayrec:alldayrec}

if [ "${1:-}" != remote ]; then  # on this machine
  case ${1:-} in
    list) args=(${2:+--tenant "$2"} list) ;;
    set) [ $# -ge 4 ] || { sed -n 2,6p "$0" >&2; exit 2; }; args=(--tenant "$2" set "${@:3}") ;;
    *) sed -n 2,6p "$0" >&2; exit 2 ;;
  esac
  HOST=${LR_DEPLOY_HOST:?set LR_DEPLOY_HOST=user@host (the comment at the top of deploy.sh)}
  ssh "$HOST" "${LR_DEPLOY_SHELL:-bash -s} -- remote $(printf '%q ' "$INSTANCES" "${args[@]}")" < "$0"
  exit
fi

# on the deploy host (as root, as deploy.sh): remote <dir:unit ...> <gain.py's arguments>
instances=$2; shift 2
# what stopped gain.py, by its type only: its messages may hold the .env's values
py='import runpy, sys
try:
    runpy.run_module("alldayrec.gain", run_name="__main__")
except SystemExit as e:
    if e.code is None or isinstance(e.code, int):
        raise
    sys.exit("  gain.py: SystemExit")
except BaseException as e:
    sys.exit("  gain.py: " + type(e).__name__)'
ok='' failed=''
for i in $instances; do
  d=${i%%:*} u=${i#*:}
  # as its unit runs it: systemd reads the unit's own EnvironmentFile (its quotes, spaces, the last one wins; never read
  # here, never printed, never in process args), in its WorkingDirectory, as its User. A unit it does not know has none
  # of them: gain.py would find another tenants.json, so it does not run
  wd=$(systemctl show -p WorkingDirectory --value "$u") user=$(systemctl show -p User --value "$u") envs=()
  while read -r f; do [ -z "$f" ] || envs+=(-p "EnvironmentFile=${f% (ignore_errors=*}"); done < <(systemctl show -p EnvironmentFiles --value "$u")
  if [ -z "$wd" ] || [ ${#envs[@]} = 0 ]; then
    echo "  stop: $d: $u has no WorkingDirectory or EnvironmentFile" >&2; failed=1; continue
  fi
  # a tenant another instance has: gain.py says so (exit 3); list without a tenant: every instance's
  if systemd-run --pipe --wait --quiet --collect "${envs[@]}" -p "WorkingDirectory=$wd" ${user:+-p "User=$user"} \
    "$d/server/.venv/bin/python" -c "$py" "$@" < /dev/null 2> >(grep -xE '  gain\.py: [A-Za-z_][A-Za-z0-9_]*' >&2); then
    ok=1
  else
    rc=$?
    [ "$rc" = 3 ] || { echo "  stop: $d: gain.py failed (exit $rc)" >&2; failed=1; }
  fi
done
[ -z "$failed" ] || exit 1
[ -n "$ok" ] || { echo "  stop: no instance has that tenant" >&2; exit 1; }
