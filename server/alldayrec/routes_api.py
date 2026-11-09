"""The pages' API: the live analyses, the status, the language, days and conversations, search and ask, the MCP
endpoint, recaps and todos, tags, feedback and summaries redone."""
import datetime as dt
import json
import logging
import time
import uuid

import anyio
import anyio.to_thread
import numpy as np
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from alldayrec import ask
from alldayrec import asr
from alldayrec import audio
from alldayrec import colocate
from alldayrec import db
from alldayrec import live
from alldayrec import local_asr
from alldayrec import mcp_server
from alldayrec import mcp_links
from alldayrec import qwen_stream
from alldayrec import split
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
VOICE_END_S = 10  # after the page let go, how long the stream has for its last words before the fallback hears it whole


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


@router.post("/api/ask/live/{device}")
async def ask_live(device: str, body: dict, tasks: BackgroundTasks):
    """A question said to the device (its button held, then let go) between `start` and `end`, unix ms on the clock of
    its /api/chunks pieces: {id} at once; its words are taken from the device's live transcript, the answer streamed
    on /ws as type `ask` (ask.live)."""
    try:
        lo, hi = (dt.datetime.fromtimestamp(int(body[k]) / 1000) for k in ("start", "end"))
    except (KeyError, TypeError, ValueError, OverflowError):
        raise HTTPException(400, "start and end are unix ms")
    if not lo < hi <= lo + dt.timedelta(seconds=VOICE_MAX_S):
        raise HTTPException(400, f"[bad_span] 问题要在 {VOICE_MAX_S} 秒以内")
    id = uuid.uuid4().hex[:12]
    tasks.add_task(ask.live, device, id, lo, hi)  # after the answer is sent
    return {"id": id}


@router.post("/api/ask/voice")
async def ask_voice(request: Request):
    """Deprecated: the page streams its PCM on the WebSocket /api/ask/voice/ws since 2026-11; kept for pages cached before.
    A question said aloud (what the browser's MediaRecorder wrote, webm, ogg or mp4; iPhone's mp4 came out as its first
    second alone) as text, {text}, for the page to show and ask with /api/ask; "" when nothing was heard. Decoded as an uploaded piece
    (audio.decode), heard by the draft layer's engine when it takes a whole window (fast, text alone; qwen-stream is a
    session), else the final layer's, ENGINE_FALLBACK as ever, each try in the calls table as `voice`. Nothing kept."""
    body = await request.body()
    if not body or len(body) > 4 << 20:
        raise HTTPException(400, "empty or too long")
    x = await anyio.to_thread.run_sync(audio.decode, body, limiter=threads.MEDIA)
    if x is None or len(x) > VOICE_MAX_S * audio.SR:
        raise HTTPException(400, "[bad_audio] 不是一段能听的录音，或超过一分钟")
    try:
        segs = await asr.engine_segs(x, [], engine=voice_engine(), kind="voice")
    except Exception as e:  # engine_segs: the last engine's error once the fallback failed too
        log.warning("ask voice failed: %r", e)
        raise HTTPException(502, f"[asr_failed] 语音识别失败：{e!r}"[:300])
    return {"text": local_asr.text([" " + g["text"] for g in segs])}  # sentences as words: CJK joined, English spaced


def voice_engine() -> str:
    """Who hears a spoken question whole: the draft layer's engine when it takes a whole window (fast, text alone;
    qwen-stream is a session), else the final layer's; ENGINE_FALLBACK after it as ever (engine_segs)."""
    return asr.DRAFT_ENGINE if asr.DRAFT_ENGINE in asr.PROVIDERS else asr.ENGINE


