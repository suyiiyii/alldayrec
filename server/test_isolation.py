"""Two tenants in one process: each sees only its own data through every route and its WebSocket, and its
requests never change the other's store or recordings; their pipelines run side by side on devices of the same name;
code outside any tenant fails instead of reaching one; nothing per person stays in a module. A route added without a
line in REQUESTS (or a reason in SHARED) fails test_every_route."""
import ast
import contextvars
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import anyio
import httpx2
import numpy as np
import pytest
from starlette.testclient import TestClient

from alldayrec import asr
from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import device_pipeline
from alldayrec import monitor
from alldayrec import pipeline_state
from alldayrec import server
from alldayrec import summary
from alldayrec import tenant
from alldayrec import ws
from test_backlog import PIECE_S, SR, flac, hear, rig, tone

DEV = "iPhone"  # both tenants have one
TODAY = dt.date.today().isoformat()
CID = "c" + TODAY.replace("-", "") + "100000"  # both have this conversation, each its own
AT = f"{TODAY}T10:00:05.000"
MS = int(dt.datetime.fromisoformat(AT).timestamp() * 1000)
OGG = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=2:r=16000", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                     capture_output=True, check=True).stdout
VEC = np.ones(4) / 2
TODO = db.todo_ids(CID, [{"text": "MARKa 待办"}])[0]  # a's todo (seed); b's has another id


def make(root: Path, id: str, token: str) -> tenant.Tenant:
    t = tenant.Tenant(id, token, root / id, root / id)
    t.data.mkdir(parents=True)
    tenant.call(t, db.init)
    return t


def seed(t: tenant.Tenant):
    """Everything a tenant keeps, each text holding MARK<id>: the store, the recordings, its state in memory."""
    mark = f"MARK{t.id}"

    def store():
        with db.tx() as c:
            db.insert_sentences(c, [dict(id="f1", start=AT, end=f"{TODAY}T10:00:09.000", speaker="S1", text=f"{mark} 说的话", source=DEV)])
            db.insert_drafts(c, [dict(id="d1", start=f"{TODAY}T23:59:00.000", end=f"{TODAY}T23:59:01.000", speaker="S1", text=f"{mark} 初稿",
                                      source=DEV)])
            db.insert_conversation(c, dict(id=CID, start=f"{TODAY}T10:00:00.000", end=f"{TODAY}T10:30:00.000", participants=["我", "S1"],
                                           speech_s=600, sentences=1, device=DEV))
            db.put_summary({"conversation": CID, "status": "done", "prompt_version": 8, "title": f"{mark} 标题", "summary": mark, "tags": [mark],
                            "people": [{"who": "S1", "points": [mark]}],
                            "todos": [{"owner": "我", "text": f"{mark} 待办", "due": "今天", "due_date": TODAY}]}, c)
            db.insert_voices(c, [{"conv": CID, "label": "S1", "device": "live", "n": 3, "speech_s": 10, "k": 1, "e": VEC, "clip": f"{mark}.ogg",
                                  "clip_start": AT, "clip_end": f"{TODAY}T10:00:07.000", "clip_text": mark}])
            c.execute("INSERT INTO people VALUES (?, 1, ?)", (f"{mark}人", db.blob(VEC)))
            c.execute("INSERT INTO aliases VALUES (?, ?)", (f"{mark}哥", f"{mark}人"))
            db.put_name_hints({"conv": CID, "names": [{"who": "S1", "name": f"{mark}人", "kind": "self", "at": AT, "by": "S1", "quote": mark}]}, c)
            db.put_kv(f"recap:{TODAY}", {"date": TODAY, "headline": mark, "todos": [], "highlights": [], "people": []}, c)
        db.put_profile({"name": f"{mark}人", "facts": [{"text": mark, "src": []}]})
        db.add_feedback(CID, f"{mark} 反馈")
        db.put_device_log(DEV, [{"src": "app", "seq": 1, "at": AT, "data": {"kind": mark}}])
        db.add_device_status(DEV, AT, {"battery": 50, "note": mark})
        db.add_call("final", f"engine-{mark}", DEV, 100)
        db.add_balance("stepfun", 1.0)
        audio.save(DEV, MS, OGG, "audio/ogg")
        (t.data / "voices" / "clips").mkdir(parents=True)
        (t.data / "voices" / "clips" / f"{mark}.ogg").write_bytes(OGG)
    tenant.call(t, store)
    t.events.append({"type": "event", "at": AT, "kind": "info", "text": mark})
    t.devices[DEV] = pipeline_state.PipelineState(
        conversation={"start": f"{TODAY}T11:00:00.000", "end": f"{TODAY}T11:01:00.000", "sentences": 1, "speech_s": 30,
                      "device": DEV, "note": mark},
        live={"start": f"{TODAY}T11:00:00.000", "topics": [{"title": mark}], "messages": []})


