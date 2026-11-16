#!/bin/bash
# Each device's calibration offset (device_gain, server/alldayrec/gain.py), on the deploy host:
#   server/gain.sh list [<tenant>]                       # every row, and each device's offset and capture gain now
#   server/gain.sh set <tenant> <device> <offset_db> [--since <ISO time or unix ms>] [--capture <dB>] [--note <text>]
#                                                        # from --since on (default 0: all of its audio); the same
#                                                        # device and --since again replaces it. Takes effect at once
# Instances are dir:unit as in deploy.sh (LR_INSTANCES, default /opt/alldayrec:alldayrec), on the host LR_DEPLOY_HOST
# names through LR_DEPLOY_SHELL; each instance's tenants.json says which tenants are its own.
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

# on the deploy host: remote <dir:unit ...> <gain.py's arguments>
instances=$2; shift 2
ok=
for i in $instances; do
  d=${i%%:*}
  # a tenant another instance has: gain.py says so (exit 1); list without a tenant: every instance's
  if DATA_DIR=$d "$d/server/.venv/bin/python" -m alldayrec.gain "$@" 2> >(grep -v '^no tenant ' >&2); then ok=1; fi
done
[ -n "$ok" ] || { echo "  stop: no instance has that tenant" >&2; exit 1; }
