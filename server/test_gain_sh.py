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
    unit(tmp, name, f"{d}/.env (ignore_errors=no)")
    return f"{d}:lr-{name}"


def unit(tmp: Path, name: str, *envs: str):
    """What the stand-in systemctl show prints for lr-<name>: its settings as systemd 255 prints them, each env file
    with its ignore_errors (yes: -<path> in the unit), User the caller (real systemd: not root's files in tmp)."""
    (tmp / "units").mkdir(exist_ok=True)
    (tmp / "units" / f"lr-{name}").write_text(f"WorkingDirectory={tmp / name}/server\nUser={getpass.getuser()}\n" +
                                              "".join(f"EnvironmentFiles={e}\n" for e in envs))


def gain_sh(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    """As gain.sh runs itself on the host: piped into bash -s, no DATA_DIR of the caller's, a systemctl that answers
    for the units setup() made, and systemd-run real (as root) or a stand-in that logs its arguments."""
    b = tmp / "bin"
    if not b.exists():
        b.mkdir()
        # show -p WorkingDirectory -p User -p EnvironmentFiles <unit>: unit()'s lines; a unit with a .fail file fails
        # (real systemd: a unit unit() did not make is answered by the real systemctl)
        real = f'[ -e "{tmp}/units/$8" ] || exec {shutil.which("systemctl")} "$@"\n' if REAL else ""
        (b / "systemctl").write_text(f'#!/bin/sh\n{real}[ "$1" = show ] && cat "{tmp}/units/$8" 2>/dev/null; '
                                     f'[ ! -e "{tmp}/units/$8.fail" ]\n')
        if not REAL:  # its -p EnvironmentFile (sourced in order: plain lines only; -: skipped when missing) and
            # WorkingDirectory, none of the caller's env
            (b / "systemd-run").write_text(f'''#!/bin/bash
echo "$@" >> "{tmp}/systemd-run.log"
fs=()
while [ "${{1#-}}" != "$1" ]; do
  case $1 in -p) case $2 in EnvironmentFile=*) fs+=("${{2#*=}}") ;; WorkingDirectory=*) wd=${{2#*=}} ;; esac; shift 2 ;; *) shift ;; esac
done
cd "$wd" && exec env -i /bin/bash -c 'set -a
for f in "${{@:2:$1}}"; do [ "${{f#-}}" = "$f" ] || {{ f=${{f#-}}; [ -e "$f" ] || continue; }}; . "$f" || exit 1; done
set +a; exec "${{@:$1+2}}"' _ ${{#fs[@]}} "${{fs[@]}}" "$@"
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
        assert "DATA_DIR=" not in log and " -E " not in log and "--setenv" not in log, log
        assert log.startswith("--pipe --wait --quiet --collect --expand-environment=no "), log


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
    ('DATA_DIR="/made-up/nl-7q\n  gain.py: fake-7q"\n', "7q"),  # a value whose own line looks like gain.py's message
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


def test_args_as_given(tmp_path):  # systemd expands no $VAR (nor %n) in gain.py's arguments: the note is what was typed
    one = setup(tmp_path, "one", "a")
    note = "made up ${LLM_API_KEY} $LLM_API_KEY %n"
    r = gain_sh(tmp_path, one, "--tenant", "a", "set", "LR-SH", "1", "--note", note)  # gain_sh: SECRET not in the output
    assert r.returncode == 0 and note in r.stdout, r.stdout + r.stderr
    with sqlite3.connect(tmp_path / "one-data/a/live.db") as c:
        assert c.execute("SELECT note FROM device_gain").fetchall() == [(note,)]


def test_env_files_in_order_optional_ones_optional(tmp_path):  # later files win; a missing optional (-) one is skipped
    one, _ = setup(tmp_path, "one", "a", f"DATA_DIR={tmp_path / 'two-data'}\n"), setup(tmp_path, "two", "a")
    (tmp_path / "over.env").write_text(f"DATA_DIR={tmp_path / 'one-data'}\n")
    unit(tmp_path, "one", f"{tmp_path}/one/.env (ignore_errors=no)", f"{tmp_path}/over.env (ignore_errors=yes)",
         f"{tmp_path}/missing.env (ignore_errors=yes)")
    r = gain_sh(tmp_path, one, "--tenant", "a", "set", "LR-SH", "4.5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (rows(tmp_path, "one", "a"), rows(tmp_path, "two", "a")) == (1, 0)
    if not REAL:
        assert f"-p EnvironmentFile={tmp_path}/one/.env -p EnvironmentFile=-{tmp_path}/over.env -p EnvironmentFile=-" \
            f"{tmp_path}/missing.env " in (tmp_path / "systemd-run.log").read_text()


def test_no_data_dir_runs_nothing(tmp_path):  # the optional file with DATA_DIR missing: not the default dir's tenants.json
    one, _ = setup(tmp_path, "one", "a"), setup(tmp_path, "decoy", "a")
    home = tmp_path / "home"
    base = home / "Library/Application Support" if sys.platform == "darwin" else home / ".local/share"
    base.mkdir(parents=True)
    (base / "alldayrec").symlink_to(tmp_path / "decoy-data")  # where gain.py would look without DATA_DIR
    (tmp_path / "home.env").write_text(f"HOME={home}\nXDG_DATA_HOME={home}/.local/share\n")
    unit(tmp_path, "one", f"{tmp_path}/home.env (ignore_errors=yes)", f"{tmp_path}/missing.env (ignore_errors=yes)")
    r = gain_sh(tmp_path, one, "--tenant", "a", "set", "LR-SH", "1")
    assert r.returncode == 1 and "  gain.py: no DATA_DIR from the unit EnvironmentFiles" in r.stderr, r.stdout + r.stderr
    assert "one: gain.py failed (exit 1)" in r.stderr and rows(tmp_path, "decoy", "a") == 0


@pytest.mark.parametrize("bad", ["EnvironmentFiles=/made-up/x.env (ignore_errors=maybe)", "fail"])
def test_systemctl_unknown_or_failed_runs_nothing(tmp_path, bad):
    one = setup(tmp_path, "one", "a")
    if bad == "fail":
        (tmp_path / "units/lr-one.fail").touch()
    else:
        unit(tmp_path, "one", f"{tmp_path}/one/.env (ignore_errors=no)", bad.split("=", 1)[1])
    r = gain_sh(tmp_path, one, "list")
    assert r.returncode == 1 and "one: systemctl show lr-one failed or printed what" in r.stderr, r.stdout + r.stderr
    assert not (tmp_path / "systemd-run.log").exists()


@pytest.mark.parametrize("ids, args, said, rc", [
    ("a", ["set", "LR-SH", "61"], "set: offset 61.0: a finite number of dB within +-60", 1),
    ("a", ["set", "LR-SH", "1", "--since", "not-a-date"], "--since not-a-date: an ISO time or unix ms", 1),
    ("ab", ["set", "LR-SH", "1"], "set: which tenant (--tenant)", 1),
    ("a", ["set", "LR-SH", "not-a-float"], "python -m alldayrec.gain set: error: argument offset_db: invalid float", 2),
])
def test_own_messages_shown(tmp_path, ids, args, said, rc):  # what gain.py says of the command line, and its exit code
    one = setup(tmp_path, "one", ids)
    r = gain_sh(tmp_path, one, *(["--tenant", "a"] if ids == "a" else []), *args)
    assert r.returncode == 1 and f"  gain.py: {said}" in r.stderr and f"one: gain.py failed (exit {rc})" in r.stderr, \
        r.stdout + r.stderr
    assert rc == 1 or "  gain.py: usage: python -m alldayrec.gain" in r.stderr
    assert all(rows(tmp_path, "one", i) == 0 for i in ids)


@real_only
def test_real_unit(tmp_path):  # a unit systemd itself knows, its settings as the real systemctl show prints them
    one, u = setup(tmp_path, "one", "a"), f"lr-gainsh-test-{os.getpid()}"
    subprocess.run(["sudo", "-n", "systemd-run", "--quiet", "--unit", u, "--remain-after-exit",
                    "-p", f"EnvironmentFile={tmp_path}/one/.env", "-p", f"EnvironmentFile=-{tmp_path}/missing.env",
                    "-p", f"WorkingDirectory={tmp_path}/one/server", "-p", f"User={getpass.getuser()}", "true"], check=True)
    try:
        r = gain_sh(tmp_path, one.replace(":lr-one", f":{u}"), "--tenant", "a", "set", "LR-SH", "2")
        assert r.returncode == 0 and "a\tLR-SH\t" in r.stdout, r.stdout + r.stderr
        assert rows(tmp_path, "one", "a") == 1
    finally:
        subprocess.run(["sudo", "-n", "systemctl", "stop", u])
