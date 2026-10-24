"""apk_release.sh puts the APK in every instance it is given (its first tenant's data) and keeps the one it replaces; an
instance whose APK is not older changes nothing."""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def instance(tmp: Path, name: str, apk: str | None = None) -> str:
    d = tmp / name
    (d / "server/.venv/bin").mkdir(parents=True)
    py = d / "server/.venv/bin/python"
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    (d / "server/alldayrec").mkdir()
    (d / "server/alldayrec/apk.py").write_text("import sys; print(open(sys.argv[1]).read())")  # these APKs are their version
    (d / "data").mkdir()
    if apk:
        (d / "data/live-record.apk").write_text(apk)
    (d / "tenants.json").write_text(json.dumps([{"id": "a", "token": "t", "data": str(d / "data")}]))
    (d / ".env").write_text("PORT=1\n")
    return f"{d}:lr-{name}"


def release(tmp: Path, new: str, *instances: str) -> subprocess.CompletedProcess:
    (tmp / "lr-app.apk").write_text(new)  # as scp put it
    (tmp / "bin").mkdir(exist_ok=True)  # curl: takes the header from stdin, and the server reads the new file
    (tmp / "bin/curl").write_text(f"#!/bin/sh\ncat >/dev/null\necho '{{\"versionCode\":{new.split('+')[1]}}}'\n")
    (tmp / "bin/curl").chmod(0o755)
    return subprocess.run(["bash", HERE / "apk_release.sh", "remote", new, *instances], capture_output=True, text=True,
                          env={**os.environ, "LR_APK": str(tmp / "lr-app.apk"), "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}"})


def test_every_instance(tmp_path):
    r = release(tmp_path, "1.4.8+23", instance(tmp_path, "a", "1.2.1+5"), instance(tmp_path, "b"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert [(tmp_path / f"{n}/data/live-record.apk").read_text() for n in "ab"] == ["1.4.8+23"] * 2
    assert (tmp_path / "a/data/live-record.apk.prev-1.2.1+5").read_text() == "1.2.1+5"
    assert not (tmp_path / "lr-app.apk").exists()


def test_not_newer(tmp_path):
    r = release(tmp_path, "1.4.8+23", instance(tmp_path, "a", "1.4.8+23"))
    assert r.returncode == 1 and "not newer" in r.stderr, r.stdout + r.stderr
    assert not list(tmp_path.glob("a/data/*.prev-*")) and not (tmp_path / "lr-app.apk").exists()