@router.websocket("/api/ask/voice/ws")
async def ask_voice_ws(ws: WebSocket):
    """A question said aloud as it is said (the ask sheet's hold-to-talk). The page sends its microphone as binary
    messages, 16 kHz mono int16 PCM, little-endian, every 0.1 s or so, then {"type": "end"} once let go; it closes
    the socket to drop the question. Back come {"type": "partial", "text"} as the words grow, then one
    {"type": "final", "text"} ("" when nothing was heard) or {"type": "error", "error"}, and the server closes.
    The end may carry `diag`, how the page's microphone went (held_ms, rate, frames, levels, ...: AskVoice.vue), and
    a page that drops the question may say why in the close reason: both go to the log with the length of the text
    heard (never the text), the reason to the calls table as the error ("cancelled: short 230").
    Heard by Bailian's streaming ASR (qwen_stream) when an engine of this server already sends audio there (qwen,
    qwen-stream), else, and whenever the stream fails or has not ended VOICE_END_S after the end, whole by
    voice_engine. At most VOICE_MAX_S of audio (the rest is dropped, and the question ends there) and VOICE_MAX_S + 5 s
    of socket; a page gone closes the stream at once. Each try in the calls table as `voice`, audio_s the seconds
    received. Nothing kept."""
    await ws.accept()
    cap = VOICE_MAX_S * audio.SR * 2
    pcm, said, out = bytearray(), [""], {}  # the sentences ended, then the one being said
    streams = bool({asr.ENGINE, asr.DRAFT_ENGINE, asr.ENGINE_FALLBACK} & {"qwen", "qwen-stream"})
    tx, rx = anyio.create_memory_object_stream(float("inf"))  # bounded by cap: what the page sends, to the stream
    done = anyio.Event()
    t = time.perf_counter()
    diag = None

    async def stream():
        try:
            async with qwen_stream.Session() as s, anyio.create_task_group() as tg:
                async def feed():
                    async with rx:
                        async for b in rx:
                            await s.send(b)
                    await s.finish()
                tg.start_soon(feed)
                async for g in s.results():
                    said[-1] = g["text"]  # a sentence's begin_time moves as it grows: not a key
                    if g["final"]:
                        said.append("")
                    await ws.send_json({"type": "partial", "text": joined()})
                tg.cancel_scope.cancel()
            out["text"] = joined()
        except WebSocketDisconnect:  # a partial to a page gone: the receive loop logs why
            pass
        except Exception as e:
            out["error"] = threads.first(e)
            log.warning("ask voice stream failed after %.1f s: %r", time.perf_counter() - t, out["error"])
        finally:
            rx.close()  # a failed stream takes no more audio
            done.set()

    joined = lambda: local_asr.text([" " + x for x in said if x])  # sentences as words: CJK joined, English spaced
    async with anyio.create_task_group() as tg:
        if streams:
            tg.start_soon(stream)
        else:
            rx.close()
            done.set()
        try:
            with anyio.move_on_after(VOICE_MAX_S + 5):  # a page that never says end: what it sent is the question
                while len(pcm) < cap:
                    m = await ws.receive()
                    if m["type"] == "websocket.disconnect":
                        raise WebSocketDisconnect(m.get("code", 1000), m.get("reason") or "")
                    if b := m.get("bytes"):
                        b = b[:min(len(b) & ~1, cap - len(pcm))]  # whole samples
                        pcm += b
                        try:
                            tx.send_nowait(b)
                        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                            pass  # the stream failed: the fallback hears it whole
                    elif (end := ended(m.get("text"))) is not None:
                        diag = end.get("diag")
                        break
        except WebSocketDisconnect as e:  # dropped (slid up, Escape, too short) or the page went: no answer, no more audio
            tg.cancel_scope.cancel()
            why = f"cancelled: {e.reason[:60] or f'the page went ({e.code})'}"
            log.info("ask voice %s after %.2f s of audio, %d ms", why, len(pcm) / 2 / audio.SR,
                     round((time.perf_counter() - t) * 1000))
            if streams:
                db.add_call("voice", "qwen-stream", None, round((time.perf_counter() - t) * 1000), why,
                            len(pcm) / 2 / audio.SR, len(pcm) / 2 / audio.SR)
            return
        tx.close()
        with anyio.move_on_after(VOICE_END_S):
            await done.wait()
        if not done.is_set():
            out["error"] = TimeoutError(f"no end {VOICE_END_S} s after the last audio")
            tg.cancel_scope.cancel()
    secs = len(pcm) / 2 / audio.SR
    if streams:
        err = out.get("error")
        db.add_call("voice", "qwen-stream", None, round((time.perf_counter() - t) * 1000), err and asr.failure(err), secs, secs)
    reply = {"type": "final", "text": out.get("text", "")}
    if "text" not in out and pcm:
        try:
            segs = await asr.engine_segs(np.frombuffer(bytes(pcm), np.int16), [], engine=voice_engine(), kind="voice")
            reply["text"] = local_asr.text([" " + g["text"] for g in segs])
        except Exception as e:  # engine_segs: the last engine's error once the fallback failed too
            log.warning("ask voice failed: %r", e)
            reply = {"type": "error", "error": f"[asr_failed] 语音识别失败：{e!r}"[:300]}
    log.info("ask voice: %.2f s of audio, heard %s chars by %s; page %s", secs,
             len(reply["text"]) if "text" in reply else "no", "stream" if "text" in out else "whole",
             json.dumps(diag, ensure_ascii=False)[:800])  # the page's word, quoted: one line whatever it sent
    try:
        await ws.send_json(reply)
        await ws.close()
    except Exception:  # the page went meanwhile
        pass


