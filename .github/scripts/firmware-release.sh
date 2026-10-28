#!/usr/bin/env bash
# Release job only: verify origin/main ancestry and publish already-built bits.
# Prerequisites: git, gh, sha256sum. No toolchain and no build command here.
set -euo pipefail
[[ "${GITHUB_REF:-}" == refs/tags/firmware-* ]] || { echo 'A firmware tag push is required' >&2; exit 1; }
[ "${GITHUB_EVENT_NAME:-}" = push ] || { echo 'Manual builds do not release' >&2; exit 1; }
git merge-base --is-ancestor HEAD origin/main
shopt -s nullglob
archives=(firmware-artifacts/*.tar.gz)
# Four formal recorder variants and the separate selftest; the relay is built locally with its key.
[ "${#archives[@]}" -eq 5 ] || { echo 'Release requires all five formal firmware archives' >&2; exit 1; }
for archive in "${archives[@]}"; do
  (cd firmware-artifacts && sha256sum --check "$(basename "$archive").sha256")
done
uv run --no-project --python 3.11 python - <<'PY'
import json, os, pathlib, subprocess, tarfile
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
tag = os.environ["GITHUB_REF_NAME"]
repo = os.environ["GH_REPO"]
ref = json.loads(subprocess.check_output(["gh", "api", f"repos/{repo}/git/ref/tags/{tag}"]))
target = ref["object"]
while target["type"] == "tag":
    target = json.loads(subprocess.check_output(["gh", "api", f"repos/{repo}/git/tags/{target['sha']}"]))["object"]
if target["type"] != "commit" or target["sha"] != commit:
    raise SystemExit("Release tag moved or differs from the built source")
expected = {"LR-X1", "LR-P1", "LR-P2", "LR-P3", "LR-selftest-ProMicro"}
models = set()
for path in pathlib.Path("firmware-artifacts").glob("*.tar.gz"):
    with tarfile.open(path) as archive:
        metadata = [entry for entry in archive.getmembers() if entry.name.endswith("/metadata.json")]
        if len(metadata) != 1:
            raise SystemExit("Firmware archive must contain exactly one metadata record")
        data = json.load(archive.extractfile(metadata[0]))
    if data["source_commit"] != commit or data["model"] in models:
        raise SystemExit("Firmware source mismatch or duplicate model")
    models.add(data["model"])
if models != expected:
    raise SystemExit("Release is missing or includes unexpected firmware models")
PY
assets=(firmware-artifacts/*.tar.gz firmware-artifacts/*.sha256)
# Existing releases deliberately fail instead of mutating a published release on rerun.
gh release create "$GITHUB_REF_NAME" "${assets[@]}" --verify-tag --draft \
  --target "$(git rev-parse HEAD)" --title "$GITHUB_REF_NAME" \
  --notes 'Owner review required before publication. These are the exact CI-built firmware bits; see the per-model metadata and SHA256SUMS inside each archive. No devices were flashed and no production endpoint was contacted.'
