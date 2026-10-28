#!/usr/bin/env python3
"""Select affected firmware families without adding another Actions dependency."""
import json
import os
import subprocess
import sys
from pathlib import Path

FORMAL_VARIANTS = ["sdmod", "promicro", "promicro-p2", "promicro-p3"]
FAMILIES = ["devkit", "selftest", "blerelay"]


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    tag = os.environ["GITHUB_EVENT_NAME"] == "push" and os.environ["GITHUB_REF"].startswith("refs/tags/firmware-")
    variants = FORMAL_VARIANTS
    if tag:
        subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], check=True)
        families = [f for f in FAMILIES if f != "blerelay"]  # the formal relay is built on the maintainer's machine: its key stays out of CI
    elif os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        target = event.get("inputs", {}).get("target", "all")
        variant = event.get("inputs", {}).get("variant", "all")
        if target not in ["all", *FAMILIES] or variant not in ["all", *FORMAL_VARIANTS, "expansion"]:
            raise SystemExit("Unknown firmware target/variant")
        families = FAMILIES if target == "all" else [target]
        variants = FORMAL_VARIANTS if variant == "all" else [variant]
    else:
        # Compare with this branch's last successful push build, not the push's `before`: a newer push cancels the
        # run before it, and what that run would have built must not be skipped for good.
        base = os.environ.get("LAST_SUCCESS_SHA", "")
        if not base or subprocess.run(["git", "cat-file", "-e", base + "^{commit}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            print(f"No last successful build in this history ({base or 'none'}): building every family", file=sys.stderr)
            families = FAMILIES
        else:
            paths = git("diff", "--name-only", base, "HEAD").splitlines()
            shared = any(p in (".github/workflows/firmware-build.yml", ".github/scripts/test_firmware_select.py") or p.startswith(".github/scripts/firmware-") for p in paths)
            families = FAMILIES if shared else [f for f in FAMILIES if any(p.startswith(f"firmware/{f}/") for p in paths)]
    include = []
    for family in families:
        for variant in variants if family == "devkit" else [family]:
            include.append({"family": family, "variant": variant, "sdk": "idf" if family == "blerelay" else "ncs"})
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("matrix=" + json.dumps({"include": include}, separators=(",", ":")) + "\n")
        output.write(f"selected={'true' if include else 'false'}\n")


if __name__ == "__main__":
    main()
