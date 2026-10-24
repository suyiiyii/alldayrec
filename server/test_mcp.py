"""Remote MCP (/mcp): both protocol eras, the 2026 headers checked, every read tool answering in Markdown, a long
transcript paged by cursor, mark_todo doing what 完成 / 不对 does; over HTTP only with the instance token."""
import datetime as dt
import json
import tempfile
from pathlib import Path

import anyio

import db
import mcp_server as m
import server

TODAY = dt.date.today().isoformat()
CID = "c20261001100000"


def fake():
    t = db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    rows = [dict(id=f"r{i}", start=f"2026-10-01T10:{i // 60:02d}:{i % 60:02d}.000", end=f"2026-10-01T10:{i // 60:02d}:{i % 60:02d}.900",
                 speaker="老王" if i % 2 else "我", text=f"第 {i} 句" + "存储方案细节" * 10, source="phone") for i in range(400)]
    with db.tx() as c:
        db.insert_conversation(c, dict(id=CID, start="2026-10-01T10:00:00.000", end="2026-10-01T10:30:00.000", participants=["我", "老王"],
                                       speech_s=1200, sentences=len(rows), device="phone"))
        db.insert_conversation(c, dict(id="c20261001150000", start="2026-10-01T15:00:00.000", end="2026-10-01T15:05:00.000",
                                       participants=["我"], speech_s=60, sentences=1, kind="口述", device="phone"))
        db.insert_sentences(c, rows + [dict(id="x", start="2026-10-01T10:00:01.000", end="2026-10-01T10:00:02.000", speaker="我",
                                            text="另一台也录到了", source="LR-X1-567E")])
        db.put_summary({"conversation": CID, "status": "done", "prompt_version": 8, "title": "存储方案", "summary": "定了按小时存。",
                        "tags": ["存储"], "people": [{"who": "老王", "points": ["建议按小时存"]}], "decisions": [{"text": "按小时存", "at": None}],
                        "todos": [{"owner": "我", "to": "老王", "text": "改存储", "due": "今天", "due_date": TODAY, "at": "2026-10-01T10:05:00.000",
                                   "quote": "我今天改"}]}, c)
    return t


def rpc(method, **params):
    """A 2025 request (after initialize): no _meta, no mirrored headers."""
    status, out = m.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, {})
    assert status == 200, (status, out)
    return out


def modern(method, headers=None, version="2026-07-28", **params):
    """A 2026-07-28 request: its version in _meta and mirrored in the headers."""
    h = {"mcp-protocol-version": version, "mcp-method": method, **({"mcp-name": params["name"]} if "name" in params else {})}
    return m.handle({"jsonrpc": "2.0", "id": 2, "method": method, "params": {**params, "_meta": {m.PV: version}}}, h if headers is None else headers)


def tool(tool_name, **args):
    r = rpc("tools/call", name=tool_name, arguments=args)["result"]
    return r["content"][0]["text"], r["isError"]