def snapshot(t: tenant.Tenant) -> str:
    """Every row of its store and every file of it, as one hash."""
    def rows():
        c = db.conn()
        return [(name, sorted(map(tuple, c.execute(f"SELECT * FROM {name}")))) for (name,) in
                c.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    files = sorted((str(p.relative_to(t.data)), p.read_bytes()) for p in t.data.rglob("*") if p.is_file() and not p.name.startswith("live.db"))
    return hashlib.sha256(repr((tenant.call(t, rows), files)).encode()).hexdigest()


# what a's token is sent to, route by route: (method, path, request kwargs)
REQUESTS = {
    ("POST", "/api/chunks"): ("POST", "/api/chunks", dict(params={"device": DEV, "start": MS + 60_000}, content=OGG, headers={"content-type": "audio/ogg"})),
    ("POST", "/api/chunks/end"): ("POST", "/api/chunks/end", dict(params={"device": DEV, "t": MS + 62_000})),
    ("POST", "/api/hold/release"): ("POST", "/api/hold/release", dict(params={"device": DEV})),
    ("POST", "/api/device-status"): ("POST", "/api/device-status", dict(params={"device": DEV}, json={"battery": 1})),
    ("POST", "/api/device-log"): ("POST", "/api/device-log", dict(params={"device": DEV}, json=[{"src": "app", "seq": 2, "at": MS, "data": {"kind": "x"}}])),
    ("GET", "/api/device-log"): ("GET", "/api/device-log", dict(params={"device": DEV, "since": TODAY})),
    ("GET", "/api/calls"): ("GET", "/api/calls", dict(params={"since": TODAY})),
    ("GET", "/api/upgrades"): ("GET", "/api/upgrades", dict(params={"device": DEV, "since": TODAY})),
    ("GET", "/api/client-config"): ("GET", "/api/client-config", {}),
    ("GET", "/api/app-setup"): ("GET", "/api/app-setup", {}),
    ("GET", "/api/events"): ("GET", "/api/events", {}),
    ("GET", "/api/live"): ("GET", "/api/live", {}),
    ("POST", "/api/live/{device}"): ("POST", f"/api/live/{DEV}", {}),
    ("GET", "/api/status"): ("GET", "/api/status", {}),
    ("GET", "/api/lang"): ("GET", "/api/lang", {}),
    ("PUT", "/api/lang"): ("PUT", "/api/lang", dict(json={"lang": "en"})),
    ("GET", "/api/sentences"): ("GET", "/api/sentences", {}),
    ("GET", "/api/conversations"): ("GET", "/api/conversations", {}),
    ("GET", "/api/days"): ("GET", "/api/days", {}),
    ("GET", "/api/calendar"): ("GET", "/api/calendar", {}),
    ("GET", "/api/search"): ("GET", "/api/search", dict(params={"q": "说的话"})),
    ("POST", "/api/ask"): ("POST", "/api/ask", dict(json={"q": "说过什么？"})),
    ("POST", "/api/ask/voice"): ("POST", "/api/ask/voice", dict(content=OGG, headers={"content-type": "audio/ogg"})),
    ("POST", "/mcp"): ("POST", "/mcp", dict(json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "grep", "arguments": {"q": "说的话"}}})),
    ("DELETE", "/mcp"): ("DELETE", "/mcp", {}),
    ("GET", "/mcp"): ("GET", "/mcp", {}),
    ("GET", "/api/recap"): ("GET", "/api/recap", {}),
    ("GET", "/api/todos/due"): ("GET", "/api/todos/due", {}),
    ("PUT", "/api/todos/{todo_id}/mark"): ("PUT", f"/api/todos/{TODO}/mark", dict(json={"mark": "done"})),
    ("DELETE", "/api/todos/{todo_id}/mark"): ("DELETE", f"/api/todos/{TODO}/mark", {}),
    ("POST", "/api/todos/marks"): ("POST", "/api/todos/marks", dict(json={"ids": [TODO], "mark": "wrong"})),
    ("POST", "/api/recap/{date}"): ("POST", f"/api/recap/{TODAY}", {}),
    ("GET", "/api/tags"): ("GET", "/api/tags", {}),
    ("GET", "/api/conversation/{conv_id}"): ("GET", f"/api/conversation/{CID}", {}),
    ("POST", "/api/conversation/{conv_id}/feedback"): ("POST", f"/api/conversation/{CID}/feedback", dict(json={"text": "不对"})),
    ("GET", "/api/feedback"): ("GET", "/api/feedback", {}),
    ("POST", "/api/conversation/{conv_id}/summarize"): ("POST", f"/api/conversation/{CID}/summarize", {}),
    ("GET", "/api/names"): ("GET", "/api/names", {}),
    ("POST", "/api/conversation/{conv_id}/rename"): ("POST", f"/api/conversation/{CID}/rename", dict(json={"from": "S1", "to": "MARKa人"})),
    ("GET", "/api/speakers"): ("GET", "/api/speakers", {}),
    ("GET", "/api/speakers/{gid}"): ("GET", "/api/speakers/{gid}", {}),  # gid: the first group a's /api/speakers lists
    ("POST", "/api/speakers/name"): ("POST", "/api/speakers/name", dict(json={"group": "{gid}", "name": "老王"})),
    ("POST", "/api/speakers/detach"): ("POST", "/api/speakers/detach", dict(json={"conv": CID, "label": "老王", "group": "{gid}"})),
    ("POST", "/api/speakers/undo"): ("POST", "/api/speakers/undo", dict(json={"undo": "x"})),  # none of a's: 409
    ("GET", "/api/conversation/{conv_id}/speakers"): ("GET", f"/api/conversation/{CID}/speakers", {}),
    ("GET", "/api/aliases"): ("GET", "/api/aliases", {}),
    ("POST", "/api/aliases"): ("POST", "/api/aliases", dict(json={"alias": "王哥", "name": "老王"})),
    ("GET", "/api/person/{name}"): ("GET", "/api/person/MARKa人", {}),
    ("POST", "/api/person/{name}/profile"): ("POST", "/api/person/MARKa人/profile", {}),
    ("GET", "/api/voice-clip/{name}"): ("GET", "/api/voice-clip/MARKb.ogg", {}),  # the other's clip: not found here
    ("GET", "/api/self"): ("GET", "/api/self", {}),
    ("POST", "/api/self"): ("POST", "/api/self", dict(json={"answer": "later"})),
    ("GET", "/api/audio"): ("GET", "/api/audio", dict(params={"start": AT, "end": f"{TODAY}T10:00:06.000", "device": DEV})),
    ("GET", "/api/listen"): ("GET", "/api/listen", dict(params={"device": DEV})),
}
SHARED = {  # routes with no tenant data: the same for every tenant, by design
    "/": "the web app's page", "/conversations": "page", "/c/{conv_id}": "page", "/record": "page", "/speakers": "page",
    "/live": "page", "/me": "page", "/todos": "page", "/calendar": "page", "/progress": "page", "/connect": "page", "/p/{name}": "page", "/device/{name}": "page",
    "/assets/{name}": "the web app's files", "/app.apk": "the Android app: one for all", "/api/app/latest": "the Android app's version",
    "/api/firmware": "the recorders' firmware: one shelf for all", "/api/firmware/{model}/{version}": "firmware",
    "/api/firmware/latest": "firmware", "/api/firmware/{model}/{version}.zip": "firmware",
    "/api/addresses": "this machine's IPs for the 连接手机 page: the process's, not a tenant's (a token still needed)",
    "/openapi.json": "FastAPI's", "/docs": "FastAPI's", "/docs/oauth2-redirect": "FastAPI's", "/redoc": "FastAPI's",
    "/api/health": "the one route without a token: {ok: true}, no tenant (test_health)",
}


