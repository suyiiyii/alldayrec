"""The pages' API: the live analyses, the status, the language, days and conversations, search and ask, the MCP
endpoint, recaps and todos, tags, feedback and summaries redone."""
import datetime as dt
import json
import logging

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from alldayrec import ask
from alldayrec import asr
from alldayrec import audio
from alldayrec import db
from alldayrec import live
from alldayrec import local_asr
from alldayrec import mcp_server
from alldayrec import tenant
from alldayrec import threads
from alldayrec.core import conversation_rows, find_conversation, queue_summary, tag_counts
from alldayrec.monitor import status
from alldayrec.summaries import write_recap
from alldayrec.tenant import T
from alldayrec.transcript import merge_drafts

log = logging.getLogger("live")
router = APIRouter()
VOICE_MAX_S = 61  # a spoken question (the page stops at 60 s)


@router.get("/api/events")
async def events():
    return T().events


@router.get("/api/live")
async def live_now():
    """Each device's live analysis of its open conversation."""
    return [live.public(st.live) for st in T().devices.values() if st.live]


@router.post("/api/live/{device}")
async def live_ask(device: str):
    """立即总结 (the live page's button): the device's live analysis now; 409 with why not."""
    st = T().devices.get(device)
    ask = st.ask if st else None  # none until its pipeline's task group is ready
    if not ask:
        raise HTTPException(404, "[no_pipeline] 没有这台设备的处理流程")
    if why := ask():
        raise HTTPException(409, why)
    return {"analyzing": True}


@router.get("/api/health")
async def health():
    """No token and no tenant (Tenants lets it through): for reverse proxies and docker-compose.yml's healthcheck."""
    return {"ok": True}


@router.get("/api/status")
async def status_now():
    return await anyio.to_thread.run_sync(status, limiter=threads.DB)


@router.get("/api/lang")
async def lang_get():
    """What the models write this tenant's summaries, recaps and answers in (tenant.py): {"lang": "zh"} or "en"."""
    return {"lang": T().lang}


@router.put("/api/lang")
async def lang_put(body: dict):
    """The language from the next model call on; existing summaries keep theirs (重新生成 rewrites one). Kept in the
    tenant's store: it outlasts a restart, over tenants.json's lang (or UI_LANG)."""
    if body.get("lang") not in tenant.LANGS:
        raise HTTPException(400, "lang is zh or en")
    db.put_kv("lang", body["lang"])
    T().lang = body["lang"]
    return {"lang": T().lang}


@router.get("/api/sentences")
async def sentences(date: str | None = None):
    day = dt.date.fromisoformat(date) if date else dt.date.today()
    lo, hi = day.isoformat(), (day + dt.timedelta(days=1)).isoformat()
    drafts = db.drafts(lo, hi)
    return merge_drafts(db.sentences(lo, hi), drafts, {d: db.kv(f"finalized_to:{d}") for d in {r.get("source") for r in drafts}})


@router.get("/api/conversations")
async def conversations(date: str | None = None):
    day = dt.date.fromisoformat(date) if date else dt.date.today()
    closed = db.conversations(day.isoformat(), (day + dt.timedelta(days=1)).isoformat())
    return closed + [{**st.conversation, "state": "open"} for st in T().devices.values()
                     if st.conversation and st.conversation["start"][:10] == day.isoformat()]


@router.get("/api/days")
async def days():
    """Days that have sentences, newest first."""
    return [r[0] for r in db.conn().execute("SELECT DISTINCT substr(start, 1, 10) d FROM sentences ORDER BY d DESC")]


@router.get("/api/calendar")
async def calendar():
    """Per day with sentences: closed conversations, their speech minutes and the sentence count, for the day picker
    and the calendar page."""
    out = {d: {"conversations": 0, "speech_min": 0.0, "sentences": n}
           for d, n in db.conn().execute("SELECT substr(start, 1, 10), count(*) FROM sentences GROUP BY 1")}
    for d, n, s in db.conn().execute("SELECT substr(start, 1, 10), count(*), coalesce(sum(speech_s), 0) FROM conversations GROUP BY 1"):
        x = out.setdefault(d, {"conversations": 0, "speech_min": 0.0, "sentences": 0})
        x["conversations"], x["speech_min"] = n, round(s / 60, 1)
    return dict(sorted(out.items(), reverse=True))