def ended(text: str | None) -> dict | None:
    """The page's {"type": "end", ...}, else None."""
    try:
        m = json.loads(text or "null")
        return m if m.get("type") == "end" else None
    except (ValueError, AttributeError):
        return None


@router.post("/mcp")
async def mcp(request: Request):
    """Remote MCP, authenticated by a tenant bearer or an MCP-only connection link: stateless, JSON only."""
    if not mcp_server.origin_ok(request.headers):
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Origin not allowed"}}, 403)
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
    args = (mcp_server.handle, msg, request.headers)
    if link_id := request.scope.get("mcp_link_id"):
        args = (mcp_links.handle, link_id, msg, request.headers)
    status, out = await anyio.to_thread.run_sync(*args, limiter=threads.DB)
    return Response(status_code=status) if out is None else JSONResponse(out, status)


@router.api_route("/mcp", methods=["GET", "DELETE"])
async def mcp_stream():
    """No server-sent stream and no sessions to end (2026-07-28)."""
    return Response(status_code=405, headers={"Allow": "POST"})


@router.get("/api/mcp-links")
def mcp_links_list():
    """List this tenant's AI connection links: metadata only, never the secret or its hash."""
    return JSONResponse(mcp_links.listing(), headers={"Cache-Control": "no-store"})


@router.post("/api/mcp-links")
async def mcp_links_create(request: Request):
    """Create a revocable MCP-only credential from JSON {label, can_write?} (can_write: it may mark todos; read only by
    default): returns its path once (201)."""
    if not mcp_server.origin_ok(request.headers):
        raise HTTPException(403, "Origin not allowed")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    label = body.get("label") if isinstance(body, dict) else None
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise HTTPException(400, "Label must contain 1–80 characters")
    if not isinstance(can_write := body.get("can_write", False), bool):
        raise HTTPException(400, "can_write must be true or false")
    try:
        row = await anyio.to_thread.run_sync(mcp_links.create, label.strip(), can_write, limiter=threads.DB)
    except mcp_links.LimitReached:
        raise HTTPException(409, "[mcp_link_limit] Revoke a connection link before creating another (limit 20)")
    return JSONResponse(row, status_code=201, headers={"Cache-Control": "no-store"})


