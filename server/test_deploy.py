"""deploy.sh installs nothing anywhere while an instance's tenants.json is one its new server would not find (DATA_DIR in
its .env) or start with; it leaves an instance whose unit is disabled alone (merged into another one)."""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOOD = json.dumps([{"id": "a", "token": "t", "data": "/x", "recordings": "/y"}])


def instance(tmp: Path, name: str, tenants: str | None = None, mode: int = 0o600, data_dir: bool = True) -> str:
    d = tmp / name
    (d / "server/.venv/bin").mkdir(parents=True)
    (d / ".env").write_text(f"DATA_DIR={d}\n" if data_dir else "PORT=1\n")
    py = d / "server/.venv/bin/python"  # this venv's (a symlink would lose its packages)
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    if tenants is not None:
        (d / "tenants.json").write_text(tenants)
        (d / "tenants.json").chmod(mode)
    return f"{d}:lr-{name}"


def deploy(tmp: Path, *instances: str, disabled: str = "") -> subprocess.CompletedProcess:
    tgz = tmp / "release.tgz"  # as deploy.sh packs it
    subprocess.run(["tar", "-czf", tgz, "-C", HERE, "alldayrec/tenant.py"], check=True)
    (tmp / "bin").mkdir(exist_ok=True)  # systemctl: every unit enabled but those in $DISABLED
    (tmp / "bin/systemctl").write_text('#!/bin/sh\ncase " $DISABLED " in *" $2 "*) echo disabled; exit 1;; esac; echo enabled\n')
    (tmp / "bin/systemctl").chmod(0o755)
    return subprocess.run(["bash", HERE / "deploy.sh", "remote", "deploy", *instances], capture_output=True, text=True,
                          env={**os.environ, "LR_TGZ": str(tgz), "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "DISABLED": disabled,
                               "HOME": str(tmp), "XDG_DATA_HOME": ""})  # no DATA_DIR: a user data dir with no tenants.json


def test_stops_before_installing(tmp_path):
    good = instance(tmp_path, "good", GOOD)
    for bad, why in ((instance(tmp_path, "none"), "no tenants file"), (instance(tmp_path, "open", GOOD, 0o644), "chmod 600"),
                     (instance(tmp_path, "broken", "["), "invalid JSON at line 1, column 2"),
                     (instance(tmp_path, "nodir", GOOD, data_dir=False), "no tenants file")):
        r = deploy(tmp_path, good, bad)  # the good one first: not installed either
        assert r.returncode == 1 and why in r.stderr and "cannot read" in r.stdout and "nothing installed" in r.stdout, r.stdout + r.stderr
        assert not list(tmp_path.glob("*/backup-*"))
    r = deploy(tmp_path, good)  # passes the check, then fails installing: this fake instance has no server
    assert "cannot read" not in r.stdout and list(tmp_path.glob("good/backup-deploy-*")), r.stdout + r.stderr


def test_leaves_disabled_alone(tmp_path):
    good, merged = instance(tmp_path, "good", GOOD), instance(tmp_path, "merged")  # merged: its tenants in good's tenants.json
    r = deploy(tmp_path, merged, good, disabled="lr-merged")
    assert "skip" in r.stderr and "cannot read" not in r.stdout and list(tmp_path.glob("good/backup-deploy-*")), r.stdout + r.stderr
    assert not list(tmp_path.glob("merged/backup-*"))


def test_check_asks_where_it_listens(tmp_path):
    """check (and a deploy's) GETs the instance at its HOST, loopback when it listens on every address or HOST is unset."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/systemctl").write_text("#!/bin/sh\necho enabled\n")
    (tmp_path / "bin/curl").write_text(f'#!/bin/sh\nfor a; do last=$a; done; echo "$last" >> "{tmp_path}/urls"; exit 1\n')
    for f in ("systemctl", "curl"):
        (tmp_path / "bin" / f).chmod(0o755)
    for host, want in ((None, "127.0.0.1"), ("0.0.0.0", "127.0.0.1"), ("::", "127.0.0.1"), ("192.0.2.9", "192.0.2.9"),
                       ("fd7a::9", "[fd7a::9]")):
        i = instance(tmp_path, f"h{len(want)}{host}".replace(":", "-"), GOOD)
        env = Path(i.split(":")[0]) / ".env"
        env.write_text(env.read_text() + "PORT=8770\n" + (f"HOST={host}\n" if host else ""))
        (tmp_path / "urls").unlink(missing_ok=True)
        subprocess.run(["bash", HERE / "deploy.sh", "remote", "check", i], capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"})
        urls = (tmp_path / "urls").read_text().split()
        assert urls and all(u.startswith(f"http://{want}:8770/") for u in urls), (host, urls)