@router.get("/api/search")
async def search(q: str = "", tag: str = "", who: str = "", after: str = "", before: str = "", limit: int = 30):
    """db.search; `before` is the start of the last one already shown."""
    return db.search(q, tag, who, after, before, limit)


@router.post("/api/ask")
async def ask_ai(body: dict):
    """A question about my own past answered from this app's data (ask.py), in 10-30 s: {answer, sources, calls, usage}."""
    q = (body.get("q") or "").strip()
    if not q or len(q) > 500:
        raise HTTPException(400, "empty or too long")
    try:
        return await ask.ask(q)
    except Exception as e:  # HTTP errors, timeouts
        log.warning("ask failed: %r", e)
        raise HTTPException(502, f"[llm_failed] 模型调用失败：{e!r}"[:300])


@router.post("/api/ask/voice")
async def ask_voice(request: Request):
    """A question said aloud (the ask sheet's hold-to-talk: what the browser's MediaRecorder wrote, webm, ogg or mp4) as
    text, {text}, for the page to show and ask with /api/ask; "" when nothing was heard. Decoded as an uploaded piece
    (audio.decode), heard by the draft layer's engine when it takes a whole window (fast, text alone; qwen-stream is a
    session), else the final layer's, ENGINE_FALLBACK as ever, each try in the calls table as `voice`. Nothing kept."""
    body = await request.body()
    if not body or len(body) > 4 << 20:
        raise HTTPException(400, "empty or too long")
    x = await anyio.to_thread.run_sync(audio.decode, body, limiter=threads.MEDIA)
    if x is None or len(x) > VOICE_MAX_S * audio.SR:
        raise HTTPException(400, "[bad_audio] 不是一段能听的录音，或超过一分钟")
    engine = asr.DRAFT_ENGINE if asr.DRAFT_ENGINE in asr.PROVIDERS else asr.ENGINE
    try:
        segs = await asr.engine_segs(x, [], engine=engine, kind="voice")
    except Exception as e:  # engine_segs: the last engine's error once the fallback failed too
        log.warning("ask voice failed: %r", e)
        raise HTTPException(502, f"[asr_failed] 语音识别失败：{e!r}"[:300])
    return {"text": local_asr.text([" " + g["text"] for g in segs])}  # sentences as words: CJK joined, English spaced


@router.post("/mcp")
async def mcp(request: Request):
    """Remote MCP for the owner's agents (mcp_server.py), with the same token as every other client: stateless, JSON only."""
    if not mcp_server.origin_ok(request.headers):
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Origin not allowed"}}, 403)
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
    status, out = await anyio.to_thread.run_sync(mcp_server.handle, msg, request.headers, limiter=threads.DB)
    return Response(status_code=202) if out is None else JSONResponse(out, status)


@router.api_route("/mcp", methods=["GET", "DELETE"])
async def mcp_stream():
    """No server-sent stream and no sessions to end (2026-07-28)."""
    return Response(status_code=405, headers={"Allow": "POST"})


@router.get("/api/recap")
async def recap_get(date: str = ""):
    """A day's recap, the newest one when no day is given; null when there is none. Its todos as the due list has them
    (id, the sentence, whom to), a todo marked done or wrong since left out."""
    if date:
        r = db.kv(f"recap:{date}")
    else:
        row = db.conn().execute("SELECT value FROM kv WHERE key LIKE 'recap:%' ORDER BY key DESC LIMIT 1").fetchone()
        r = json.loads(row[0]) if row else None
    if r:
        closed, al, todos = db.closed_todos(), db.aliases(), []
        for x in r["todos"]:
            c, s = db.conversation(x["conv"]), db.summary(x["conv"]) or {}
            found = (x["id"], db.todo(x["id"])) if x.get("id") else db.todo_by_text(x["conv"], x["text"])  # recaps before 10-24 hold no id
            v = {**db.todo_view(c, s, *found, closed, al), **x} if c and found and found[1] else x
            if v.get("id") not in closed:
                todos.append(v)
        r["todos"] = todos
    return r