def routes() -> set[tuple[str, str]]:
    out = set()

    def walk(rs):
        for r in rs:
            if hasattr(r, "path"):
                out.update((m, r.path) for m in (getattr(r, "methods", None) or {"WS"}) - {"HEAD"})
            else:
                walk(r.original_router.routes)
    walk(server.app.routes)
    return out


def test_every_route():
    missing = [r for r in routes() if r not in REQUESTS and r[1] not in SHARED and r != ("WS", "/ws")]
    assert not missing, f"routes without an isolation check (REQUESTS) or a reason (SHARED): {sorted(missing)}"


def test_every_page_is_served():
    """Each path of the web app's router has its page here: else a reload of it, or the App opening it, is a 404."""
    pages = re.findall(r'path: "([^"]+)"', (Path(__file__).parent.parent / "web/src/router.ts").read_text())
    served = {re.sub(r"\{\w+\}", "{}", p) for m, p in routes() if m == "GET"}
    missing = [p for p in pages if re.sub(r":\w+", "{}", p) not in served]
    assert pages and not missing, f"router.ts paths the server does not serve: {missing}"


@pytest.fixture
def two(tmp_path, monkeypatch):
    a, b = make(tmp_path, "a", "ta"), make(tmp_path, "b", "tb")
    for t in (a, b):
        seed(t)
    monkeypatch.setattr(core, "TENANTS", [a, b])
    monkeypatch.setitem(core.state, "embed", lambda x: VEC)
    return a, b