def test_protocol():
    fake()
    init = rpc("initialize", protocolVersion="2025-06-18")["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "alldayrec" and "tools" in init["capabilities"]
    assert rpc("initialize", protocolVersion="1999-01-01")["result"]["protocolVersion"] == "2025-11-25"
    assert m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, {}) == (202, None)
    assert rpc("nope")["error"]["code"] == -32601 and rpc("ping")["result"] == {"resultType": "complete"}
    assert m.handle([], {})[0] == 400 and m.handle({"jsonrpc": "2.0", "id": 1, "method": "x", "params": "x"}, {})[0] == 400

    status, d = modern("server/discover")
    r = d["result"]
    assert status == 200 and r["supportedVersions"][0] == "2026-07-28" and r["resultType"] == "complete" and r["ttlMs"] > 0 and r["cacheScope"]
    status, t = modern("tools/call", name="day", arguments={"date": "2026-10-01"})
    assert status == 200 and not t["result"]["isError"], t
    h = {"mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call", "mcp-name": "due"}
    assert modern("tools/call", h, name="day", arguments={})[1]["error"]["code"] == -32020, "Mcp-Name differs from the body"
    assert modern("tools/list", {"mcp-protocol-version": "2026-07-28"})[0] == 400, "Mcp-Method missing"
    assert modern("tools/list", {"mcp-method": "tools/list"})[0] == 400, "MCP-Protocol-Version missing"
    status, e = m.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, {"mcp-protocol-version": "2026-07-28", "mcp-method": "tools/list"})
    assert status == 400 and e["error"]["code"] == -32020, "a 2026 header with no _meta"
    assert modern("tools/call", {**h, "mcp-name": "=?base64?ZGF5?="}, name="day", arguments={"date": "2026-10-01"})[0] == 200, "base64 name"
    assert modern("tools/call", {**h, "mcp-name": "=?base64?%%%?="}, name="day", arguments={})[0] == 400
    status, e = modern("tools/list", version="2099-01-01")
    assert status == 400 and e["error"]["code"] == -32022 and e["error"]["data"] == {"supported": list(m.VERSIONS), "requested": "2099-01-01"}
    assert modern("nope")[0] == 404 and modern("ping")[0] == 404 and modern("initialize")[0] == 404

    status, ls = modern("tools/list")
    tools = {t["name"]: t for t in ls["result"]["tools"]}
    assert len(tools) == 12 and ls["result"]["cacheScope"] == "public" and ls["result"]["ttlMs"] > 0
    assert all(t["annotations"]["readOnlyHint"] for n, t in tools.items() if n != "mark_todo") and not tools["mark_todo"]["annotations"]["readOnlyHint"]
    assert all(t["name"].isascii() and t["inputSchema"]["type"] == "object" for t in tools.values())
    assert rpc("tools/call", name="ask", arguments={})["error"]["code"] == -32602 and rpc("tools/call", name="day", arguments="x")["error"]["code"] == -32602


def test_tools_and_paging():
    fake()
    text, bad = tool("day", date="2026-10-01")
    assert not bad and "共 2 段，对话 1，口述 1" in text and f"`{CID}` 10-01 10:00-10:30 我、老王 **存储方案**" in text and "待办：改存储" in text, text
    assert tool("day", date="10-01") == ("date 要写成 YYYY-MM-DD，不是 '10-01'", True)
    assert "2026-10：1 天有对话，共 2 段，说话 21 分钟" in tool("calendar")[0]
    assert "10-01 口述 1 段，对话 1 段，说话 21 分钟" in tool("calendar", month="2026-10")[0]
    assert tool("calendar", month="10")[1]
    assert CID in tool("search", q="按小时")[0] and "搜到 0 段" in tool("search", q="按小时", before="2026-10-01")[0]
    assert "搜到 1 段（只列了最新的 1 段；更早的：before 设成 2026-10-01T15:00 再搜）" in tool("search", limit=0)[0], "limit at least 1"
    one, _ = tool("conversation", id=CID)
    assert "## 老王\n- 建议按小时存" in one and "[我 → 老王] 改存储（今天）  原话 10:05：我今天改" in one and "原文 400 句" in one, one
    assert tool("conversation", id="c1")[1] and tool("transcript", id="c1")[1]
    assert "老王 12" not in tool("names")[0] and "老王 1" in tool("names")[0]
    assert "- 10-01 10:00 [我 → 老王] 改存储（今天）" in tool("todos", after="2026-10-01", before="2026-10-02")[0]
    assert "我答应 TA 的\n- 改存储（今天）" in tool("person", name="老王")[0] and tool("person", name="没这人")[1]
    assert "另一台也录到了" in tool("grep", q="另一台")[0] and "找到 0 句" in tool("grep", q="另一台", after="2026-10-02")[0]
    at, _ = tool("transcript_at", date="2026-10-01", start="10:00", end="10:00")
    assert "## phone" in at and "## LR-X1-567E" in at and "原文 61 句" in at, at[:200]
    assert tool("transcript_at", date="2026-10-01", start="10", end="11")[1] and tool("day")[1], "missing / malformed arguments"

    page1, _ = tool("transcript", id=CID)
    assert len(page1) <= m.PAGE + 60 and page1.endswith("下一页：参数不变，加 cursor=\"2\"）") and "[10:00:00] 我: 第 0 句" in page1
    pages, cursor = [page1], "2"
    while cursor:
        text, bad = tool("transcript", id=CID, cursor=cursor)
        assert not bad and len(text) <= m.PAGE + 60
        pages.append(text)
        cursor = str(int(cursor) + 1) if "下一页" in text else ""
    assert pages[-1].endswith("（第 5/5 页，完）") and len(pages) == 5 and all(f"第 {i} 句" in "".join(pages) for i in range(400))
    assert tool("transcript", id=CID, cursor="9")[1] and tool("transcript", id=CID, cursor="x")[1]
    assert m.paged("a" * 20000).count("a") == m.PAGE, "a line longer than a page is cut"
    assert m.paged("短") == "短"


