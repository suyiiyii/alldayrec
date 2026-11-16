"""Invalid tenant configuration fails at startup without exposing configuration values."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from alldayrec import tenant


SECRET = "synthetic-secret-never-log"
ONE = {"id": "sample", "token": SECRET, "data": "data", "recordings": "recordings"}


@pytest.mark.parametrize("content", [
    b"\xff",
    '[{"token": "' + SECRET + '",',
    json.dumps({"tenants": [ONE]}),
    *[json.dumps(x) for x in (None, True, 12, SECRET)],
    *[json.dumps([x]) for x in (None, True, 12, SECRET, [])],
    *[json.dumps([{k: v for k, v in ONE.items() if k != missing}]) for missing in ONE],
    *[json.dumps([{**ONE, field: value}]) for field in ONE for value in (None, True, 12, [], {})],
    *[json.dumps([{**ONE, "lang": value}]) for value in (True, 12, [], {}, "fr", SECRET)],
    json.dumps([{**ONE, "id": SECRET, "lang": "fr"}]),
])
def test_invalid_config_exits_cleanly(tmp_path, content):
    check_invalid_config(tmp_path, content)


def check_invalid_config(tmp_path, content, setup=""):
    path = tmp_path / "tenants.json"
    original = content if isinstance(content, bytes) else content.encode()
    path.write_bytes(original)
    path.chmod(0o600)
    result = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; from alldayrec.tenant import load; "
         "import json, sys\n" + setup + "\nload(Path(sys.argv[1]))", str(path)],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "PYTHONPATH": str(Path(tenant.__file__).parent.parent)},
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert str(path) in result.stderr
    assert "expected a JSON array of objects" in result.stderr
    assert all(field in result.stderr for field in ONE)
    assert "Traceback" not in output and SECRET not in output
    if setup:
        assert result.stderr == (f"{path}: JSON exceeds parser limits; expected a JSON array of objects "
                                 "with string fields id, token, data, recordings, optional lang (zh or en) and sound (true or false)\n")
    assert result.stdout == ""
    assert path.read_bytes() == original and path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("content, limit, error", [
    ('[' * 1100 + '"' + SECRET + '"' + ']' * 1100,
     "sys.setrecursionlimit(300)", "RecursionError"),
    ('[{"token":"' + SECRET + '","id":' + '1' * 5000 + '}]',
     "sys.set_int_max_str_digits(640)", "ValueError"),
], ids=["nesting-limit", "integer-digit-limit"])
def test_json_parser_limits_exit_cleanly(tmp_path, content, limit, error):
    # Limits vary by Python version/settings. Set them only in the child, and
    # prove the real decoder hits the intended limit before exercising load().
    setup = f"""
{limit}
try:
    json.loads(Path(sys.argv[1]).read_text())
except {error}:
    pass
else:
    raise AssertionError("JSON parser limit was not reached")
"""
    check_invalid_config(tmp_path, content, setup)


@pytest.mark.parametrize("error", [ValueError, RecursionError])
def test_tenant_construction_errors_propagate(tmp_path, monkeypatch, error):
    path = tmp_path / "tenants.json"
    path.write_text(json.dumps([ONE]))
    path.chmod(0o600)

    def broken_tenant(*args, **kwargs):
        raise error("construction error")

    monkeypatch.setattr(tenant, "Tenant", broken_tenant)
    with pytest.raises(error, match="construction error"):
        tenant.load(path)


@pytest.mark.parametrize("lang", [None, "", "zh", "en"])
def test_valid_config_keeps_language_fallback(tmp_path, monkeypatch, lang):
    monkeypatch.setenv("UI_LANG", "en")
    path = tmp_path / "tenants.json"
    path.write_text(json.dumps([{**ONE, "lang": lang, "extra": "ignored"}]))
    path.chmod(0o600)
    loaded, = tenant.load(path)
    assert (loaded.id, loaded.token, loaded.data, loaded.recordings, loaded.lang) == (
        ONE["id"], SECRET, Path("data"), Path("recordings"), lang or "en")
