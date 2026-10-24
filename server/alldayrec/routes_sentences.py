"""One sentence of a closed conversation, as a long press on its row offers: starred (收藏, the list on the 我的 page),
or its speaker fixed alone, which the answer's undo takes back like a rename (POST /api/speakers/undo)."""
import datetime as dt
import json
import re

from fastapi import APIRouter, HTTPException

from alldayrec import bank
from alldayrec import db
from alldayrec import speakers
from alldayrec import ws
from alldayrec.conversations import IGNORE
from alldayrec.core import conversation_rows, find_conversation, queue_summary, state
from alldayrec.routes_speakers import apply_rename, enroll_soon, keep_undo
from alldayrec.tenant import T

router = APIRouter()


def sentence(sid: str, conv_id: str | None) -> tuple[dict, dict]:
    """The conversation and its row with this id, the row's rowid with it: its device's row, as two devices may cut a
    row in the same ms."""
    c = find_conversation(conv_id or "")
    if not c:
        raise HTTPException(404, "no such conversation")
    r = db.conn().execute("SELECT rowid, * FROM sentences WHERE id = ? AND start >= ? AND start <= ? AND (? IS NULL OR source = ?)",
                          (sid, c["start"], c["end"], c.get("device"), c.get("device"))).fetchone()
    if not r:
        raise HTTPException(404, "no such sentence")
    return c, dict(r)


@router.get("/api/stars")
async def stars():
    """The starred sentences as they read now (a rename shows), the newest star first, with their conversation's start
    and title. ponytail: a star whose row a replay re-cut (another start, another id) drops out of the list."""
    return [dict(r) for r in db.conn().execute(
        "SELECT s.id, s.conv, s.at, x.start, x.end, x.speaker, x.text, c.start AS conv_start, json_extract(m.body, '$.title') AS title"
        " FROM stars s JOIN conversations c ON c.id = s.conv"
        " JOIN sentences x ON x.id = s.id AND x.start >= c.start AND x.start <= c.end AND (c.device IS NULL OR x.source = c.device)"
        " LEFT JOIN summaries m ON m.conversation = s.conv ORDER BY s.at DESC")]


@router.put("/api/sentences/{sid}/star")
async def star(sid: str, body: dict):
    c, _ = sentence(sid, body.get("conv"))
    db.conn().execute("INSERT OR IGNORE INTO stars VALUES (?, ?, ?)", (sid, c["id"], dt.datetime.now().isoformat(timespec="seconds")))
    return {"starred": True}


@router.delete("/api/sentences/{sid}/star")
async def unstar(sid: str):
    db.conn().execute("DELETE FROM stars WHERE id = ?", (sid,))
    return {"starred": False}


@router.post("/api/sentences/{sid}/speaker")
async def sentence_speaker(sid: str, body: dict):
    """This sentence was said by `to`. The only one of its label: the label renamed (apply_rename: its voice, pins and
    name hints go with it). Else the row alone, and the participants gain `to` when new: only then is the summary
    redone. A named person's voice is learnt from it after the answer (enroll); 我 and S<n> teach nothing here."""
    c, r = sentence(sid, body.get("conv"))
    frm, to = r.get("speaker") or "", (body.get("to") or "").strip()
    if not to or to == "S?" or len(to) > 20 or to == frm:
        raise HTTPException(400, "bad name")
    if frm in c["participants"] and sum(x.get("speaker") == frm for x in conversation_rows(c)) == 1:
        res, rows, before = apply_rename(c["id"], frm, to)
        after = res["participants"]
    else:
        after = c["participants"] + ([to] if to not in c["participants"] and to not in IGNORE else [])
        keys = (f"{c['id']}|{frm}", f"{c['id']}|{to}")
        with db.tx() as t:  # undo_rename's shape: the voices and pins as they are (unchanged), put back as they were
            before = {"conv": c["id"], "from": frm, "to": to, "participants": c["participants"], "after": after, "hints": None,
                      "rows": [r["rowid"]],
                      "voices": [tuple(v) for v in t.execute("SELECT * FROM voices WHERE conv = ? AND label IN (?, ?)", (c["id"], frm, to))],
                      "apart": [tuple(v) for v in t.execute("SELECT voice, grp FROM apart WHERE voice IN (?, ?)", keys)]}
            t.execute("UPDATE sentences SET speaker = ? WHERE rowid = ?", (to, r["rowid"]))
            t.execute("UPDATE conversations SET participants = ? WHERE id = ?", (json.dumps(after, ensure_ascii=False), c["id"]))
            if frm != "我" and not speakers.ANON.fullmatch(frm):  # what it taught the people bank as frm (bank.py)
                before["enrolled"] = bank.forget(t, frm, [r["rowid"]])
        bank.sync(frm)
        if after != c["participants"]:
            T().speakers["at"] = 0.0
            queue_summary(c, keep=True)
        learn = to != "我" and not re.fullmatch(r"S\d+", to) and state.get("embed")
        rows = [{**r, "conv": c["id"]}] if learn and (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds() >= 1.5 else []
        ws.note(f"对话 {c['start'][11:19]}：{r['start'][11:19]} 这一句 {frm} 改为 {to}", "state")
    await enroll_soon(to, rows)
    return {"speaker": to, "participants": after, "voiceprints": len(rows), "undo": keep_undo([before])}