def test_mark_todo():
    fake()
    assert "改存储" in tool("due")[0]
    assert tool("mark_todo", id=CID, text="编的", mark="done")[1] and tool("mark_todo", id=CID, text="改存储", mark="maybe")[1]
    assert tool("mark_todo", id=CID, text="改存储", mark="done") == ("好了，「改存储」标成了完成", False)
    assert list(db.closed_todos().values()) == ["done"] and db.due_todos(TODAY) == [], "the web page's 完成"
    assert tool("mark_todo", id=CID, text="改存储", mark="wrong") == ("这条待办已经标记过了", False) and list(db.closed_todos().values()) == ["done"]
    assert "（已标记）" in tool("conversation", id=CID)[0] and "到期、还没标记的待办 0 条" in tool("due")[0]


def post(body, headers: dict[str, str], method="POST") -> tuple[int, dict | None]:
    """Through the app as an agent calls it, auth middleware included."""
    out, raw = [], json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(x):
        out.append(x)
    anyio.run(server.app, {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
                            "path": "/mcp", "raw_path": b"/mcp", "query_string": b"", "root_path": "",
                            "headers": [(k.lower().encode(), v.encode()) for k, v in {"host": "live.test", **headers}.items()],
                            "client": ("127.0.0.1", 1), "server": ("test", 80)}, receive, send)
    status = next(x for x in out if x["type"] == "http.response.start")["status"]
    body = b"".join(x.get("body", b"") for x in out)
    return status, json.loads(body) if body.startswith(b"{") else None


def test_http_token(monkeypatch):
    t = fake()
    t.token = "s3cret"
    monkeypatch.setattr(server, "TENANTS", [t])
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "due", "arguments": {}}}
    assert post(call, {})[0] == 401, "no token"
    assert post(call, {"Authorization": "Bearer wrong"})[0] == 401
    status, r = post(call, {"Authorization": "Bearer s3cret"})
    assert status == 200 and "改存储" in r["result"]["content"][0]["text"], r
    assert post({"jsonrpc": "2.0", "method": "notifications/initialized"}, {"Authorization": "Bearer s3cret"}) == (202, None)
    assert post(call, {"Authorization": "Bearer s3cret", "Origin": "https://evil.example"})[0] == 403
    assert post(call, {"Authorization": "Bearer s3cret", "Origin": "https://live.test"})[0] == 200, "a page of this host"
    assert post(call, {"Authorization": "Bearer s3cret"}, "GET")[0] == 405 and post(call, {"Authorization": "Bearer s3cret"}, "DELETE")[0] == 405
    assert post(call, {"Authorization": "Bearer s3cret"}, "GET")[0] == 405
    assert post(call, {}, "GET")[0] == 401
