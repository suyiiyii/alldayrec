"""gain.sh on the deploy host: each instance's gain.py as its unit runs it (systemd-run with the unit's EnvironmentFile,
WorkingDirectory and User, so systemd reads the .env), nothing of the .env in its output, and "no instance has that
tenant" only when none has it: a failure says so. With real systemd (sudo systemd-run: the CI runner, where it must be
there) every test runs on it, and the .env syntax ones only there; elsewhere a stand-in systemd-run sources the .env."""
import getpass
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from alldayrec import db
from alldayrec import tenant
from test_deploy import HERE

SECRET = "made-up-secret-value"
REAL = bool(shutil.which("systemd-run")) and os.path.isdir("/run/systemd/system") and \
    subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
if os.environ.get("CI") and sys.platform == "linux":
    assert REAL, "the CI runner has systemd and sudo: the .env tests must run on them, not be skipped"
real_only = pytest.mark.skipif(not REAL, reason="needs systemd-run and sudo (runs on CI)")


def setup(tmp: Path, name: str, ids: str, env: str | None = None) -> str:
    """An instance dir:unit whose tenants.json is where its .env's DATA_DIR says (not the dir itself), with a python
    that runs only in <dir>/server, as its unit does (this venv has the package installed: any cwd would import it),
    and its unit's settings for the stand-in systemctl."""
    d, data = tmp / name, tmp / f"{name}-data"
    (d / "server/.venv/bin").mkdir(parents=True)
    (d / "server/alldayrec").symlink_to(HERE / "alldayrec")
    (d / ".env").write_text(env.format(data=data) if env else f"# made up\nDATA_DIR=/nowhere\nDATA_DIR={data}\nLLM_API_KEY={SECRET}\n")
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
    units = tmp / "units"
    units.mkdir(exist_ok=True)
    (units / f"lr-{name}.WorkingDirectory").write_text(f"{d}/server\n")
    (units / f"lr-{name}.EnvironmentFiles").write_text(f"{d}/.env (ignore_errors=no)\n")  # as systemd 255 prints it
    (units / f"lr-{name}.User").write_text(getpass.getuser() + "\n")  # real systemd: not root's files in tmp
    return f"{d}:lr-{name}"