@router.delete("/api/mcp-links/{link_id}")
async def mcp_links_revoke(link_id: str, request: Request):
    """Disable this tenant's link: 204 when calls finish, 202 {pending: true} after 2 s; unknown ids return 404."""
    if not mcp_server.origin_ok(request.headers):
        raise HTTPException(403, "Origin not allowed")
    # Delete before draining: no new execution lease can pass its live check.
    # Default pool: queued MCP calls must not tie revocation to threads.DB.
    if not await anyio.to_thread.run_sync(mcp_links.revoke, link_id):
        raise HTTPException(404, "Link not found")
    done = False
    with anyio.move_on_after(mcp_links.REVOKE_WAIT_S):
        done = await anyio.to_thread.run_sync(mcp_links.drained, link_id, time.monotonic() + mcp_links.REVOKE_WAIT_S,
                                             abandon_on_cancel=True)
    if not done:
        return JSONResponse({"pending": True}, status_code=202, headers={"Cache-Control": "no-store"})
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


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
        closed, al, hints, todos = db.closed_todos(), db.aliases(), db.done_hints(), []
        for x in r["todos"]:
            c, s = db.conversation(x["conv"]), db.summary(x["conv"]) or {}
            ts = s.get("todos") or []
            pairs = list(zip(db.todo_ids(x["conv"], ts), ts))
            # A pinned ID survives rewording, time changes and renames. Legacy unpinned IDs still need a text match.
            matches = [(i, t) for i, t in pairs if x.get("id") == i == t.get("id")]
            if not matches:
                matches = [(i, t) for i, t in pairs if t.get("owner") == "我" and t.get("text") == x["text"]
                           and all(t.get(k) == x[k] for k in ("to", "due") if k in x)]
            if c and len(matches) == 1:
                if matches[0][1].get("owner") != "我":  # a resolved action owned by someone else is not an orphan of mine
                    continue
                v = {**x, **db.todo_view(c, s, *matches[0], closed, al, hints)}
            else:
                v = {**x, "id": None} if x.get("id") else x
            if v.get("id") not in closed:
                todos.append(v)
        r["todos"] = todos
        for h in r.get("highlights") or []:
            if h.get("conv") and (c := db.conversation(h["conv"])):
                h["participants"] = c["participants"]
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
    conv_id = c["id"]
    s, closed = db.summary(conv_id), db.closed_todos()
    if s and s.get("todos"):  # each todo with its id and mark, for 完成 / 不对 (/api/todos/{id}/mark), and a later sentence saying it is done
        hints = db.done_hints()
        s["todos"] = [{**t, "id": i, "mark": closed.get(i), "done_hint": hints.get(i)} for i, t in zip(db.todo_ids(conv_id, s["todos"]), s["todos"])]
    return {**c, "summary": s, "timeline": live.public(t) if (t := db.timeline(conv_id)) else None, "feedback": db.feedback(conv_id),
            "rows": conversation_rows(c), "standby": colocate.stood_by(c.get("device"), c["start"], c["end"])}


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
    return [{**f, "start": (find_conversation(f["conv"]) or {}).get("start"), "title": (db.summary(f["conv"]) or {}).get("title")}
            for f in db.feedback()]


@router.post("/api/split")
async def split_backfill(since: str, apply: bool = False):
    """Split the long conversations started since a day that are not checked yet, one at a time (the backfill script
    split.py). Without apply it only lists the cuts: the model calls are made, nothing is written."""
    try:
        since = dt.date.fromisoformat(since).isoformat()
    except ValueError:
        raise HTTPException(400, "since: YYYY-MM-DD")
    out = []
    for c in db.conversations(since, "9999"):
        revision = db.kv(f"conversation_revision:{c['id']}")
        if split.due(c) and not split.checked(c, revision):
            out.append({"id": c["id"], "start": c["start"], "cuts": await split.check(c, revision, dry=not apply)})
    return out


@router.post("/api/split/recheck")
async def split_recheck(since: str, apply: bool = False):
    """Ask again for the conversations split since a day and merge back the parts whose cut no longer stands (split.py
    --recheck); apply asks the model anew, so the cuts it undoes can differ from a dry run's, but it only ever undoes
    cuts, never adds one. Without apply it only lists them: the model calls are made, nothing is written."""
    try:
        since = dt.date.fromisoformat(since).isoformat()
    except ValueError:
        raise HTTPException(400, "since: YYYY-MM-DD")
    return await split.recheck(since, apply)


@router.post("/api/conversation/{conv_id}/summarize")
async def resummarize(conv_id: str):
    """Write a conversation's summary again with the current prompt (重新生成, the backfill script resummarize.py). The
    old summary stays until the new one is written, and stays if every try fails; the old todos' marks carry over."""
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    queue_summary(c, keep=True)
    return {"status": "pending"}