def test_routes(two, monkeypatch):
    """a's token through every route: a's data, never b's (in the answer, in what the model is sent), b's store and
    files untouched; b's own token sees b's."""
    a, b = two
    prompts = []

    async def llm(messages, timeout=300, kind="summary", **extra):
        prompts.append(json.dumps(messages, ensure_ascii=False))
        if kind == "ask" and not any(m["role"] == "tool" for m in messages):
            return {"choices": [{"message": {"tool_calls": [{"id": "t1", "type": "function",
                                                              "function": {"name": "grep", "arguments": '{"q": "说的话"}'}}]}}]}
        return {"choices": [{"message": {"content": json.dumps({"headline": "h", "answer": "a"}) if kind != "ask" else f"好 [{CID}]"}}]}
    monkeypatch.setattr(summary, "call", llm)

    async def heard(x, partial=None):  # a spoken question
        return [{"start_ms": 0, "end_ms": 1000, "text": "说过什么？"}]
    monkeypatch.setitem(asr.PROVIDERS, asr.DRAFT_ENGINE, heard)
    before = snapshot(b)

    async def run():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=server.app), base_url="http://live.test") as c:
            as_a = {"Authorization": "Bearer ta"}
            gid = (await c.get("/api/speakers?others=1", headers=as_a)).json()["cards"][0]["id"]  # one 10 s voice: folded
            for key, (method, path, kw) in REQUESTS.items():
                kw, path = dict(kw), path.replace("{gid}", gid)
                if "json" in kw:
                    kw["json"] = json.loads(json.dumps(kw["json"]).replace("{gid}", gid))
                headers = kw.pop("headers", {})
                assert (await c.request(method, path, headers=headers, **kw)).status_code == 401, (key, "no token: refused")
                r = await c.request(method, path, headers={**as_a, **headers}, **kw)
                assert r.status_code < 500, (key, r.status_code, r.text[:300])
                assert b"MARKb" not in r.content and b'"tb"' not in r.content, (key, r.text[:300])
            assert (await c.get("/api/app-setup", headers=as_a)).json() == {"token": "ta"}
            got = (await c.get(f"/api/conversation/{CID}", headers={"Authorization": "Bearer tb"})).json()
            assert got["summary"]["title"] == "MARKb 标题" and "MARKa" not in json.dumps(got, ensure_ascii=False), "b's token: b's"
            assert (await c.get("/api/status", headers={"Cookie": "token=tb"})).json()["devices"][0]["conversation"]["note"] == "MARKb"
            r = await c.get("/?token=tb")
            assert r.status_code == 303 and "token=tb" in r.headers["set-cookie"], "?token= of b: b's cookie"
    anyio.run(run)
    assert prompts and not [p for p in prompts if "MARKb" in p], "the model saw nothing of b's"
    assert any("MARKa" in p for p in prompts), "ask's tools read a's store"
    assert snapshot(b) == before, "nothing of b's changed"
    assert b.events[-1]["text"] == "MARKb" and len(b.events) == 1 and b.devices[DEV].conversation["note"] == "MARKb"
    assert a.chunks and b.chunks is None, "the upload is a's"
    assert a.lang == "en" and b.lang == "zh", "a's PUT /api/lang is a's"
    assert b.summaries[1].statistics().current_buffer_used == 0 and a.summaries[1].statistics().current_buffer_used >= 1


