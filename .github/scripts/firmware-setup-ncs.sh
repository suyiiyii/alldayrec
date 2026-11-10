#!/usr/bin/env bash
# Standalone: bash .github/scripts/firmware-setup-ncs.sh
# Prerequisites: Linux x86_64, bash, git, curl, tar/xz, sha256sum, uv, native
# compiler. Nordic's official native v2.7.0 toolchain includes Python,
# cmake/ninja/dtc/gperf and its compiler; SDK installation downloads the west modules.
# Nothing requires Docker, sudo or writes outside the invoking user's directories.
set -euo pipefail
CACHE=${LR_FIRMWARE_CACHE:-${HOME}/.cache/live-record-ci}
mkdir -p "$CACHE/bin"
CACHE=$(realpath "$CACHE")
export PATH="$CACHE/bin:$HOME/.local/bin:$PATH"
for command in git curl tar xz sha256sum uv cc; do
  command -v "$command" >/dev/null || { echo "Required host tool missing: $command" >&2; exit 1; }
done
# Pin the official Linux launcher 1.4.1 by its versioned file name: the unversioned
# .../nrfutil alias moves with each release (1.4.2 on 2026-11-05). The sha256 equals
# the one Nordic's Artifactory storage API lists for this file:
# https://files.nordicsemi.com/artifactory/api/storage/swtools/external/nrfutil/executables/x86_64-unknown-linux-gnu/nrfutil-x86_64-unknown-linux-gnu-1.4.1-f15ff58
NRFUTIL="$CACHE/bin/nrfutil"
BOOTSTRAP_SHA=1cf4bd1d3d64f43a6b9602d8d8e8d647bec0d26d517ba72fd154f95546b50260
if [ ! -f "$NRFUTIL" ]; then
  curl --fail --location --retry 5 --retry-all-errors --retry-delay 5 --output "$NRFUTIL" \
    https://files.nordicsemi.com/artifactory/swtools/external/nrfutil/executables/x86_64-unknown-linux-gnu/nrfutil-x86_64-unknown-linux-gnu-1.4.1-f15ff58
fi
printf '%s  %s\n' "$BOOTSTRAP_SHA" "$NRFUTIL" | sha256sum --check
chmod u+x "$NRFUTIL"
INSTALL="$CACHE/nordic"
if [ ! -f "$CACHE/ncs-v2.7.0.ready" ]; then
  nrfutil install sdk-manager=1.16.1
  # Installs both the native toolchain and the actual complete NCS SDK workspace.
  # Merely unpacking the toolchain is not sufficient to build an application.
  nrfutil sdk-manager install v2.7.0 --install-dir "$INSTALL"
  # Ask west inside the installed environment, instead of assuming sdk-manager's
  # workspace layout. There must be exactly one pinned sdk-nrf checkout.
  mapfile -t MANIFESTS < <(find "$INSTALL" -path '*/nrf/west.yml' -type f)
  [ "${#MANIFESTS[@]}" -eq 1 ] || { echo 'Expected one NCS west manifest in installed SDK' >&2; exit 1; }
  NCS=$(dirname "$(dirname "${MANIFESTS[0]}")")
  [ "$(git -C "$NCS/nrf" rev-parse HEAD)" = 5cb85570ca43bc344d3145c2338128eacf4b48ec ] || {
    echo 'Installed NCS SDK is not the pinned v2.7.0 commit' >&2; exit 1;
  }
  printf '%s\n' "$NCS" > "$CACHE/ncs-v2.7.0.workspace"
  # SDK archives omit inactive modules; freeze would incorrectly require them.
  # Preserve the resolved manifest plus exact SHAs of all active installed modules.
  nrfutil sdk-manager toolchain launch --ncs-version v2.7.0 --install-dir "$INSTALL" \
    --chdir "$NCS" -- west manifest --resolve > "$CACHE/ncs-v2.7.0.manifest.yml"
  nrfutil sdk-manager toolchain launch --ncs-version v2.7.0 --install-dir "$INSTALL" \
    --chdir "$NCS" -- west list -f '{name} {sha} {path}' > "$CACHE/ncs-v2.7.0.projects.txt"
  touch "$CACHE/ncs-v2.7.0.ready"
fi
NCS=$(cat "$CACHE/ncs-v2.7.0.workspace")
[ "$(git -C "$NCS/nrf" rev-parse HEAD)" = 5cb85570ca43bc344d3145c2338128eacf4b48ec ] || {
  echo 'Cached NCS SDK commit changed; use a new LR_FIRMWARE_CACHE' >&2; exit 1;
}
ENV_FILE="$CACHE/ncs-v2.7.0.env"
{
  printf 'export PATH=%q:"$PATH"\n' "$CACHE/bin:$HOME/.local/bin"
  printf 'export NCS=%q\n' "$NCS"
  printf 'export LR_NCS_INSTALL_DIR=%q\n' "$INSTALL"
  printf 'unset ZEPHYR_SDK_INSTALL_DIR\n'
  printf 'export FIRMWARE_TOOLCHAIN_METADATA=%q\n' "$CACHE/ncs-v2.7.0.metadata.json"
  printf 'export FIRMWARE_SDK_MANIFEST=%q\n' "$CACHE/ncs-v2.7.0.manifest.yml"
  printf 'export FIRMWARE_SDK_PROJECTS=%q\n' "$CACHE/ncs-v2.7.0.projects.txt"
} > "$ENV_FILE"
printf '{"sdk":"nRF Connect SDK","version":"v2.7.0","sdk_commit":"5cb85570ca43bc344d3145c2338128eacf4b48ec","toolchain":"official Nordic v2.7.0 via nrfutil sdk-manager 1.16.1","bootstrap_sha256":"%s"}\n' "$BOOTSTRAP_SHA" > "$CACHE/ncs-v2.7.0.metadata.json"
[ -z "${GITHUB_OUTPUT:-}" ] || printf 'env_file=%s\n' "$ENV_FILE" >> "$GITHUB_OUTPUT"
printf 'Environment: source %q\n' "$ENV_FILE"
