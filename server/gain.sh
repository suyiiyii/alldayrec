#!/bin/bash
# Each device's calibration offset (device_gain, server/alldayrec/gain.py), on the deploy host:
#   server/gain.sh list [<tenant>]                       # every row, and each device's offset and capture gain now
#   server/gain.sh set <tenant> <device> <offset_db> [--since <ISO time or unix ms>] [--capture <dB>] [--note <text>]
#                                                        # from --since on (default 0: all of its audio); the same
#                                                        # device and --since again replaces it. Takes effect at once.
#                                                        # A device's own sensitivity: from 0, so history voice clips
#                                                        # (no clip_start) are calibrated too
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
ok= failed=
for i in $instances; do
  d=${i%%:*}
  # as its unit runs it: in <dir>/server, with <dir>/.env's variables (EnvironmentFile, the last one wins), read in
  # Python before the package is imported (never sourced, never printed, never in process args)
  # a tenant another instance has: gain.py says so (exit 3); list without a tenant: every instance's
  if (cd "$d/server" && .venv/bin/python -c 'import os, runpy
for line in open("../.env"):
    k, eq, v = line.strip().partition("=")
    if eq and k and not k.startswith(("#", ";")):
        os.environ[k] = v
runpy.run_module("alldayrec.gain", run_name="__main__")' "$@") 2> >(grep -v '^no tenant ' >&2); then
    ok=1
  else
    rc=$?
    [ "$rc" = 3 ] || { echo "  stop: $d: gain.py failed (exit $rc), above" >&2; failed=1; }
  fi
done
[ -z "$failed" ] || exit 1
[ -n "$ok" ] || { echo "  stop: no instance has that tenant" >&2; exit 1; }