def test_health(two):
    """GET /api/health answers without a token; every other route still wants one."""
    c = TestClient(server.app)  # no `with`: the lifespan does not start
    assert c.get("/api/health").json() == {"ok": True}
    assert c.get("/api/status").status_code == 401


def test_websocket(two):
    """Each page gets its own tenant's pushes; a watch is its tenant's."""
    a, b = two
    c = TestClient(server.app)  # no `with`: the lifespan (the model, the tenants' loops) does not start
    with c.websocket_connect("/ws", headers={"Authorization": "Bearer ta"}) as wa, \
            c.websocket_connect("/ws", headers={"Cookie": "token=tb"}) as wb:
        wa.send_json({"type": "watch", "wake": True})
        wb.send_json({"type": "ping"})
        async def push(t, msg):  # a push of t's (a pipeline's, a note's): it runs as t
            tenant.enter(t)
            await ws.broadcast(msg)
        for t, w, msg in ((b, wb, {"type": "event", "text": "MARKb"}), (a, wa, {"type": "event", "text": "MARKa"})):
            w.portal.call(push, t, msg)  # each WebSocket's own event loop (the test client's thread)
        assert wa.receive_json() == {"type": "event", "text": "MARKa"}, "a's first push is a's, b's went to b alone"
        assert wb.receive_json() == {"type": "event", "text": "MARKb"}
    assert a.watch_until > 0 and a.wake_at > 0 and b.watch_until == 0 and b.wake_at == 0
    assert not a.clients and not b.clients, "gone when they close"
    with pytest.raises(Exception):
        with c.websocket_connect("/ws", headers={"Authorization": "Bearer nobody"}):
            pass


