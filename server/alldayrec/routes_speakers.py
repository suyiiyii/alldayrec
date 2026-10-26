"""Who speaks: a conversation's labels renamed, the speakers page (voices grouped across conversations), aliases, a
person's page and profile, the voiceprints of closed conversations, and the 「哪个是你？」 card."""
import datetime as dt
import json
import logging
import os
import re
import secrets
import shutil
import time

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from alldayrec import bank
from alldayrec import db
from alldayrec import hints
from alldayrec import profiles
from alldayrec import routes_audio
from alldayrec import selfvoice
from alldayrec import speakers
from alldayrec import summary
from alldayrec import threads
from alldayrec import ws
from alldayrec.core import all_conversations, conversation_rows, find_conversation, owner_npz, queue_summary, state
from alldayrec.tenant import T

log = logging.getLogger("live")
router = APIRouter()


@router.get("/api/names")
async def names():
    """Names already in use, most conversations first: suggestions for a rename."""
    return list(db.names())


@router.post("/api/conversation/{conv_id}/rename")
async def rename(conv_id: str, body: dict):
    """One label renamed; with a name the conversation has already, a merge; to 我, its voice learnt too (learn_me).
    The answer's undo takes it back (POST /api/speakers/undo)."""
    frm, to = body.get("from", ""), (body.get("to") or "").strip()
    _, to = db.resolve_voice(conv_id, to)
    conv_id, frm = db.resolve_voice(conv_id, frm)
    me = [voice_of(conv_id, frm)] if to == "我" else []  # before the merge deletes its voice
    r, rows, before = apply_rename(conv_id, frm, to)
    await enroll_soon(to, rows)
    return {**r, **learn_me(to, me, [before]), "undo": keep_undo([before])}