@router.get("/api/todos/due")
async def todos_due():
    """My todos due today or before, not marked done or wrong, with how stale each is (db.due_todos)."""
    return db.due_todos(dt.date.today().isoformat())


MARKS = ("done", "wrong")


@router.put("/api/todos/{todo_id}/mark")
async def todo_mark(todo_id: str, body: dict):
    """完成 (done) or 不对 (wrong) on one todo, by its id (db.todo_ids); a mark again replaces it."""
    if body.get("mark") not in MARKS:
        raise HTTPException(400, "mark is done or wrong")
    if not db.todo(todo_id):
        raise HTTPException(404, "no such todo")
    db.mark_todos([todo_id], body["mark"])
    return {"id": todo_id, "mark": body["mark"]}


@router.delete("/api/todos/{todo_id}/mark")
async def todo_unmark(todo_id: str):
    """The undo: the todo is open again."""
    db.mark_todos([todo_id], None)
    return {"id": todo_id, "mark": None}


@router.post("/api/todos/marks")
async def todo_marks(body: dict):
    """Many todos at once: {ids, mark}, mark done or wrong, or null to drop their marks (the bulk undo)."""
    ids, mark = body.get("ids"), body.get("mark")
    if not isinstance(ids, list) or not ids or len(ids) > 500 or mark not in (*MARKS, None):
        raise HTTPException(400, "ids is a list of todo ids, mark done, wrong or null")
    if mark and (bad := [i for i in ids if not isinstance(i, str) or not db.todo(i)]):
        raise HTTPException(404, f"no such todo: {bad[:5]}")
    db.mark_todos(ids, mark)
    return {"ids": ids, "mark": mark}


@router.post("/api/recap/{date}")
async def recap_redo(date: str):
    """Write a day's recap again (15-20 s), e.g. after its summaries were redone."""
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "date is YYYY-MM-DD")
    try:
        r = await write_recap(date)
    except Exception as e:
        log.warning("recap %s failed: %r", date, e)
        raise HTTPException(502, f"[llm_failed] 模型调用失败：{e!r}"[:300])
    if not r:
        raise HTTPException(404, "[no_summaries] 那天没有摘要")
    return r


@router.get("/api/tags")
async def tags():
    return tag_counts()


@router.get("/api/conversation/{conv_id}")
async def conversation(conv_id: str):
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    s, closed = db.summary(conv_id), db.closed_todos()
    if s and s.get("todos"):  # each todo with its id and mark, for 完成 / 不对 (/api/todos/{id}/mark)
        s["todos"] = [{**t, "id": i, "mark": closed.get(i)} for i, t in zip(db.todo_ids(conv_id, s["todos"]), s["todos"])]
    return {**c, "summary": s, "timeline": live.public(t) if (t := db.timeline(conv_id)) else None, "feedback": db.feedback(conv_id),
            "rows": conversation_rows(c)}


@router.post("/api/conversation/{conv_id}/feedback")
async def add_feedback(conv_id: str, body: dict):
    """A correction in the owner's words (wrongly greyed as media, a wrong speaker, a bad summary); collected
    and read back in batches to fix the prompt, never acted on one by one."""
    text = (body.get("text") or "").strip()
    if not find_conversation(conv_id):
        raise HTTPException(404, "no such conversation")
    if not text or len(text) > 2000:
        raise HTTPException(400, "empty or too long")
    db.add_feedback(conv_id, text)
    return db.feedback(conv_id)


@router.get("/api/feedback")
async def feedback():
    """Every correction, newest first, with the conversation's time and title for the read-back."""
    return [{**f, "start": (c := find_conversation(f["conv"]) or {}).get("start"), "title": (db.summary(f["conv"]) or {}).get("title")}
            for f in db.feedback()]


@router.post("/api/conversation/{conv_id}/summarize")
async def resummarize(conv_id: str):
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    queue_summary(c)
    return {"status": "pending"}