def test_pipelines_side_by_side(tmp_path, monkeypatch):
    """Both tenants' pipelines on a device of the same name at once: each finalizes only its own audio, keeps its own
    queue position and diarists; a rename in one teaches only its own diarists."""
    rig(tmp_path, monkeypatch, hear)  # the fake VAD, engines and LLM
    a, b = make(tmp_path, "a", "ta"), make(tmp_path, "b", "tb")
    silence = np.zeros(PIECE_S * SR, np.int16)
    t0 = dt.datetime.fromtimestamp(round(dt.datetime.now().timestamp()) - 6 * PIECE_S)
    tones = {a: (1, 2), b: (51, 52)}
    finals = lambda: [r["text"] for r in db.sentences(core.iso(t0), "~", source=DEV)]

    async def one(t):
        tenant.enter(t)
        np.savez(t.data / "owner-redimnet2.npz", owner=np.array([1.0, 0, 0, 0]))
        for k, i in enumerate(tones[t]):
            audio.save(DEV, int((t0 + dt.timedelta(seconds=PIECE_S * k)).timestamp() * 1000),
                       flac(np.concatenate([silence[:10 * SR], tone(i, 3), silence[13 * SR:]])), "audio/flac")
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEV)
            for _ in range(120):
                if len(finals()) >= 2:
                    break
                await anyio.sleep(0.5)
            dias[t.id] = list(t.dias)
            tg.cancel_scope.cancel()
        return finals()

    got, dias = {}, {}

    async def both():
        async def keep(t):
            got[t.id] = await one(t)
        async with anyio.create_task_group() as tg:
            for t in (a, b):
                tg.start_soon(keep, t)
    anyio.run(both)
    assert got == {"a": ["1", "2"], "b": ["51", "52"]}, got
    assert DEV in a.read_to and DEV in b.read_to and a.read_to is not b.read_to
    assert a.devices[DEV].seg is not b.devices[DEV].seg
    assert len(dias["a"]) == len(dias["b"]) == 2 and not set(map(id, dias["a"])) & set(map(id, dias["b"])), "each its own diarists"
    assert not a.dias and not b.dias, "gone with their pipelines"


def test_outside_any_tenant():
    """No tenant, no data: an error, never someone's store."""
    for f in (db.conn, lambda: audio.queued(DEV), lambda: ws.note("x"), monitor.status, lambda: tenant.T()):
        with pytest.raises(LookupError):
            contextvars.Context().run(f)


def test_nothing_per_person_in_a_module():
    """The modules the server runs keep no mutable state at module level but these (none of them a person's): a cache
    or a dict added there would be shared by every tenant."""
    allowed = {"core": {"TENANTS", "state", "NO_CACHE"}, "asr": {"PROVIDERS"}, "monitor": {"CHARGE"}, "db": {"_local"}, "ask": {"TOOLS", "SPECS"},
               "conversations": {"IGNORE"}, "hints": {"NOT_NAMES"}, "mcp_server": {"SERVER", "S", "CURSOR", "DATE", "READ", "TOOLS"},
               "infer": {"MODELS", "SOURCES", "@cache _vad"},  # the model files' hashes and URLs; the VAD model, loaded once for everyone
               "local_asr": {"FILES"}}  # the model files' hashes (its recognizer is one for everyone too, like the VAD)
    here = Path(server.__file__).parent
    mutable = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)
    found = {}
    for name, m in list(sys.modules.items()):  # a getattr below may import a module (seen on 3.14)
        f = Path(getattr(m, "__file__", "") or "")
        if f.parent != here or f.name == "__init__.py":
            continue
        name = name.removeprefix("alldayrec.")
        tree = ast.parse(f.read_text())
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                v = node.value
                call = isinstance(v, ast.Call) and getattr(v.func, "id", getattr(v.func, "attr", "")) in (
                    "dict", "list", "set", "defaultdict", "deque", "OrderedDict", "Counter", "local", "Queue")
                if isinstance(v, mutable) or call:
                    for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                        if isinstance(t, ast.Name) and t.id not in allowed.get(name, set()):
                            found.setdefault(name, []).append(t.id)
            for d in getattr(node, "decorator_list", []):
                if "cache" in ast.unparse(d) and f"@{ast.unparse(d)} {node.name}" not in allowed.get(name, set()):
                    found.setdefault(name, []).append(f"@{ast.unparse(d)} {node.name}")
    assert not found, f"module-level state (put it on tenant.Tenant, or explain it in `allowed`): {found}"


