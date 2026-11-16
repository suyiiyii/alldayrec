"""gain.sh on the deploy host: each instance's gain.py as its unit runs it (in <dir>/server, with <dir>/.env's variables,
none of them printed), and "no instance has that tenant" only when none has it: a failure says so."""
import json
import os
import subprocess
import sys
from pathlib import Path

from alldayrec import db
from alldayrec import tenant
from test_deploy import HERE

SECRET = "made-up-secret-value"


def setup(tmp: Path, name: str, ids: str) -> str:
    """An instance dir:unit whose tenants.json is where its .env's DATA_DIR says (not the dir itself), with a python
    that runs only in <dir>/server, as its unit does (this venv has the package installed: any cwd would import it)."""
    d, data = tmp / name, tmp / f"{name}-data"
    (d / "server/.venv/bin").mkdir(parents=True)
    (d / "server/alldayrec").symlink_to(HERE / "alldayrec")
    (d / ".env").write_text(f"# made up\nDATA_DIR=/nowhere\nDATA_DIR={data}\nLLM_API_KEY={SECRET}\n")
    py = d / "server/.venv/bin/python"
    py.write_text(f'#!/bin/sh\n[ "$(pwd -P)" = "{(d / "server").resolve()}" ] || {{ echo "not in server/" >&2; exit 9; }}\n'
                  f'exec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    ts = [{"id": i, "token": f"t{i}", "data": str(data / i), "recordings": str(data / i)} for i in ids]
    data.mkdir()
    (data / "tenants.json").write_text(json.dumps(ts))
    (data / "tenants.json").chmod(0o600)
    for t in tenant.load(data / "tenants.json"):
        t.data.mkdir()
        tenant.call(t, db.init)
    return f"{d}:lr-{name}"


def gain_sh(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    """As gain.sh runs itself on the host: piped into bash -s, no DATA_DIR of the caller's."""
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"} | {"HOME": str(tmp), "XDG_DATA_HOME": str(tmp)}
    r = subprocess.run(["bash", "-s", "--", "remote", *args], stdin=open(HERE / "gain.sh"), capture_output=True, text=True,
                       env=env)
    assert SECRET not in r.stdout + r.stderr
    return r


def test_list_and_set(tmp_path):
    one, two = setup(tmp_path, "one", "a"), setup(tmp_path, "two", "b")
    r = gain_sh(tmp_path, f"{one} {two}", "--tenant", "b", "set", "LR-SH", "4.5", "--note", "made up")
    assert r.returncode == 0 and "b\tLR-SH\t" in r.stdout and "offset=4.5" in r.stdout and r.stderr == "", r.stdout + r.stderr
    r = gain_sh(tmp_path, f"{one} {two}", "list")
    assert r.returncode == 0 and "b\tLR-SH\tnow\toffset=4.5" in r.stdout, r.stdout + r.stderr


def test_no_tenant_or_failed(tmp_path):
    one, two = setup(tmp_path, "one", "a"), setup(tmp_path, "two", "b")
    r = gain_sh(tmp_path, f"{one} {two}", "--tenant", "zz", "list")
    assert r.returncode == 1 and "no instance has that tenant" in r.stderr, r.stdout + r.stderr
    assert gain_sh(tmp_path, one, "--tenant", "a", "set", "LR-SH", "1").returncode == 0
    (tmp_path / "two-data/tenants.json").write_text("not json")  # two fails; one still lists
    r = gain_sh(tmp_path, f"{one} {two}", "list")
    assert r.returncode == 1 and "a\t" in r.stdout and "two: gain.py failed" in r.stderr, r.stdout + r.stderr
    assert "no instance has that tenant" not in r.stderr
