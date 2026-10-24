"""tenants.sh merge stops before restarting anything when an instance it merges does not stop (two processes on one
store otherwise), and add leaves nothing behind when the server would not take the new tenants.json."""
import json
import os
import subprocess

from test_deploy import HERE, instance

A = [{"id": "a", "token": "ta", "data": "/a/data", "recordings": "/a/recordings"}]
B = [{"id": "b", "token": "tb", "data": "/b/data", "recordings": "/b/recordings"}]


def tenants_sh(tmp, mode, arg, *instances):
    """remote mode arg instances, with a systemctl that logs its calls and fails `disable`."""
    (tmp / "bin").mkdir(exist_ok=True)
    (tmp / "bin/systemctl").write_text(f'#!/bin/sh\necho "$@" >> {tmp}/systemctl.log\n[ "$1" != disable ]\n')
    (tmp / "bin/systemctl").chmod(0o755)
    return subprocess.run(["bash", HERE / "tenants.sh", "remote", mode, arg, *instances], capture_output=True, text=True,
                          env={**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}"})


def setup(tmp, name, ts):
    i = instance(tmp, name, json.dumps(ts))
    pkg = tmp / name / "server/alldayrec"
    pkg.mkdir()
    for f in ("__init__.py", "tenant.py"):
        (pkg / f).symlink_to(HERE / "alldayrec" / f)
    (pkg / "COMMIT").write_text("x\n")
    return i


def test_merge_stops_when_one_does_not_stop(tmp_path):
    src, to = setup(tmp_path, "src", B), setup(tmp_path, "to", A)
    r = tenants_sh(tmp_path, "merge", "-", src, to)
    assert r.returncode == 1 and "did not stop" in r.stdout and "tenants.json.new" in r.stdout, r.stdout + r.stderr
    assert json.loads((tmp_path / "to/tenants.json").read_text()) == A, "unchanged"
    assert "restart" not in (tmp_path / "systemctl.log").read_text(), "nothing restarted"


def test_add_leaves_nothing_when_refused(tmp_path):
    to = setup(tmp_path, "to", [{**A[0], "token": ""}])  # a lone tenant without a token: no second one beside it
    r = tenants_sh(tmp_path, "add", "new", to)
    assert r.returncode == 1 and "would not start" in r.stdout, r.stdout + r.stderr
    assert not (tmp_path / "lr-new").exists() and not (tmp_path / "systemctl.log").exists()


def test_merge_needs_others(tmp_path):  # the default (test instance only) has nobody to merge: no restart for nothing
    to = setup(tmp_path, "to", A)
    r = tenants_sh(tmp_path, "merge", "-", to)
    assert r.returncode == 1 and "needs the others" in r.stdout, r.stdout + r.stderr
    assert not (tmp_path / "systemctl.log").exists()
