"""Each tenant's summaries (a queue and its workers), the names its labels were called, and the daily recap."""
import datetime as dt
import logging
import os

import anyio

from alldayrec import db
from alldayrec import hints
from alldayrec import live
from alldayrec import recap
from alldayrec import speakers
from alldayrec import summary
from alldayrec import ws
from alldayrec.core import all_conversations, conversation_rows, queue_summary, tag_counts
from alldayrec.routes_speakers import make_profile
from alldayrec.tenant import T

log = logging.getLogger("live")


SUMMARY_WORKERS = int(os.environ.get("SUMMARY_WORKERS", "4"))  # 6 drew ~100 429s in a full re-run (09-30); 10 got 429s at once


async def summary_worker():
    """Queued first: conversations closed while the service was down and summaries that never finished. A newer
    prompt does not redo old summaries: the owner redoes one from its page (POST /api/conversation/{id}/summarize)."""
    for c in reversed(all_conversations()):
        s = c["summary"]
        if not s or s["status"] == "pending":
            queue_summary(c)
    async with anyio.create_task_group() as tg:
        for _ in range(SUMMARY_WORKERS):
            tg.start_soon(summarize_loop)


async def summarize(c: dict, tags: list[str]) -> dict:
    """A conversation that closed with a live agent gets its final summary from that agent (the whole conversation
    and the day in its context, the prefix cached); any other, and every redo, from a plain summary call."""
    rows = conversation_rows(c)
    tl = db.timeline(c["id"]) if c.get("via_live") else None
    if tl and tl.get("messages") and (c.get("speech_s", 0) >= summary.MIN_SPEECH_S or tl.get("asked")):  # asked: 立即总结 on a short one
        return {**summary.result(c, rows, *await live.final(tl, rows, tags, c["start"])), "via": "live"}
    return await summary.summarize(c, rows, tags)


async def summarize_loop():
    while True:
        c = await T().summaries[1].receive()
        for delay in (10, 60, 300, None):
            try:
                res = await summarize(c, list(tag_counts())[:60])
                break
            except Exception as e:  # HTTP errors, timeouts, a reply that is not JSON
                log.warning("summary %s failed: %r", c["id"], e)
                if delay == 300:
                    c.pop("via_live", None)  # the last try takes the plain summary
                res = {"conversation": c["id"], "status": "failed", "error": repr(e)[:300]}
                if delay:
                    await anyio.sleep(delay)
        old = db.summary(c["id"])
        if res["status"] == "failed" and old and old["status"] == "done":  # a failed upgrade keeps the old summary
            continue
        media = summary.media_labels(conversation_rows(c), res.get("media_spans") or [])
        with db.tx() as t:
            db.put_summary(res, t)
            if media:  # a redone summary found media: its voices leave the voiceprint bank
                t.executemany("DELETE FROM voices WHERE conv = ? AND label = ?", [(c["id"], m) for m in media])
        if media:
            T().speakers["at"] = 0.0
        ws.note(f"对话 {c['start'][11:19]} 的摘要" + (f"：{res['title']}" if res["status"] == "done" else f"生成失败：{res['error']}"),
             "state" if res["status"] == "done" else "error")
        await ws.broadcast({"type": "summary", **res})
        if res["status"] == "done" and res.get("model") and not db.name_hints(c["id"]):
            await name_hints(c, media)
        if res["status"] == "done":  # a profile kept up to date once it exists
            for p in c.get("participants") or []:
                if p != "我" and not speakers.ANON.fullmatch(p) and db.profile(p):
                    try:
                        await make_profile(p)
                    except Exception as e:
                        log.warning("profile %s failed: %r", p, e)


async def name_hints(c: dict, media: set[str]):
    """What each label was called (hints.py), once per conversation with a summary call (a rename carries them over in
    apply_rename). A failure is logged."""
    if not set(c.get("participants") or []) - {"我", "S?"} - media:
        return
    try:
        h = await hints.hints(c, conversation_rows(c), media)
    except Exception as e:
        log.warning("name hints %s failed: %r", c["id"], e)
        return
    db.put_name_hints(h)
    if h["names"]:
        T().speakers["at"] = 0.0
        ws.note(f"对话 {c['start'][11:19]}：听出 {len(h['names'])} 个称呼，可在对话页确认", "state")


RECAP_AT = os.environ.get("RECAP_AT", "10:00")  # yesterday's recap (recap.py) is written then; empty: never


async def write_recap(day: str) -> dict | None:
    r = await recap.make(day)
    if r:
        db.put_kv(f"recap:{day}", r)
    return r


async def recap_loop():
    """Yesterday's recap at RECAP_AT, or at the start once that has passed and it is missing (a day without summaries
    gets none); a failed one is tried again in an hour."""
    at = dt.time.fromisoformat(RECAP_AT)
    while True:
        now = dt.datetime.now()
        day = (now.date() - dt.timedelta(days=1)).isoformat()
        if now.time() >= at and db.kv(f"recap:{day}") is None:
            try:
                await write_recap(day)
            except Exception as e:  # HTTP errors, timeouts, a reply that is not JSON
                log.warning("recap %s failed: %r", day, e)
                await anyio.sleep(3600)
                continue
        nxt = dt.datetime.combine(now.date() + dt.timedelta(days=now.time() >= at), at)
        await anyio.sleep(max(1.0, (nxt - dt.datetime.now()).total_seconds()))
