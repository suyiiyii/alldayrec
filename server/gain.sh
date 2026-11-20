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
# are its own. Of what gain.py prints on stderr only its own messages are shown (the rest may hold the .env's values):
# a failure says its exit code.
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
# stderr is closed to everything but gain.py's own messages (err: fixed text and the command line, as main says) and,
# of anything else that stopped it, its type: the rest may hold the .env's values. No DATA_DIR from the unit's
# EnvironmentFiles (an optional one missing): it does not run, it would take the default dir's tenants.json
py='import io, os, sys
shown, said, rc = os.fdopen(os.dup(2), "w"), io.StringIO(), 1
os.dup2(os.open(os.devnull, os.O_WRONLY), 2)
try:
    if os.environ.get("DATA_DIR"):
        from alldayrec import gain
        gain.main([os.fsdecode(a) for a in sys.stdin.buffer.read().split(b"\0")[:-1]], err=said)
        rc = 0
    else:
        said.write("no DATA_DIR from the unit EnvironmentFiles\n")
except SystemExit as e:
    if e.code is None or isinstance(e.code, int):
        rc = e.code or 0
    else:
        said.write("SystemExit\n")
except BaseException as e:
    said.write(type(e).__name__ + "\n")
for line in said.getvalue().splitlines():
    print("  gain.py:", line, file=shown)
sys.exit(rc)'
ok='' failed=''
for i in $instances; do
  d=${i%%:*} u=${i#*:} wd='' user='' envs=() bad=''
  # as its unit runs it: systemd reads the unit's own EnvironmentFiles in order, an optional one (-) still optional
  # (their quotes, spaces, the last one wins; never read here, never printed, never in process args), in its
  # WorkingDirectory, as its User. A unit it does not know has none of them: gain.py would find another tenants.json,
  # so it does not run; nor when systemctl fails or prints what this does not know
  props=$(systemctl show -p WorkingDirectory -p User -p EnvironmentFiles "$u") || bad=1
  while IFS= read -r l; do
    case $l in
      WorkingDirectory=*) wd=${l#*=} ;;
      User=*) user=${l#*=} ;;
      EnvironmentFiles=*' (ignore_errors=yes)') f=${l#*=}; envs+=(-p "EnvironmentFile=-${f% (ignore_errors=yes)}") ;;
      EnvironmentFiles=*' (ignore_errors=no)') f=${l#*=}; envs+=(-p "EnvironmentFile=${f% (ignore_errors=no)}") ;;
      ''|EnvironmentFiles=) ;;
      *) bad=1 ;;
    esac
  done <<< "$props"
  if [ -n "$bad" ]; then
    echo "  stop: $d: systemctl show $u failed or printed what gain.sh does not know" >&2; failed=1; continue
  fi
  if [ -z "$wd" ] || [ ${#envs[@]} = 0 ]; then
    echo "  stop: $d: $u has no WorkingDirectory or EnvironmentFile" >&2; failed=1; continue
  fi
  # gain.py's arguments on stdin (NUL-separated), not in the command: systemd expands ${VAR} there (245 also without
  # --expand-environment), so a ${LLM_API_KEY} in a note would be its value; $py has no $ or % (test_gain_sh). A
  # tenant another instance has: gain.py says so (exit 3); list without a tenant: every instance's. systemd-run's own
  # errors are not shown either (they share the stream): the exit code says it failed
  if systemd-run --pipe --wait --quiet --collect "${envs[@]}" -p "WorkingDirectory=$wd" ${user:+-p "User=$user"} \
    "$d/server/.venv/bin/python" -c "$py" < <(printf '%s\0' "$@") 2> >(grep '^  gain\.py: ' >&2); then
    ok=1
  else
    rc=$?
    [ "$rc" = 3 ] || { echo "  stop: $d: systemd-run or gain.py failed (exit $rc)" >&2; failed=1; }
  fi
done
[ -z "$failed" ] || exit 1
[ -n "$ok" ] || { echo "  stop: no instance has that tenant" >&2; exit 1; }
