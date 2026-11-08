"""Voices: the voiceprints of closed live conversations (voices_loop), and the 「哪个是你？」 card each round of them
may pop (selfvoice.py)."""
import datetime as dt
import logging

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, HTTPException

from alldayrec import db
from alldayrec import routes_audio
from alldayrec import selfvoice
from alldayrec import threads
from alldayrec import ws
from alldayrec.core import all_conversations, conversation_rows, owner_npz, state
from alldayrec.routes_speakers import save_owner, self_voices, speaker_groups
from alldayrec.tenant import T

log = logging.getLogger("live")
router = APIRouter()


async def voices_loop():
    """Voiceprints for the labels of closed live conversations (history ones are uploaded): up to 4 sentences of
    >= 1.5 s, each capped at 15 s, independent of its summary. speech_s is the label's total sentence duration.
    Short voices keep their clips and suggestions; learning 我 still needs ME_MIN_S per click. A conversation without
    a recording here is marked and not retried. Automatic people need enough non-media speech across conversations."""
    while True:
        await anyio.to_thread.run_sync(speaker_groups, limiter=threads.DB)  # warm: the page's first load would otherwise wait seconds
        await anyio.sleep(300)
        if not state.get("embed"):
            continue
        seen, convs = await anyio.to_thread.run_sync(lambda: (db.voiced_conversations(), all_conversations()), limiter=threads.DB)
        since = (dt.date.today() - dt.timedelta(days=3)).isoformat()
        for c in convs:
            revision = db.kv(f"conversation_revision:{c['id']}")
            if c["start"] < since or c["id"] in seen:
                continue
            rows = conversation_rows(c)
            by = {}
            for r in rows:
                if r.get("speaker") and r["speaker"] != "S?" and not r.get("music"):
                    by.setdefault(r["speaker"], []).append(r)
            out = []
            for label, rs in by.items():
                dur = lambda r: (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds()
                long = sorted((r for r in rs if dur(r) >= 1.5), key=dur, reverse=True)[:4]
                es, ls, used = [], [], []
                for r in long:
                    try:
                        x = np.frombuffer(await routes_audio.cut(r["start"], min(r["end"], (dt.datetime.fromisoformat(r["start"]) + dt.timedelta(seconds=15)).isoformat()), "s16le", r.get("source")), np.int16)
                    except HTTPException:
                        continue
                    es.append(await anyio.to_thread.run_sync(state["embed"], x, limiter=threads.INFER))
                    used.append(r)
                    if (lv := selfvoice.level(x)) is not None:
                        ls.append(lv)
                if es:
                    e = np.mean(es, 0)
                    best = min(used, key=lambda r: abs(dur(r) - 6))
                    out.append({"conv": c["id"], "label": label, "device": "live", "n": len(rs), "speech_s": round(sum(dur(r) for r in rs), 1),
                                "k": len(es), "e": e / np.linalg.norm(e), "level": round(float(np.median(ls)), 2) if ls else None,
                                "clip_start": best["start"], "clip_end": min(best["end"], (dt.datetime.fromisoformat(best["start"]) + dt.timedelta(seconds=10)).isoformat()),
                                "clip_text": best.get("text")})
            with db.tx() as t:
                if db.resolve_conversation(c["id"]) != c["id"] or db.kv(f"conversation_revision:{c['id']}") != revision:
                    continue
                db.insert_voices(t, out or [{"conv": c["id"]}])
            T().speakers["at"] = 0.0
        try:
            await self_round()
        except Exception:
            log.exception("self voice round")


# 「哪个是你？」(selfvoice.py): on an instance without 我's voiceprint a card lets the user pick their own voice;
# the pick is written as that voiceprint and the pipelines take it from their next conversation on
CLIP_CONVS = 6  # a candidate's clips come from its biggest conversations: a sentence decoded costs ~5 CPU-s of ReDimNet2


async def self_round():
    """After the voices of closed conversations came in: pop the card when it is due (selfvoice.due). Off while 我 has a
    voiceprint (a hand-made npz, or the one picked here)."""
    st = await anyio.to_thread.run_sync(db.kv, "selfvoice", limiter=threads.DB) or {}
    if owner_npz().exists() or st.get("card"):  # the card up waits for the user; nothing else changes st meanwhile
        return
    vs = await anyio.to_thread.run_sync(self_voices, limiter=threads.DB)
    if not vs:
        return
    now = dt.datetime.now()
    st.setdefault("first_at", now.isoformat(timespec="seconds"))
    cands, n = await anyio.to_thread.run_sync(selfvoice.candidates, vs, limiter=threads.DB)
    if why := selfvoice.due(st, cands, n, now):
        st["card"] = await self_card(cands[:selfvoice.SHOW], vs, why, n)
        st.pop("wait", None)
        log.info("self voice card (%s): %s", why, [(c["p"], c["convs"], len(c["clips"])) for c in st["card"]["candidates"]])
    db.put_kv("selfvoice", st)
    if why:
        ws.note("「哪个是你？」可以确认了：到「对话」页听一听，选出你自己的声音", "state")
        await ws.broadcast({"type": "self"})


async def self_card(cands: list[dict], vs: list[dict], why: str, n: int) -> dict:
    """The card: per candidate its tier, conversations, speech, voiceprint and 3 clips (selfvoice.pick) from its
    biggest conversations, decoded and embedded here (a few minutes of CPU, once per card)."""
    lvl = {(v["conv"], v["label"]): v.get("level") for v in vs}
    banks, out = [c["bank"] for c in cands], []
    for c in cands:
        pool = []
        for m in sorted(c["members"], key=lambda m: -m["speech_s"])[:CLIP_CONVS]:
            if not (conv := db.conversation(m["conv"])):
                continue
            for r in await anyio.to_thread.run_sync(selfvoice.shortlist, conversation_rows(conv), m["label"], limiter=threads.DB):
                try:
                    x = np.frombuffer(await routes_audio.cut(r["start"], r["end"], "s16le", r.get("source")), np.int16)
                except HTTPException:
                    continue
                pool.append({**r, "conv": m["conv"], "lvl": selfvoice.level(x), "lmed": lvl.get((m["conv"], m["label"])),
                             "e": await anyio.to_thread.run_sync(state["embed"], x, limiter=threads.INFER)})
        clips = selfvoice.pick(pool, c["bank"], [b for b in banks if b is not c["bank"]])
        out.append({"tier": selfvoice.tier(c["p"]), "p": round(c["p"], 3), "convs": c["convs"], "speech_s": round(c["speech_s"]),
                    "bank": [float(x) for x in c["bank"]],
                    "clips": [{"start": r["start"], "end": r["end"], "device": r.get("source"), "text": r.get("text")} for r in clips]})
    return {"at": dt.datetime.now().isoformat(timespec="seconds"), "why": why, "convs": n, "candidates": out}


@router.get("/api/self")
async def self_get():
    """The card while it is up: candidates most likely first (none preselected), each with a tier instead of a %."""
    st = db.kv("selfvoice") or {}
    c = None if owner_npz().exists() else st.get("card")
    return {"card": c and {**c, "candidates": [{k: v for k, v in x.items() if k not in ("bank", "p")} for x in c["candidates"]]}}


@router.post("/api/self")
async def self_answer(body: dict):
    """The answer to the card: a candidate's index becomes 我's voiceprint (owner-redimnet2.npz), taken by every
    pipeline from its next conversation on: the conversations before keep their labels and summaries. "none" (都不是)
    and "later" (以后再说) put the card away until selfvoice.due brings it again. Every answer is logged with the card's
    probabilities: what POP_P and the tiers are to be checked against."""
    st = db.kv("selfvoice") or {}
    card, a, now = st.get("card"), body.get("answer"), dt.datetime.now()
    if not card or owner_npz().exists():
        raise HTTPException(409, "[no_pending_voice] 没有待确认的声音，刷新页面再试")
    if a == "none":
        vs = await anyio.to_thread.run_sync(self_voices, limiter=threads.DB)
        st["wait"] = {"convs": len({v["conv"] for v in vs})}
    elif a == "later":
        st["wait"] = {"until": (now + dt.timedelta(seconds=selfvoice.LATER_S)).isoformat(timespec="seconds")}
    elif isinstance(a, int) and not isinstance(a, bool) and 0 <= a < len(card["candidates"]):
        save_owner({"self": selfvoice.unit(card["candidates"][a]["bank"]).astype(np.float32)})
        st["confirmed"] = {"at": now.isoformat(timespec="seconds"), "pick": a}
    else:
        raise HTTPException(400, "bad answer")
    st["log"] = st.get("log", []) + [{"at": now.isoformat(timespec="seconds"), "why": card["why"], "convs": card["convs"],
                                      "p": [x["p"] for x in card["candidates"]], "answer": a}]
    st["card"] = None
    db.put_kv("selfvoice", st)
    if "confirmed" in st:
        ws.note(f"「我」的声音已确认（第 {a + 1} 个）：从下一段对话起认出「我」，已有的对话不变", "state")
    await ws.broadcast({"type": "self"})
    return {"ok": True}