def test_tenant_tasks(tmp_path, monkeypatch):
    """A tenant's loops run in its task group: one failing is started again on its own (its siblings and the other
    tenant go on), cancelling a tenant's scope stops all of its tasks and none of the other's."""
    a, b = make(tmp_path, "a", "ta"), make(tmp_path, "b", "tb")
    ticks, failed = {"a": 0, "b": 0}, []

    async def loop():  # stands in for a tenant's loops: counts its tenant's ticks
        while True:
            ticks[tenant.T().id] += 1
            await anyio.sleep(0.01)

    async def flaky():
        if not failed:
            failed.append(tenant.T().id)
            raise RuntimeError("a bug, once")
        await anyio.sleep_forever()
    for name in ("pipelines", "summary_worker", "voices_loop", "recap_loop"):
        monkeypatch.setattr(server, name, loop if name == "pipelines" else lambda: anyio.sleep_forever())
    monkeypatch.setattr(server, "status_loop", flaky)
    monkeypatch.setattr(server, "RESTART_S", 0.05)

    async def run():
        async with anyio.create_task_group() as tg:
            for t in (a, b):
                tg.start_soon(server.run_tenant, t)
            await anyio.sleep(0.3)
            assert failed and ticks["a"] > 5 and ticks["b"] > 5, (failed, ticks)
            a.scope.cancel()
            await anyio.sleep(0.05)
            stopped = dict(ticks)
            await anyio.sleep(0.2)
            assert ticks["a"] == stopped["a"] and ticks["b"] > stopped["b"] + 5, "a stopped, b goes on"
            tg.cancel_scope.cancel()
    anyio.run(run)


def test_tenants_file(tmp_path, monkeypatch):
    """tenants.json is who the server serves, the only place with the tokens: mode 600, one id and one token each."""
    f = tmp_path / "tenants.json"
    with pytest.raises(SystemExit, match="no tenants file"):
        tenant.load(f)
    write = lambda ts, mode=0o600: (f.write_text(json.dumps(ts)), f.chmod(mode))
    one = {"id": "main", "token": "x", "data": str(tmp_path / "d"), "recordings": str(tmp_path / "r")}
    write([one], 0o644)
    with pytest.raises(SystemExit, match="chmod 600"):
        tenant.load(f)
    two = {"id": "friend", "token": "y", "data": str(tmp_path / "d2"), "recordings": str(tmp_path / "r2")}
    write([one, two])
    assert [(t.id, t.db) for t in tenant.load(f)] == [("main", tmp_path / "d" / "live.db"), ("friend", tmp_path / "d2" / "live.db")]
    for bad in ([one, {**two, "id": "main"}], [one, {**two, "token": "x"}], [one, {**two, "data": one["data"]}], [one, {**two, "recordings": one["recordings"]}],
                [{**one, "token": ""}, two], []):
        write(bad)
        with pytest.raises(SystemExit, match="one each"):
            tenant.load(f)
    write([{**one, "token": ""}])
    monkeypatch.delenv("HOST", raising=False)
    assert tenant.by_token(tenant.load(f), None).id == "main", "the only tenant without a token: no auth"
    assert tenant.by_token(tenant.load(f), "anything").id == "main"
    monkeypatch.setenv("HOST", "0.0.0.0")  # Docker's: an auth-less server reachable from the network never starts
    with pytest.raises(SystemExit, match="no token, and HOST=0.0.0.0"):
        tenant.load(f)