def gain_sh(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    """As gain.sh runs itself on the host: piped into bash -s, no DATA_DIR of the caller's, a systemctl that answers
    for the units setup() made, and systemd-run real (as root) or a stand-in that logs its arguments."""
    b = tmp / "bin"
    if not b.exists():
        b.mkdir()
        (b / "systemctl").write_text(f'#!/bin/sh\n[ "$1 $2 $4" = "show -p --value" ] && cat "{tmp}/units/$5.$3" 2>/dev/null; true\n')
        if not REAL:  # its -p EnvironmentFile (sourced: plain lines only) and WorkingDirectory, none of the caller's env
            (b / "systemd-run").write_text(f'''#!/bin/bash
echo "$@" >> "{tmp}/systemd-run.log"
while [ "${{1#-}}" != "$1" ]; do
  case $1 in -p) case $2 in EnvironmentFile=*) f=${{2#*=}} ;; WorkingDirectory=*) wd=${{2#*=}} ;; esac; shift 2 ;; *) shift ;; esac
done
cd "$wd" && exec env -i /bin/bash -c 'set -a; . "$0"; set +a; exec "$@"' "$f" "$@"
''')
        for f in b.iterdir():
            f.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"} | {"HOME": str(tmp), "XDG_DATA_HOME": str(tmp)}
    sudo = ["sudo", "-n", "env", f"PATH={b}:{os.environ['PATH']}"] if REAL else []
    r = subprocess.run([*sudo, "bash", "-s", "--", "remote", *args], stdin=open(HERE / "gain.sh"), capture_output=True,
                       text=True, env={**env, "PATH": f"{b}:{os.environ['PATH']}"})
    assert SECRET not in r.stdout + r.stderr
    return r


def rows(tmp: Path, name: str, t: str) -> int:
    with sqlite3.connect(tmp / f"{name}-data" / t / "live.db") as c:
        return c.execute("SELECT count(*) FROM device_gain").fetchone()[0]


def test_list_and_set(tmp_path):
    one, two = setup(tmp_path, "one", "a"), setup(tmp_path, "two", "b")
    r = gain_sh(tmp_path, f"{one} {two}", "--tenant", "b", "set", "LR-SH", "4.5", "--note", "made up")
    assert r.returncode == 0 and "b\tLR-SH\t" in r.stdout and "offset=4.5" in r.stdout and r.stderr == "", r.stdout + r.stderr
    r = gain_sh(tmp_path, f"{one} {two}", "list")
    assert r.returncode == 0 and "b\tLR-SH\tnow\toffset=4.5" in r.stdout, r.stdout + r.stderr
    assert (rows(tmp_path, "one", "a"), rows(tmp_path, "two", "b")) == (0, 1)
    if not REAL:  # the unit's own settings, and DATA_DIR only from its .env
        log = (tmp_path / "systemd-run.log").read_text()
        assert f"-p EnvironmentFile={tmp_path}/one/.env -p WorkingDirectory={tmp_path}/one/server -p User=" in log, log
        assert "DATA_DIR" not in log and " -E " not in log and "--setenv" not in log, log


def test_no_tenant_or_failed(tmp_path):
    one, two = setup(tmp_path, "one", "a"), setup(tmp_path, "two", "b")
    r = gain_sh(tmp_path, f"{one} {two}", "--tenant", "zz", "list")  # exit 3 from each, through systemd-run
    assert r.returncode == 1 and "no instance has that tenant" in r.stderr and "failed" not in r.stderr, r.stdout + r.stderr
    assert gain_sh(tmp_path, one, "--tenant", "a", "set", "LR-SH", "1").returncode == 0
    (tmp_path / "two-data/tenants.json").write_text("not json")  # two fails; one still lists
    r = gain_sh(tmp_path, f"{one} {two}", "list")
    assert r.returncode == 1 and "a\t" in r.stdout and "two: gain.py failed (exit 1)" in r.stderr, r.stdout + r.stderr
    assert "no instance has that tenant" not in r.stderr and "invalid JSON" not in r.stderr


def test_unknown_unit_runs_nothing(tmp_path):  # no EnvironmentFile: gain.py would find the user data dir's tenants.json
    one = setup(tmp_path, "one", "a")
    r = gain_sh(tmp_path, one.replace(":lr-one", ":lr-gone"), "list")
    assert r.returncode == 1 and "lr-gone has no WorkingDirectory or EnvironmentFile" in r.stderr, r.stdout + r.stderr
    assert not (tmp_path / "systemd-run.log").exists()


@pytest.mark.parametrize("env, value", [
    ("DATA_DIR=/made-up/nowhere-7q\n", "nowhere-7q"),  # no tenants file there: tenant.load names the path
    ("DATA_DIR={data}\nHOST=made-up-host-7q\n", "made-up-host-7q"),  # a tenant without a token on another HOST
])
def test_no_env_values_in_output(tmp_path, env, value):
    d = tmp_path / "one-data"
    one = setup(tmp_path, "one", "a", env)
    if "HOST" in env:
        (d / "tenants.json").write_text(json.dumps([{"id": "a", "token": "", "data": str(d / "a"), "recordings": str(d / "a")}]))
    r = gain_sh(tmp_path, one, "list")
    assert r.returncode == 1 and "one: gain.py failed (exit 1)" in r.stderr and "gain.py: SystemExit" in r.stderr, r.stdout + r.stderr
    assert value not in r.stdout + r.stderr


@real_only
@pytest.mark.parametrize("env", ['DATA_DIR="{data}"\n', "DATA_DIR='{data}'\n", "DATA_DIR = {data}\n", "  DATA_DIR={data}  \n"])
def test_env_syntax_as_systemd_reads_it(tmp_path, env):
    one = setup(tmp_path, "one", "a", env)
    r = gain_sh(tmp_path, one, "--tenant", "a", "set", "LR-SH", "2")
    assert r.returncode == 0 and "a\tLR-SH\t" in r.stdout, r.stdout + r.stderr
    assert rows(tmp_path, "one", "a") == 1


@real_only
def test_last_data_dir_wins_with_spaces(tmp_path):  # the review's case: the first line names another real instance's
    two = tmp_path / "two-data"
    one = setup(tmp_path, "one", "a", f"DATA_DIR={two}\nDATA_DIR = {{data}}\n")
    setup(tmp_path, "two", "a")
    r = gain_sh(tmp_path, one, "--tenant", "a", "set", "LR-SH", "4.5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (rows(tmp_path, "one", "a"), rows(tmp_path, "two", "a")) == (1, 0)