def apply_rename(conv_id: str, frm: str, to: str) -> tuple[dict, list[dict], dict]:
    """One label of one conversation becomes a name: its rows, the participants, its voice and pins change in one
    transaction, and the summary is redone. Also returns up to ENROLL of its sentences to teach the diarist that voice
    for later conversations (enroll_soon: seconds of audio work per sentence, after the answer), and what it changed as
    it was before (undo_rename)."""
    _, to = db.resolve_voice(conv_id, to)
    conv_id, frm = db.resolve_voice(conv_id, frm)
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    if not to or to == "S?" or len(to) > 20 or to == frm or frm == "S?":  # S?: music and odd rows, one at a time only
        raise HTTPException(400, "bad name")
    hit = [r for r in conversation_rows(c) if r.get("speaker") == frm]
    if not hit:
        raise HTTPException(400, f"no sentence of {frm}")
    before = {"conv": conv_id, "from": frm, "participants": c["participants"]}
    c["participants"] = list(dict.fromkeys(to if p == frm else p for p in c["participants"]))
    with db.tx() as t:
        keys = (f"{conv_id}|{frm}", f"{conv_id}|{to}")
        # its device's rows only (conversation_rows): another device recording the same minutes has labels of its own
        before |= {"to": to, "after": c["participants"], "hints": db.name_hints(conv_id).get(conv_id),
                   "rows": [r[0] for r in t.execute("SELECT rowid FROM sentences WHERE start >= ? AND start <= ? AND speaker = ? AND (? IS NULL OR source = ?)",
                                                    (c["start"], c["end"], frm, c.get("device"), c.get("device")))],
                   "voices": [tuple(r) for r in t.execute("SELECT * FROM voices WHERE conv = ? AND label IN (?, ?)", (conv_id, frm, to))],
                   "apart": [tuple(r) for r in t.execute("SELECT voice, grp FROM apart WHERE voice IN (?, ?)", keys)]}
        t.execute(f"UPDATE sentences SET speaker = ? WHERE rowid IN ({','.join('?' * len(before['rows']))})", (to, *before["rows"]))
        t.execute("UPDATE conversations SET participants = ? WHERE id = ?", (json.dumps(c["participants"], ensure_ascii=False), conv_id))
        if t.execute("SELECT 1 FROM voices WHERE conv = ? AND label = ?", (conv_id, to)).fetchone():
            t.execute("DELETE FROM voices WHERE conv = ? AND label = ?", (conv_id, frm))  # a merge: the label's own voice stays
        else:
            t.execute("UPDATE voices SET label = ? WHERE conv = ? AND label = ?", (to, conv_id, frm))  # voices and pins follow
        t.execute("UPDATE OR REPLACE apart SET voice = ? WHERE voice = ?", keys[::-1])
        if h := db.name_hints(conv_id).get(conv_id):  # the names it was called go with it
            h["names"] = [{**x, **{k: to for k in ("who", "by") if x.get(k) == frm}} for x in h["names"]]
            db.put_name_hints(h, t)
        before["todo_labels"] = db.rename_todos(conv_id, frm, to, t)
        if frm != "我" and not speakers.ANON.fullmatch(frm):  # what these sentences taught the people bank as frm
            before["enrolled"] = bank.forget(t, frm, before["rows"])
    if before.get("enrolled"):
        bank.sync(frm)
    T().speakers["at"] = 0.0
    queue_summary(c, keep=True)
    long = []
    # S<n> means "not known": nothing to learn; 我 is learnt into its own voiceprint (learn_me), not the people bank
    if to != "我" and not re.fullmatch(r"S\d+", to) and state.get("embed"):
        long = sorted(({**r, "conv": conv_id} for r in hit if (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds() >= 1.5),
                      key=lambda r: r["start"])[:ENROLL]
    ws.note(f"对话 {c['start'][11:19]}：{frm} 改名为 {to}", "state")
    # voiceprints: the sentences queued to learn from (a history conversation without its recording here gives none)
    return {"participants": c["participants"], "renamed": len(hit), "voiceprints": len(long)}, long, before


ENROLL = 20
UNDO_KEEP = 20  # renames that can be taken back, the newest; in memory: not after a restart
ME_MIN_S = 10.0  # 「这是我」: less speech than this relabels only; a voiceprint from a few words would let others in as 我


def voice_of(conv: str, label: str) -> tuple[np.ndarray, float] | None:
    """A label's voiceprint in one conversation (the voices table: a mean of its longest sentences) and its speech."""
    r = db.conn().execute("SELECT e, speech_s FROM voices WHERE conv = ? AND label = ? AND e IS NOT NULL", (conv, label)).fetchone()
    return (db.vec(r[0]), r[1] or 0.0) if r else None


def save_owner(bank: dict[str, np.ndarray]):
    """我's voiceprint, whole or not at all (a restart loads it like a hand-made one); the pipelines take it from their
    next conversation on (device_pipeline: T().owner). No centroid left: no file, nobody is 我."""
    if bank:
        tmp = T().data / "owner-redimnet2.tmp.npz"
        np.savez(tmp, **bank)
        os.replace(tmp, owner_npz())
    else:
        owner_npz().unlink(missing_ok=True)
    T().owner = bank


def owner_bank() -> dict[str, np.ndarray]:
    if not owner_npz().exists():
        return {}
    with np.load(owner_npz()) as z:
        return dict(z)


def learn_me(to: str, vs: list[tuple[np.ndarray, float] | None], befores: list[dict]) -> dict:
    """「这是我」: voices (voice_of) renamed 我 in one click become one more centroid of 我's voiceprint, their unit mean
    weighted by speech. A centroid, not a mean into the old one: the diarist takes the best of them (Diarist.add), and a
    voice split from 我 is one the old centroids missed (another recorder, another room); a mean would pull the old
    one off the voice it does match. The npz before is kept beside it (.prev); the undo drops the centroid again (any
    order of undos), and so does 不是他 on its voices (forget_me: kv me_centroids holds them, conv|我 each). Less than
    ME_MIN_S of speech with a voiceprint learns nothing. Another name: nothing to say. vs: one per befores."""
    if to != "我":
        return {}
    # a label without one: an open conversation, under 3 sentences, no recording here
    vs, convs = [v for v in vs if v], [b["conv"] for v, b in zip(vs, befores) if v]
    s = sum(x for _, x in vs)
    if s < ME_MIN_S or not befores:
        return {"learned": False}
    bank = owner_bank()
    if bank:
        shutil.copyfile(owner_npz(), owner_npz().with_name(owner_npz().name + ".prev"))
    k = befores[0]["me"] = f"me-{secrets.token_hex(4)}"
    save_owner({**bank, k: selfvoice.unit(sum(x * selfvoice.unit(e) for e, x in vs)).astype(np.float32)})
    db.put_kv("me_centroids", {**(db.kv("me_centroids") or {}), k: [f"{c}|我" for c in convs]})
    ws.note(f"「我」的声音：多记住了一种（{round(s)} 秒），从下一段对话起认出", "state")
    return {"learned": True}


def forget_me(key: str | None = None, voice: str | None = None):
    """Centroids 「这是我」 added to 我's voiceprint taken out again: `key` (its undo), and those left without a voice
    once `voice` (conv|我, 不是他) leaves their list. No centroid left: no npz, nobody is 我."""
    # ponytail: a centroid of several voices keeps the share of one taken out until its last goes; keep each voice's
    # vector and speech with the list and recompute it if a mixed one ever lets others in
    m = {k: [x for x in vs if x != voice] for k, vs in (db.kv("me_centroids") or {}).items()}
    gone = {k for k, vs in m.items() if not vs} | ({key} if key else set())
    db.put_kv("me_centroids", {k: vs for k, vs in m.items() if k not in gone})
    bank = owner_bank()
    if [bank.pop(k) for k in gone if k in bank]:
        save_owner(bank)


def keep_undo(befores: list[dict]) -> str:
    """The id that takes back the renames (apply_rename's befores) of one click."""
    u = T().undo
    u[k := secrets.token_hex(8)] = befores
    while len(u) > UNDO_KEEP:
        del u[next(iter(u))]
    return k


@router.post("/api/speakers/undo")
async def speaker_undo(body: dict):
    """Takes back a rename, merge or 拆开 by the undo id its answer gave: the rows, the participants, the voices, pins
    and name hints as they were, the summaries redone, what the renamed sentences taught the people bank (the ledger,
    bank.py) both ways, the centroid a 「这是我」 added to 我's voiceprint dropped. 409 when the id is unknown (taken back
    already, too old, the server restarted) or a conversation changed since."""
    bs = T().undo.pop(body.get("undo"), None)
    if not bs:
        raise HTTPException(409, "[undo_gone] 这次改名已经不能撤销了")
    with db.tx() as t:
        for b in reversed(bs):  # one conversation's renames in a row: the last first
            if "pins" in b:  # 拆开 of an unnamed card: the pins it added
                t.executemany("DELETE FROM apart WHERE voice = ? AND grp = ?", b["pins"])
                continue
            now = t.execute("SELECT participants FROM conversations WHERE id = ?", (b["conv"],)).fetchone()
            if not now or json.loads(now[0]) != b["after"]:
                raise HTTPException(409, "[undo_gone] 这次改名已经不能撤销了")
            t.execute(f"UPDATE sentences SET speaker = ? WHERE speaker = ? AND rowid IN ({','.join('?' * len(b['rows']))})",
                      (b["from"], b["to"], *b["rows"]))
            if b.get("music"):  # a ♪ row given to someone (routes_sentences): heard as music again
                t.execute(f"UPDATE sentences SET music = 1 WHERE speaker = ? AND rowid IN ({','.join('?' * len(b['music']))})",
                          (b["from"], *b["music"]))
            t.execute("UPDATE conversations SET participants = ? WHERE id = ?", (json.dumps(b["participants"], ensure_ascii=False), b["conv"]))
            t.execute("DELETE FROM voices WHERE conv = ? AND label IN (?, ?)", (b["conv"], b["from"], b["to"]))
            for v in b["voices"]:
                t.execute(f"INSERT INTO voices VALUES ({','.join('?' * len(v))})", v)
            t.execute("DELETE FROM apart WHERE voice IN (?, ?)", (f"{b['conv']}|{b['from']}", f"{b['conv']}|{b['to']}"))
            t.executemany("INSERT INTO apart VALUES (?, ?)", b["apart"])
            if b["hints"]:
                db.put_name_hints(b["hints"], t)
            db.undo_todo_rename(b["conv"], b.get("todo_labels") or {}, b["to"], t)
            bank.forget(t, b["to"], b["rows"])  # learnt already (enroll); a later one skips sentences no longer to's
            bank.restore(t, b.get("enrolled") or [])
    T().speakers["at"] = 0.0
    bank.sync(*{x for b in bs if "to" in b for x in (b["from"], b["to"])})
    if k := bs[0].get("me"):  # 「这是我」's centroid
        forget_me(key=k)
    for cid in dict.fromkeys(b["conv"] for b in bs if "conv" in b):
        queue_summary(find_conversation(cid), keep=True)
    b = bs[0]
    ws.note("撤销拆开" if "pins" in b else f"撤销改名：{b['to']} 改回 {b['from']}" if len(bs) == 1 else
            f"撤销改名：{len(bs)} 段对话的 {b['to']} 改回原来的说话人", "state")
    return {"undone": len(bs)}


async def enroll_soon(to: str, rows: list[dict]):
    """rows (apply_rename's) teach the diarist to's voice in the tenant's tasks, one after another, after the rename's
    answer: a group of 10 conversations took 40 s, and the page waited on all of it (10-24). Without the tenant's tasks
    (a test) here and now."""
    if rows:
        if T().tg:
            T().tg.start_soon(enroll, to, rows, name=f"enroll:{to}")
        else:
            await enroll(to, rows)


async def enroll(to: str, rows: list[dict]):
    """Each row's voiceprint (from the recording, when still here) into to's in the people bank and the diarists: those
    bank.pick keeps (alike within their label and to what to sounds like already, at most CAP a click), recorded each in
    the ledger (bank.learn) so that an undo or 不是 takes them back out; a row heard as music (music.py) teaches nothing.
    A failure is logged: it must not end the tenant's tasks beside it."""
    try:
        labels = {}  # conv: [(row, voiceprint)]; rows of one rename are one conversation's label
        for r in rows:
            if r.get("music"):
                continue
            try:
                x = np.frombuffer(await routes_audio.cut(r["start"], min(r["end"], (dt.datetime.fromisoformat(r["start"]) + dt.timedelta(seconds=15)).isoformat()), "s16le", r.get("source")), np.int16)
            except HTTPException:  # history conversations have no recording here
                continue
            labels.setdefault(r.get("conv"), []).append((r, await anyio.to_thread.run_sync(state["embed"], x, limiter=threads.INFER)))
        if labels:
            ls = list(labels.items())
            had = db.people().get(to)
            keep = bank.pick([[e for _, e in l] for _, l in ls], had and had[1])
            with db.tx() as t:
                n = sum(bank.learn(t, to, conv, [l[j] for i2, j in keep if i2 == i]) for i, (conv, l) in enumerate(ls))
            bank.sync(to)
            ws.note(f"{to}：记住了 {n} 句的声纹" + (f"，{k} 句没用上" if (k := sum(map(len, labels.values())) - n) else ""), "state")
    except Exception:
        log.exception("enroll %s", to)

# speakers page: one voiceprint per conversation x label (history ones computed from the old recorder's audio and
# uploaded with a clip each; live ones computed here), grouped across conversations by speakers.py
SPEAKER_LINK = float(os.environ.get("SPEAKER_LINK", "0.5"))  # cosine (device-centred) to join an anonymous group
SPEAKER_SUGGEST = float(os.environ.get("SPEAKER_SUGGEST", "0.6"))  # cosine to offer an anonymous group to a name
# cosine (device-centred) to offer merging two labels of one conversation: in the history's named voices 0.4 % of
# two people's pairs reach 0.45, and 36 % of one person's (10-05)
MERGE_SUGGEST = float(os.environ.get("MERGE_SUGGEST", "0.45"))
# an unnamed group with less speech or in fewer conversations than these is folded into 其他声音 on the speakers page
# (#295, one instance: 679 cards -> 99, 85 % of the unnamed speech still on cards; the folded are passers-by in
# crowded talks and media played aloud)
OTHERS_MIN_S, OTHERS_MIN_CONVS = 180, 2


def conv_start(conv_id: str) -> str:
    x = conv_id[1:]
    return f"{x[:4]}-{x[4:6]}-{x[6:8]}T{x[8:10]}:{x[10:12]}:{x[12:14]}"


def speaker_groups() -> list[dict]:
    """Takes seconds (every conversation and voice): kept 10 min, redone after a rename or new voices."""
    _speakers = T().speakers
    if _speakers["groups"] is not None and time.time() - _speakers["at"] < 600:
        return _speakers["groups"]
    convs = {c["id"]: c for c in all_conversations()}
    al, called = db.aliases(), {}
    for conv, h in db.name_hints().items():
        for x in hints.shown(h, (convs.get(conv) or {}).get("summary")):
            called.setdefault(f"{conv}|{x['who']}", set()).add(hints.canon(x["name"], al))
    vs, out = db.voices(), []
    for g in speakers.speakers(vs, SPEAKER_LINK, SPEAKER_SUGGEST, db.apart()):
        out.append({"id": g["id"], "name": g["name"], **describe(g["members"], convs, called),
                    "suggested": [{"id": speakers.key(s["members"][0]), "score": s["score"], **describe(s["members"], convs, called)}
                                  for s in g["suggested"]]})
        out[-1]["minor"] = not g["name"] and (out[-1]["speech_s"] < OTHERS_MIN_S or out[-1]["count"] < OTHERS_MIN_CONVS)
    order = lambda g: (g["name"] is not None, g["name"] == "我", -g["count"])  # unnamed and frequent first, 我 last
    out.sort(key=order)
    _speakers.update(at=time.time(), groups=out, vec=speakers.centered(vs))
    return out


def describe(members: list[dict], convs: dict, called: dict[str, set[str]]) -> dict:
    ms = []
    for v in members:
        c = convs.get(v["conv"]) or {}
        s = (c.get("summary") or {})
        pts = next((p.get("points") or [] for p in s.get("people") or [] if p.get("who") == v["label"]), [])
        ms.append({"conv": v["conv"], "label": v["label"], "device": v.get("device"), "start": conv_start(v["conv"]), "n": v["n"], "speech_s": v["speech_s"],
                   "clip": v.get("clip"), "clip_start": v.get("clip_start"), "clip_end": v.get("clip_end"), "clip_text": v.get("clip_text"),
                   "title": s.get("title"), "points": pts[:2],
                   "with": [p for p in c.get("participants") or [] if p != v["label"]]})
    ms.sort(key=lambda m: m["start"], reverse=True)
    co, names = {}, {}
    for m in ms:
        for p in m["with"]:
            if not speakers.ANON.fullmatch(p):
                co[p] = co.get(p, 0) + 1
        for n in called.get(f"{m['conv']}|{m['label']}", ()):
            names.setdefault(n, []).append(m["conv"])
    return {"count": len(ms), "speech_s": round(sum(m["speech_s"] for m in ms), 1), "last": ms[0]["start"] if ms else None,
            "samples": ms[:: max(1, len(ms) // 4)][:4],  # spread over time: a group that mixes people sounds mixed
            "co": sorted(co, key=lambda k: -co[k])[:4], "members": ms,
            # what its voices were called (hints.py, aliases resolved), in the most conversations first
            "called": [{"name": n, "convs": cs} for n, cs in sorted(names.items(), key=lambda kv: -len(kv[1]))][:5]}


@router.get("/api/speakers")
async def speaker_list(others: bool = False):
    """The speakers page's cards of people (named, or OTHERS_MIN_S of speech over OTHERS_MIN_CONVS conversations) and
    how many other voices there are; ?others=1 the cards of those. Cards as the page shows them (the home uplink is slow:
    whole groups were 706 KB on one instance); GET /api/speakers/{id} has a group whole, its member voices with it."""
    gs = await anyio.to_thread.run_sync(speaker_groups, limiter=threads.DB)
    return {"cards": [{**card(g), "id": g["id"], "name": g["name"], "suggested": [{**card(x), "id": x["id"], "score": x["score"]} for x in g["suggested"]]}
                      for g in gs if g["minor"] == others],
            "others": sum(g["minor"] for g in gs)}


SAMPLE = ("conv", "label", "start", "clip", "clip_start", "clip_end", "clip_text", "title")


def card(g: dict) -> dict:
    """A group's card: its samples with their clip and first point, what its voices were called and in how many
    conversations."""
    return {"count": g["count"], "speech_s": g["speech_s"], "last": g["last"], "co": g["co"],
            "samples": [{k: v[k] for k in SAMPLE if v[k] is not None} | {"points": v["points"][:1]} for v in g["samples"]],
            "called": [{"name": x["name"], "n": len(x["convs"])} for x in g["called"]]}


def find_group(gid: str, sid: str | None = None) -> dict:
    gid = canonical_voice_key(gid)
    sid = canonical_voice_key(sid) if sid else sid
    g = next((g for g in speaker_groups() if g["id"] == gid), None)
    if g and sid:
        g = next((x for x in g["suggested"] if x["id"] == sid), None)
    if not g:
        raise HTTPException(409, "[speakers_changed] 说话人分组已经变了，刷新页面再试")
    return g


@router.get("/api/speakers/{gid}")
async def speaker_one(gid: str):
    return await anyio.to_thread.run_sync(find_group, gid, limiter=threads.DB)


def check_name(to: str):
    if not to or to == "S?" or len(to) > 20 or speakers.ANON.fullmatch(to):
        raise HTTPException(400, "bad name")


def voice_key(m: dict) -> str:
    return f"{m['conv']}|{m['label']}"


def canonical_voice_key(key: str) -> str:
    if "|" not in key:
        return key
    conv, label = db.resolve_voice(*key.split("|", 1))
    return f"{conv}|{label}"


def chosen(g: dict, body: dict) -> list[dict]:
    """The voices of a card a request picks (naming a half, 拆开): voices, a list of conv|label. Another way to pick
    (say, the voices heard as music) is another key of the body here."""
    want = {canonical_voice_key(k) for k in body.get("voices") or []}
    return [m for m in g["members"] if voice_key(m) in want]


def half(ms: list[dict]) -> dict:
    """One half of a card that sounds like two people: its voices (conv|label), how many and how much speech, and 3
    of them spread over time to listen to (each a clip as the card's samples)."""
    return {"voices": [voice_key(m) for m in ms], "count": len(ms), "speech_s": round(sum(m["speech_s"] for m in ms), 1),
            "samples": [{k: m[k] for k in SAMPLE if m[k] is not None} for m in ms[:: max(1, len(ms) // 3)][:3]]}


def two_halves(g: dict) -> dict:
    h = bank.halves(g["members"], T().speakers["vec"])
    return {"mixed": bool(h), "halves": [half(x) for x in h or []]}


@router.get("/api/speakers/{gid}/halves")
async def speaker_halves(gid: str):
    """The two-half test of a card (bank.halves): mixed, and its two halves when it sounds like two people."""
    return two_halves(await anyio.to_thread.run_sync(find_group, gid, limiter=threads.DB))


@router.post("/api/speakers/name")
async def speaker_name(body: dict):
    """Every member voice of a group (or of one of its suggestions) takes the name, in all of its conversations; with
    voices (conv|label), only those. A card of bank.MIXED_MIN voices or more that sounds like two people (bank.halves)
    is not named: the answer is {mixed: true, halves} for the page to ask, unless all is true. The answer comes when the
    store has it; the voice is learnt after (enroll_soon), 我's before it (learn_me)."""
    to = (body.get("name") or "").strip()
    check_name(to)
    g = await anyio.to_thread.run_sync(find_group, body["group"], body.get("suggestion"), limiter=threads.DB)
    ms = g["members"]
    if body.get("voices"):
        ms = chosen(g, body)
    elif not body.get("all") and (h := two_halves(g))["mixed"]:
        return h
    rows, befores, me = [], [], []
    for m in ms:
        if m["label"] == to:
            continue
        v = voice_of(m["conv"], m["label"]) if to == "我" else None  # before the merge deletes it
        try:
            _, more, before = apply_rename(m["conv"], m["label"], to)
        except HTTPException as e:  # the conversation changed since the page loaded
            log.warning("speaker name %s %s: %s", m["conv"], m["label"], e.detail)
            continue
        rows += more
        befores.append(before)
        me.append(v)
    await enroll_soon(to, rows)
    return {"renamed": len(befores), **learn_me(to, me, befores), "undo": keep_undo(befores) if befores else None}


@router.post("/api/speakers/detach")
async def speaker_detach(body: dict):
    """A voice that is not this speaker: a named one goes back to a fresh S<n> of its conversation; either way it is
    kept apart from the group from now on. Out of 我, the centroid a 「这是我」 learnt from it alone goes too."""
    conv, label, group = body["conv"], body["label"], body["group"]
    conv, label = db.resolve_voice(conv, label)
    group = canonical_voice_key(group)
    key = f"{conv}|{label}"
    if not speakers.ANON.fullmatch(label):
        new = unnamed(conv, label)[0]
        forget_me(voice=key)
        key = f"{conv}|{new}"
    db.conn().execute("INSERT OR IGNORE INTO apart VALUES (?, ?)", (key, group))
    T().speakers["at"] = 0.0
    return {"key": key}


def unnamed(conv: str, label: str) -> tuple[str, dict]:
    """A named label of a conversation back to a fresh S<n> of it (S<n>: nothing to learn), and apply_rename's before."""
    c = find_conversation(conv)
    if not c:
        raise HTTPException(404, "no such conversation")
    used = {int(p[1:]) for p in c["participants"] if re.fullmatch(r"S\d+", p)}
    new = f"S{max(used, default=0) + 1}"
    return new, apply_rename(conv, label, new)[2]


@router.post("/api/speakers/{gid}/split")
async def speaker_split(gid: str, body: dict):
    """拆开: the voices of a card a request picks (chosen: a half from the two-half test) out of it into a card of
    their own, in one transaction, with an undo. A named card's go back to a fresh S<n> each (what they taught the
    people bank going with them), kept apart from the name; an unnamed card's half without the card's leader (its id:
    pins are against it) is kept apart from it."""
    g = await anyio.to_thread.run_sync(find_group, gid, limiter=threads.DB)
    ms = chosen(g, body)
    if not ms or len(ms) == len(g["members"]):
        raise HTTPException(400, "bad voices")
    befores = []
    with db.tx() as t:
        if g["name"]:
            for m in ms:
                new, before = unnamed(m["conv"], m["label"])
                t.execute("INSERT OR IGNORE INTO apart VALUES (?, ?)", (f"{m['conv']}|{new}", g["name"]))
                befores.append(before)
        else:
            want = {voice_key(m) for m in ms}
            out = ms if gid not in want else [m for m in g["members"] if voice_key(m) not in want]
            pins = [(voice_key(m), gid) for m in out if not t.execute("SELECT 1 FROM apart WHERE voice = ? AND grp = ?", (voice_key(m), gid)).fetchone()]
            t.executemany("INSERT INTO apart VALUES (?, ?)", pins)
            befores.append({"pins": pins})
            ms = out
    T().speakers["at"] = 0.0
    if g["name"]:
        bank.sync(g["name"])
        if g["name"] == "我":
            for m in ms:
                forget_me(voice=voice_key(m))
    ws.note(f"拆开「{g['name'] or gid}」：{len(ms)} 段对话的声音分出去了", "state")
    return {"moved": len(ms), "undo": keep_undo(befores)}


def label_suggestions(c: dict, groups: list[dict], vec: dict, hs: dict | None, al: dict[str, str]) -> dict:
    """What the page offers for one conversation's labels, each applied only by a click (a rename): names it was called
    here (hints.py; a named label called by another name offers that name as an alias), two labels to merge (voices
    alike, or both called the same), and per unnamed label the named speaker its voice is near and the names its
    voice was called in other conversations."""
    cid, rows = c["id"], conversation_rows(c)
    n = {}
    for r in rows:
        n[r.get("speaker")] = n.get(r.get("speaker"), 0) + 1
    labels = [p for p in c["participants"] if p != "S?" and p in n]
    anon = lambda p: bool(speakers.ANON.fullmatch(p))
    names = [{**x, "canon": hints.canon(x["name"], al)} for x in hints.shown(hs, db.summary(cid)) if x["who"] in labels]
    names = [x for x in names if x["canon"] != x["who"]]  # a name it already has
    merge, seen = [], set()
    def offer(a, b, **why):
        """One person in two labels: the unnamed one into the named one, else the one with fewer rows into the other;
        two names were told apart by the owner. A label is offered into one other only, the first (most alike)."""
        if not anon(a) and not anon(b):
            return
        frm, to = (a, b) if anon(a) and (not anon(b) or n[a] < n[b]) else (b, a)
        if frm not in seen:
            seen.add(frm)
            merge.append({"from": frm, "to": to, **why})
    for score, a, b in speakers.close_pairs([f"{cid}|{p}" for p in labels], vec, MERGE_SUGGEST):
        offer(a.split("|", 1)[1], b.split("|", 1)[1], score=round(score, 2))
    by_name = {}
    for x in names:
        if anon(x["who"]):
            by_name.setdefault(x["canon"], set()).add(x["who"])
    for name, ls in by_name.items():
        ls = sorted(ls, key=lambda p: -n[p])
        for p in ls[1:]:
            offer(p, ls[0], name=name)
    where = {}  # voice key: (its group, the suggestion of a named group it is in)
    for g in groups:
        for m in g["members"]:
            where[f"{m['conv']}|{m['label']}"] = (g, None)
        for sg in g["suggested"]:
            for m in sg["members"]:
                where[f"{m['conv']}|{m['label']}"] = (g, sg)
    like, elsewhere = [], []
    for p in filter(anon, labels):
        g, sg = where.get(f"{cid}|{p}", (None, None))
        if sg:
            like.append({"who": p, "name": g["name"], "score": sg["score"]})
        for x in (sg or g or {}).get("called", []):
            if len(others := {v for v in x["convs"] if v != cid}) >= 2 and x["name"] != g["name"] and \
                    not any(y["who"] == p and y["canon"] == x["name"] for y in names):  # one other is too often a mixed group
                elsewhere.append({"who": p, "name": x["name"], "convs": len(others)})
    clips = {r[0]: {"clip": r[1], "clip_start": r[2], "clip_end": r[3]} for r in db.conn().execute(
        "SELECT label, clip, clip_start, clip_end FROM voices WHERE conv = ? AND e IS NOT NULL", (cid,))}  # to hear a label
    return {"names": names, "merge": merge, "like": like, "elsewhere": elsewhere, "clips": clips, "asked": hs is not None}


@router.get("/api/conversation/{conv_id}/speakers")
async def conversation_speakers(conv_id: str):
    conv_id = db.resolve_conversation(conv_id)
    c = find_conversation(conv_id)
    if not c:
        raise HTTPException(404, "no such conversation")
    groups = await anyio.to_thread.run_sync(speaker_groups, limiter=threads.DB)
    return label_suggestions(c, groups, T().speakers["vec"], db.name_hints(conv_id).get(conv_id), db.aliases())


@router.get("/api/aliases")
async def alias_list():
    return db.aliases()


@router.post("/api/aliases")
async def alias_set(body: dict):
    """Another way a person is called (A哥 for A): hints with it suggest the name. No name drops the alias."""
    alias, name = (body.get("alias") or "").strip(), (body.get("name") or "").strip()
    if not alias or len(alias) > 20 or alias == name:
        raise HTTPException(400, "bad alias")
    if name:
        check_name(name)
        db.conn().execute("INSERT OR REPLACE INTO aliases VALUES (?, ?)", (alias, name))
    else:
        db.conn().execute("DELETE FROM aliases WHERE alias = ?", (alias,))
    T().speakers["at"] = 0.0
    return db.aliases()


@router.get("/api/person/{name}")
async def person(name: str):
    """Everything about one named speaker: their conversations newest first with what they said there, the todos they
    own, and what I promised them."""
    convs = [c for c in all_conversations() if name in (c.get("participants") or [])]
    out, co, tags, theirs, mine = [], {}, {}, [], []
    for c in convs:
        s = c["summary"] if (c["summary"] or {}).get("status") == "done" else {}
        pts = next((p.get("points") or [] for p in s.get("people") or [] if p.get("who") == name), [])
        out.append({"id": c["id"], "start": c["start"], "end": c["end"], "title": s.get("title"), "summary": s.get("summary"),
                    "points": pts, "participants": c["participants"], "speech_s": c.get("speech_s")})
        for p in c["participants"]:
            if p != name and not speakers.ANON.fullmatch(p):
                co[p] = co.get(p, 0) + 1
        for t in s.get("tags") or []:
            tags[t] = tags.get(t, 0) + 1
        for t in s.get("todos") or []:
            t = {**t, "conversation": c["id"], "title": s.get("title"), "start": c["start"]}
            if t.get("owner") == name:
                theirs.append(t)
            elif t.get("owner") == "我" and t.get("to") == name:  # to: prompt v4 on; older summaries have none
                mine.append(t)
    if not convs:
        raise HTTPException(404, "no conversation with this name")
    top = lambda d, n: sorted(d, key=lambda k: -d[k])[:n]
    pf = db.profile(name)
    return {"name": name, "count": len(convs), "first": convs[-1]["start"], "last": convs[0]["start"],
            "aliases": sorted(a for a, n in db.aliases().items() if n == name),
            "profile": pf, "profile_newer": sum(c["start"] > pf["upto"] for c in convs) if pf and pf.get("upto") else 0,
            "with": top(co, 6), "tags": top(tags, 8), "theirs": theirs, "mine": mine, "conversations": out}


def mentions(name: str, limit: int = 30, per_conv: int = 3) -> list[dict]:
    """Rows of other speakers holding the name or one of its aliases, newest first, each with two rows either side and
    its conversation; at most per_conv from one conversation (a name said over and over in one talk)."""
    terms = [name] + [a for a, n in db.aliases().items() if n == name]
    out, per = [], {}
    for r in db.conn().execute(f"SELECT start, source FROM sentences WHERE ({' OR '.join('instr(text, ?)' for _ in terms)}) "
                               "AND coalesce(speaker, '') != ? ORDER BY start DESC", (*terms, name)):
        c = db.conn().execute("SELECT c.id, s.body FROM conversations c LEFT JOIN summaries s ON s.conversation = c.id WHERE c.start <= ? "
                              "AND c.end >= ? AND (c.device IS NULL OR c.device IS ?) ORDER BY c.start DESC", (r[0], r[0], r[1])).fetchone()
        if not c or per.get(c[0], 0) >= per_conv:
            continue
        per[c[0]] = per.get(c[0], 0) + 1
        t = dt.datetime.fromisoformat(r[0])
        near = db.sentences((t - dt.timedelta(minutes=1)).isoformat(), (t + dt.timedelta(minutes=1)).isoformat(), source=r[1])
        i = next((k for k, x in enumerate(near) if x["start"] == r[0]), 0)
        out.append({"conv": c[0], "title": (json.loads(c[1]) if c[1] else {}).get("title"), "at": r[0],
                    "rows": [x for x in near[max(0, i - 2):i + 3] if x.get("text")]})
        if len(out) == limit:
            break
    return out


async def make_profile(name: str) -> dict:
    """The person's profile from their 40 latest conversations, their todos and mine to them, and 30 mentions."""
    p = await person(name)
    ms = await anyio.to_thread.run_sync(mentions, name, limiter=threads.DB)
    pf = await profiles.profile(name, p["conversations"][:40], (p["theirs"] + p["mine"])[:30], ms)
    db.put_profile(pf)
    return pf


@router.post("/api/person/{name}/profile")
async def person_profile(name: str):
    """Made (or made again) now: one chat call, some seconds."""
    try:
        return await make_profile(name)
    except HTTPException:
        raise
    except Exception as e:  # the model: HTTP errors, a reply that is not JSON
        raise HTTPException(502, f"[llm_failed] 生成失败：{e!r}"[:300])


@router.get("/api/voice-clip/{name}")
async def voice_clip(name: str):
    p = T().data / "voices" / "clips" / name  # the history voices' samples
    if not re.fullmatch(r"[\w.-]+\.ogg", name) or not p.is_file():
        raise HTTPException(404, "no such clip")
    return FileResponse(p, media_type="audio/ogg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def voices_loop():
    """Voiceprints for the labels of closed live conversations (history ones are uploaded): up to 4 sentences of
    >= 1.5 s per label with >= 3 sentences. A conversation without a recording here is marked and not retried."""
    while True:
        await anyio.to_thread.run_sync(speaker_groups, limiter=threads.DB)  # warm: the page's first load would otherwise wait seconds
        await anyio.sleep(300)
        if not state.get("embed"):
            continue
        seen, convs = await anyio.to_thread.run_sync(lambda: (db.voiced_conversations(), all_conversations()), limiter=threads.DB)
        since = (dt.date.today() - dt.timedelta(days=3)).isoformat()
        for c in convs:
            revision = db.kv(f"conversation_revision:{c['id']}")
            s = c["summary"]
            if c["start"] < since or c["id"] in seen or not s or s["status"] == "pending":  # the summary tells media apart
                continue
            rows = conversation_rows(c)
            media = summary.media_labels(rows, s.get("media_spans") or [])
            by = {}
            for r in rows:
                if r.get("speaker") and r["speaker"] != "S?" and r["speaker"] not in media and not r.get("music"):
                    by.setdefault(r["speaker"], []).append(r)
            out = []
            for label, rs in by.items():
                if len(rs) < 3:
                    continue
                dur = lambda r: (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds()
                long = sorted((r for r in rs if dur(r) >= 1.5), key=dur, reverse=True)[:4]
                es, ls = [], []
                for r in long:
                    try:
                        x = np.frombuffer(await routes_audio.cut(r["start"], min(r["end"], (dt.datetime.fromisoformat(r["start"]) + dt.timedelta(seconds=15)).isoformat()), "s16le", r.get("source")), np.int16)
                    except HTTPException:
                        continue
                    es.append(await anyio.to_thread.run_sync(state["embed"], x, limiter=threads.INFER))
                    if (lv := selfvoice.level(x)) is not None:
                        ls.append(lv)
                if es:
                    e = np.mean(es, 0)
                    best = min(long, key=lambda r: abs(dur(r) - 6))
                    out.append({"conv": c["id"], "label": label, "device": "live", "n": len(rs), "speech_s": round(sum(map(dur, rs)), 1),
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
    vs = db.voices()
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
        st["wait"] = {"convs": len({v["conv"] for v in db.voices()})}
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
