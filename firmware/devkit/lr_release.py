"""Build, pack and upload recorder firmware to the server, where the app's firmware page offers it (server/firmware.py).
Each variant is built by lr.sh and its model read from that build (CONFIG_BT_DIS_MODEL), so a package goes up only
under the model it was built for. Only from a clean tree (the version names one commit) on origin/main.
    LR_TOKEN=<AUTH_TOKEN> uv run --no-project python lr_release.py promicro-p3 --server URL
prints each model's draft notes: the subjects of the commits that changed its firmware since its last release. Rewrite
them for the people who own the recorder, then release with them:
    LR_TOKEN=<AUTH_TOKEN> LR_FIRMWARE_TOKEN=<FIRMWARE_TOKEN> \\
    uv run --no-project python lr_release.py promicro-p3 --server URL --notes TEXT [--unmerged]
Notes: Chinese, short, one change a line, what a person notices (开关要拨稳 0.5 秒才算数; 灯的含义改了：…); no issue
numbers, pins, chips, driver or tool names, in-house names. Models released together share --notes, so run once per
model when their last releases differ. --version V replaces the notes of a published version (no build).
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

D = Path(__file__).resolve().parent
MODELS = {"sdmod": "LR-X1", "promicro": "LR-P1", "promicro-p2": "LR-P2", "promicro-p3": "LR-P3"}  # the boards people
# have (expansion is a test base for the XIAO); LR-P2 (kept for tests) and LR-P3 (the boxed recorder) are ProMicros
# too, with packages of their own


def git(*a: str) -> str:
    return subprocess.run(["git", "-C", str(D), *a], check=True, capture_output=True, text=True).stdout.strip()


def api(server: str, path: str, body: bytes | None = None, method: str | None = None, fw_token: str = ""):
    """The server's JSON answer; None for a GET of what it does not have (404)."""
    req = urllib.request.Request(f"{server.rstrip('/')}{path}", body, {
        "Content-Type": "application/zip", "Authorization": f"Bearer {os.environ.get('LR_TOKEN', '')}",
        "X-Firmware-Token": fw_token}, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        if e.code == 404 and body is None:
            return None
        sys.exit(f"{path}: the server said {e.code} {e.read().decode()}")


def draft(server: str, model: str) -> str:
    """The subjects of the commits that changed what goes into the package since the model's last release (all but
    the tools, tests and README), tags and issue numbers dropped: the raw material for --notes."""
    last = api(server, f"/api/firmware/latest?{urllib.parse.urlencode({'model': model})}")
    since = [f"{last['commit']}..HEAD"] if last else ["-20"]
    log = git("log", "--format=%s", *since, "--", ":(top)firmware/devkit", ":(top,exclude)firmware/devkit/*.py",
              ":(top,exclude)firmware/devkit/test", ":(top,exclude)firmware/devkit/README.md")
    head = f"{model}, since {last['version']}" if last else f"{model}, first release (the last 20)"
    tidy = lambda s: re.sub(r"^(\[[^]]*])+ *| *[(（]#\d+(?:[ 、,，]+#\d+)*[)）]", "", s)
    return "\n".join([f"== {head}:", *("- " + tidy(s) for s in log.splitlines())])


def config(v: str, key: str) -> str | None:
    m = re.search(rf'^CONFIG_{key}="(.*)"$', (D / f"build/lr-{v}/zephyr/.config").read_text(), re.M)
    return m and m.group(1)


def main():
    ap = argparse.ArgumentParser(description="build, pack and upload recorder firmware")
    ap.add_argument("variants", nargs="+", choices=MODELS)
    ap.add_argument("--server", required=True, help="e.g. https://live.example.com")
    ap.add_argument("--notes", help="what changed for the people with the recorder, shown in the app; left out: print "
                                    "each model's draft and stop")
    ap.add_argument("--version", help="a published version: replace its notes, nothing built")
    ap.add_argument("--unmerged", action="store_true", help="a commit not on origin/main (a test server)")
    a = ap.parse_args()
    if not a.notes:
        sys.exit("\n\n".join([*(draft(a.server, MODELS[v]) for v in a.variants),
                               "rewrite that for the people with the recorder and pass it as --notes (see the top of lr_release.py)"]))
    fw_token = os.environ.get("LR_FIRMWARE_TOKEN") or sys.exit("LR_FIRMWARE_TOKEN (the server's FIRMWARE_TOKEN) is not set")
    if a.version:
        for v in a.variants:
            meta = api(a.server, f"/api/firmware/{MODELS[v]}/{a.version}?{urllib.parse.urlencode({'notes': a.notes})}",
                       b"", "PATCH", fw_token)
            print(f"notes of {meta['model']} {meta['version']}:\n{meta['notes']}")
        return
    if subprocess.run(["git", "-C", str(D), "diff", "--quiet", "HEAD", "--", "."]).returncode:
        sys.exit("firmware/devkit has uncommitted changes: a version must name one commit")
    commit, short = git("rev-parse", "HEAD"), git("rev-parse", "--short", "HEAD")
    if not a.unmerged:
        git("fetch", "-q", "origin", "main")
        if subprocess.run(["git", "-C", str(D), "merge-base", "--is-ancestor", commit, "origin/main"]).returncode:
            sys.exit(f"{short} is not on origin/main (--unmerged for a test server)")
    env = {k: v for k, v in os.environ.items() if k != "LR_LOG"}  # the log build is not for people's boards
    for v in a.variants:
        subprocess.run([str(D / "lr.sh"), "build", v], check=True, env=env)
        subprocess.run([str(D / "lr.sh"), "pkg", v], check=True, env=env)
        model, version = config(v, "BT_DIS_MODEL"), config(v, "BT_DIS_FW_REV_STR") or ""
        if model != MODELS[v]:
            sys.exit(f"{v} built as model {model}, expected {MODELS[v]}")
        if not version.endswith(f"-{short}"):
            sys.exit(f"{v} build carries revision {version}, not HEAD {short}")
        hexfile, pkg = D / f"build/lr-{v}/zephyr/zephyr.hex", D / f"build/lr-{v}/lr-{v}.zip"
        built = dt.datetime.fromtimestamp(hexfile.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        q = urllib.parse.urlencode({"model": model, "version": version, "commit": commit, "built": built, "notes": a.notes})
        meta = api(a.server, f"/api/firmware?{q}", pkg.read_bytes(), fw_token=fw_token)
        print(f"uploaded {meta['model']} {meta['version']} ({meta['size']} B, sha256 {meta['sha256'][:12]}…)")


if __name__ == "__main__":
    main()
