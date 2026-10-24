"""The tenant's lang (tenants.json, else UI_LANG, else zh) lands as summary.lang_line in every prompt whose reply the
owner reads: summary (and the live agent's final summary), live analysis, recap, ask, profiles."""
import datetime as dt
import json
import tempfile
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException

import ask
import db
import live
import profiles
import recap
import routes_api
import summary
import tenant
from tenant import T


def tenants(tmp: Path, ts: list[dict]) -> Path:
    p = tmp / "tenants.json"
    p.write_text(json.dumps([{"id": f"t{i}", "token": f"k{i}", "data": f"{tmp}/{i}", "recordings": f"{tmp}/{i}r", **x} for i, x in enumerate(ts)]))
    p.chmod(0o600)
    return p


def test_load(tmp_path, monkeypatch):
    monkeypatch.delenv("UI_LANG", raising=False)
    assert [t.lang for t in tenant.load(tenants(tmp_path, [{}, {"lang": "en"}]))] == ["zh", "en"], "no lang: zh"
    monkeypatch.setenv("UI_LANG", "en")
    assert [t.lang for t in tenant.load(tenants(tmp_path, [{}, {"lang": "zh"}]))] == ["en", "zh"], "UI_LANG for those without"
    with pytest.raises(SystemExit):
        tenant.load(tenants(tmp_path, [{"lang": "fr"}]))


def prompts(monkeypatch) -> dict[str, str]:
    """Each builder's system prompt as the model would get it, for the current tenant."""
    seen = []

    async def fake(msgs, timeout=300, kind=None, **extra):
        seen.append(msgs[0]["content"])
        return {"choices": [{"message": {"content": "[c20261004100000]"}}]} if kind == "ask" else ({}, {})
    monkeypatch.setattr(summary, "complete", fake)
    monkeypatch.setattr(summary, "call", fake)
    anyio.run(recap.make, "2026-10-04")
    anyio.run(profiles.profile, "老王", [], [], [])
    anyio.run(ask.ask, "x", dt.datetime(2026, 10, 4, 12))
    return {"summary": summary.instructions([], "2026-10-04T10:00:00"), "live": live.new("2026-10-04T10:00:00", "phone", [])["messages"][0]["content"],
            "recap": seen[0], "profile": seen[1], "ask": seen[2]}


def test_prompts(monkeypatch):
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    with db.tx() as c:
        db.insert_conversation(c, dict(id="c20261004100000", start="2026-10-04T10:00:00.000", end="2026-10-04T10:30:00.000",
                                       participants=["我"], speech_s=600, sentences=1))
        db.put_summary({"conversation": "c20261004100000", "status": "done", "title": "t", "summary": "s"}, c)
    assert T().lang == "zh" and summary.lang_line() == summary.ZH, "a tenant without lang: zh"
    for lang, line in (("zh", summary.ZH), ("en", summary.EN)):
        T().lang = lang
        for k, p in prompts(monkeypatch).items():
            assert line in p, (lang, k)
    assert "用中文纯文本" not in ask.SYSTEM, "ask's own words leave the language to lang_line"


def test_api():
    path = Path(tempfile.mkdtemp()) / "t.db"
    db.open_db(path)
    assert anyio.run(routes_api.lang_get) == {"lang": "zh"}
    assert anyio.run(routes_api.lang_put, {"lang": "en"}) == {"lang": "en"} and T().lang == "en"
    with pytest.raises(HTTPException) as e:
        anyio.run(routes_api.lang_put, {"lang": "fr"})
    assert e.value.status_code == 400 and T().lang == "en"
    assert db.open_db(path).lang == "en", "a restart keeps the PUT's, over the tenants file's zh"


def test_tenants_file_without_put():
    path = Path(tempfile.mkdtemp()) / "t.db"
    tenant.enter(tenant.Tenant("x", "", path.parent, path.parent, "en", db=path))
    db.init()
    assert T().lang == "en", "no PUT yet: the tenants file's lang stays"
