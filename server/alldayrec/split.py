"""Split a long conversation where an independent one begins. The Grouper closes a conversation only after 5 quiet
minutes, so voices around (a mall, a bus) can chain two unrelated talks into one. Before a conversation of at least
SPLIT_MIN_S is summarized, one chat call (reasoning_effort high) names where new ones begin; a cut stands only at a
real pause between two parts with enough speech each. Any error means no split and the usual summary.

    SPLIT_MIN_S   a conversation this many seconds long (end - start) is checked; 0 turns splitting off (600)

The first part keeps the conversation's id; later parts get their own (late_merge.record). References that belong to a
later part move with it: todos and their marks, stars, name hints, voices, profile and recap citations; feedback and
enrolled samples stay with the first part, and so does the live analysis's timeline. Each cut is stored as a wall
(table walls) that late audio never merges across.

Backfill on the running server (POST /api/split), one conversation at a time:

    cd server && uv run python -m alldayrec.split --since 2026-10-20 [--apply]

Run it on the server's host against its loopback address (LIVE_RECORD_URL=http://127.0.0.1:8770): a proxy in front
cuts a request this long. LIVE_RECORD_TOKEN (else stdin's first line) is the token. Without --apply it is a dry
run: it still makes the model calls (kind split in the calls table) but writes nothing. It prints each split's cut
times and scene words, never transcript text. --apply splits each conversation in its own transaction and queues the
parts' summaries.
"""
import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request

from alldayrec import db, late_merge, late_store, summary, ws
from alldayrec.conversations import IGNORE
from alldayrec.core import conversation_rows, enqueue_summary
from alldayrec.tenant import T

log = logging.getLogger("live")
P = dt.datetime.fromisoformat

SPLIT_MIN_S = int(os.environ.get("SPLIT_MIN_S", "600"))
# On 31 hand-labelled conversations (10-27) the model alone cut 16-41 times where it should not; reasoning effort high,
# moving each cut to the nearest pause of >= 60 s within 5 min (none: no cut) and >= 120 s of speech in every part
# took that to 0-1 at a recall of 0.8.
PAUSE_S, REACH_S, PART_S = 60, 300, 120
KINDS = ("交谈", "通话", "会议", "独自做事", "媒体或背景")

SYSTEM = """你在检查一段现场录音转写。它是按静默时间自动切出来的一段「对话」：只要中间没有连续 5 分钟安静就不会切开，所以在商场、路上、公交上，周围的人声会把几段本来互不相干的对话连成一段。
每行是「[时间] 说话人: 文本」。说话人标签来自声纹聚类：「我」是录音者本人，S1、S2… 是其他人。标签不可靠：同一个人可能被拆成几个标签，路人、广播、外放的视频也会被标成某个标签，甚至标成「我」。转写有错别字，按内容理解。
""" + summary.AUDIENCE + """
任务：找出从哪里开始是一段独立的新对话。独立的新对话 = 情境换了，而且后面的内容接不上前面：
- 和「我」打交道的人换了（之前的人都不再参与），做的事或聊的线索也换了；或者
- 「我」换了地方或换了事情（离开店、上车、到家、会开完了开始一个人干活），后面的内容接不上前面。
这些都不算新对话：
- 同一群人在同一个场合换了话题：会议的下一个议题、饭桌上聊到别的、面试的下一个问题、一通电话里说了几件事、讲课讲到下一部分；
- 同一件事跨了地方或换了人：为买同一样东西逛了几家店、和同一个朋友边走边聊、和同伴一路游览；
- 短暂的打断：服务员、路人、一通短电话、对 AI 说了一两句、外放的媒体或广播；
- 只有路人、广播、背景声的过渡段；
- 一个人对着 AI 连续布置工作，中间换了任务。
拿不准就不切，大多数对话不需要切。
只输出一个 JSON 对象：{"parts": [{"start": "HH:MM:SS", "kind": "这一段主要是什么", "scene": "不超过 15 字：在哪、和谁、做什么"}]}
parts 按时间顺序列出每段独立的对话，第一段从第一行开始；start 是这一段第一行的时间，照抄转写里的时间。不需要切就只给一段。
kind 看这一整段主要是什么，从这五个里选一个：交谈（「我」和在场的人说话，有来有回）、通话（打电话或视频通话）、会议（线上或线下开会、上课、面试）、独自做事（一个人干活、对 AI 说话、自言自语：按上面的标准是对 AI 说的话，口吻再像聊天也是独自做事，不是交谈）、媒体或背景（外放的视频、音乐、广播、路人说话，「我」不参与）。"""


def due(c: dict) -> bool:
    """Long enough to check, with speech for two parts; a conversation from before per-device pipelines (no device) never is."""
    return (SPLIT_MIN_S > 0 and bool(c.get("device")) and (c.get("speech_s") or 0) >= 2 * PART_S
            and (P(c["end"]) - P(c["start"])).total_seconds() >= SPLIT_MIN_S)


def merged(parts: list[dict]) -> list[dict]:
    """Media or background is not a part of its own: it joins the part before it (a first one joins the next); two solo
    stretches side by side are one solo session. An unknown kind counts as a talk."""
    out = []
    for x in ({**x, "kind": x.get("kind") if x.get("kind") in KINDS else "交谈"} for x in parts if isinstance(x, dict)):
        if out and (x["kind"] == "媒体或背景" or x["kind"] == out[-1]["kind"] == "独自做事"):
            continue
        if len(out) == 1 and out[0]["kind"] == "媒体或背景":
            out[0] = {**x, "start": out[0].get("start")}
            continue
        out.append(x)
    return out


def asked(reply: dict, rows: list[dict]) -> list[dict]:
    """The model's new parts as {i: the index of the row it starts at, scene}; a time no row has goes to the nearest row."""
    sec = lambda hms: sum(int(x) * k for x, k in zip(hms.split(":"), (3600, 60, 1)))
    out = []
    for x in merged(reply.get("parts") or [])[1:]:
        hms = x.get("start")
        if not isinstance(hms, str) or not re.fullmatch(r"\d\d:\d\d:\d\d", hms):
            continue
        d = lambda r: abs(sec(r["start"][11:19]) - sec(hms))
        i = min(range(len(rows)), key=lambda i: min(d(rows[i]), 86400 - d(rows[i])))  # past midnight too
        if i:
            out.append({"i": i, "scene": str(x.get("scene") or "")[:30]})
    return out


def pauses(rows: list[dict]) -> list[tuple[int, float]]:
    """(the index of the row after it, seconds) for each silence of >= PAUSE_S: from the end of everything before."""
    out, last = [], None
    for i, r in enumerate(rows):
        if last is not None and (g := (P(r["start"]) - last).total_seconds()) >= PAUSE_S:
            out.append((i, g))
        last = P(r["end"]) if last is None else max(last, P(r["end"]))
    return out


def snap(rows: list[dict], cuts: list[dict]) -> list[dict]:
    """Each cut moved to the nearest pause within REACH_S (0 inside one); none: dropped."""
    out, ps = {}, pauses(rows)
    for c in cuts:
        t, best = P(rows[c["i"]]["start"]), None
        for i, g in ps:
            b = P(rows[i]["start"])
            a = b - dt.timedelta(seconds=g)
            d = 0 if a <= t <= b else min(abs((t - a).total_seconds()), abs((t - b).total_seconds()))
            if d <= REACH_S and (best is None or d < best[0]):
                best = (d, i)
        if best:
            out.setdefault(best[1], {**c, "i": best[1]})
    return sorted(out.values(), key=lambda c: c["i"])


def speech(rows: list[dict]) -> float:
    return sum((P(r["end"]) - P(r["start"])).total_seconds() for r in rows if r.get("speaker") not in IGNORE)


def keep(rows: list[dict], cuts: list[dict]) -> list[dict]:
    """Only cuts that leave every part PART_S of speech: from the end, a short part joins the one before it; then a short
    first part joins the next."""
    out, end = [], len(rows)
    for c in reversed(cuts):
        if speech(rows[c["i"]:end]) >= PART_S:
            out.insert(0, c)
            end = c["i"]
    while out and speech(rows[:out[0]["i"]]) < PART_S:
        out.pop(0)
    return out


def plan(reply: dict, rows: list[dict]) -> list[dict]:
    return keep(rows, snap(rows, asked(reply, rows)))


async def ask(rows: list[dict]) -> list[dict]:
    """The cuts the model asks for that stand, [{i, scene}]; raises on any call error."""
    reply, _ = await summary.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": summary.transcript(rows)}],
                                      kind="split", reasoning_effort="high")
    return plan(reply, rows)


def part(rows: list[dict]) -> dict:
    """A part's conversation fields; its end covers all its rows, so conversation_rows gives exactly them back."""
    return {"start": rows[0]["start"], "end": max(r["end"] for r in rows), "sentences": rows, "speech_s": round(speech(rows), 1),
            "participants": sorted({r["speaker"] for r in rows if r.get("speaker") and r["speaker"] not in IGNORE}, key=str)}


def relink(value, cid: str, where, owner: dict):
    """A structured reference to the split conversation goes to the part of its todo (owner: old todo id -> new id, part)
    or of its time (at), else stays with the first part; moved todo ids are rewritten wherever they appear."""
    if isinstance(value, list):
        return [relink(x, cid, where, owner) for x in value]
    if isinstance(value, str):
        return owner[value][0] if value in owner else value
    if not isinstance(value, dict):
        return value
    out = {k: relink(v, cid, where, owner) for k, v in value.items()}
    tid = value.get("id")
    for k in ("conv", "conversation"):
        if value.get(k) == cid:
            out[k] = owner[tid][1] if isinstance(tid, str) and tid in owner else where(value.get("at"))
    return out


def apply(c: dict, cuts: list[str], revision) -> list[dict] | None:
    """Split c at cuts (the ISO starts of its later parts) in one transaction; mark it checked when there are none.
    The parts, or None when nothing was split (none, or c changed since revision)."""
    cid, device = c["id"], c["device"]
    with db.tx() as t:
        if (db.resolve_conversation(cid) != cid or db.kv(f"conversation_revision:{cid}") != revision
                or db.kv(f"split_checked:{cid}") == (revision or 0)):
            return None
        if not cuts:
            db.put_kv(f"split_checked:{cid}", revision or 0, t)
            return None
        c = db.conversation(cid)
        rows = db.sentences(c["start"], c["end"], True, device)
        edges = [c["start"], *cuts]
        parts = [part([r for r in rows if a <= r["start"] and (b is None or r["start"] < b)]) for a, b in zip(edges, cuts + [None])]
        parts[0]["start"], parts[-1]["end"] = c["start"], c["end"]  # the outer bounds stay
        ids = [cid]
        db.insert_conversation(t, {**c, **parts[0], "sentences": len(parts[0]["sentences"])})
        for p in parts[1:]:
            rec = late_merge.record(p, device)
            db.insert_conversation(t, {**rec, "source": c.get("source")})
            ids.append(rec["id"])
        where = lambda at: ids[sum(cut <= at for cut in cuts)] if isinstance(at, str) and at[10:11] == "T" else cid

        # todos go to the part they were said in, with marks; the first part's keep their ids (pinned)
        s = db.summary(cid) or {"conversation": cid}
        todos, owner = [[] for _ in ids], {}
        for tid, todo in zip(db.todo_ids(cid, s.get("todos") or []), s.get("todos") or []):
            pid = where(todo.get("at"))
            if pid == cid:
                todos[0].append({**todo, "id": tid})
            else:
                owner[tid] = (f"{pid}:split-{hashlib.sha1(tid.encode()).hexdigest()[:24]}", pid)
                todos[ids.index(pid)].append({**todo, "id": owner[tid][0]})
        for tid, (nid, pid) in owner.items():
            t.execute("UPDATE todo_marks SET id = ?, conv = ? WHERE id = ?", (nid, pid, tid))
        starts = {r["id"]: r["start"] for r in rows}  # a star (its id: the sentence's) goes to its sentence's part
        for sid, in t.execute("SELECT id FROM stars WHERE conv = ?", (cid,)).fetchall():
            t.execute("UPDATE stars SET conv = ? WHERE id = ?", (where(starts.get(sid)), sid))
        db.put_summary({**s, "status": "pending", "error": None, "todos": todos[0]}, t)
        for pid, ts in zip(ids[1:], todos[1:]):
            # its version too: a part whose summary keeps failing is kept "done" with these todos (due_todos reads them)
            db.put_summary({"conversation": pid, "status": "pending", "prompt_version": s.get("prompt_version"), "todos": ts}, t)
        if h := db.name_hints(cid).get(cid):
            for pid in ids:
                db.put_name_hints({**h, "conv": pid, "names": [x for x in h.get("names") or [] if where(x.get("at")) == pid]}, t)

        # each part keeps the voiceprints of the labels it holds, recounted on its own rows (voices_loop recomputes only
        # recent conversations); a pin keeping a voice apart holds in every part with that label
        labels = [{r.get("speaker") for r in p["sentences"]} for p in parts]
        old = [dict(v) for v in t.execute("SELECT * FROM voices WHERE conv = ?", (cid,))]
        t.execute("DELETE FROM voices WHERE conv = ?", (cid,))
        for v in old:
            for pid, p, ls in zip(ids, parts, labels):
                if v["label"] is None:
                    db.insert_voices(t, [{"conv": pid}])
                elif v["label"] in ls and v["e"] is not None:
                    r = late_merge.voice_record(pid, v["label"], db.vec(v["e"]), p["sentences"])
                    late_merge.put_voice(t, {**v, **{k: r.get(k) for k in ("conv", "n", "speech_s", "clip_start", "clip_end", "clip_text")}})
        for voice, grp in t.execute("SELECT voice, grp FROM apart").fetchall():
            for pid, ls in zip(ids[1:], labels[1:]):
                m = {f"{cid}|{x}": f"{pid}|{x}" for x in ls if x}
                if (voice in m or grp in m) and all(k in m for k in (voice, grp) if k.startswith(cid + "|")):
                    t.execute("INSERT OR IGNORE INTO apart VALUES (?, ?)", (m.get(voice, voice), m.get(grp, grp)))

        for name, body in t.execute("SELECT name, body FROM profiles").fetchall():
            if (new := relink(json.loads(body), cid, where, owner)) != json.loads(body):
                t.execute("UPDATE profiles SET body = ? WHERE name = ?", (json.dumps(new, ensure_ascii=False), name))
        for key, body in t.execute("SELECT key, value FROM kv WHERE key LIKE 'recap:%' OR key LIKE 'live:%'").fetchall():
            if body is not None and (new := relink(json.loads(body), cid, where, owner)) != json.loads(body):
                db.put_kv(key, new, t)
        for day in {c["start"][:10], c["end"][:10]}:  # a cached recap is redone once the parts' summaries are in
            if recap := db.kv(f"recap:{day}"):
                db.put_kv(f"recap:{day}", {**recap, "stale": True}, t)
                db.put_kv(f"recap_revision:{day}", (db.kv(f"recap_revision:{day}") or 0) + 1, t)

        for cut in cuts:
            t.execute("INSERT OR IGNORE INTO walls VALUES (?, ?)", (device, cut))
        for pid in ids:
            rev = (db.kv(f"conversation_revision:{pid}") or 0) + 1
            db.put_kv(f"conversation_revision:{pid}", rev, t)
            db.put_kv(f"split_checked:{pid}", rev, t)
        late_store.summary_done(cid, t)  # the parts are queued here, not as late work
    for key, bs in list(T().undo.items()):  # an undo from before would restore the whole conversation's voices
        if any(b.get("conv") == cid for b in bs):
            del T().undo[key]
    T().speakers["at"] = 0.0
    T().tags["at"] = 0.0
    log.info("split %s at %s", cid, ", ".join(x[11:19] for x in cuts))
    return [db.conversation(pid) for pid in ids]


async def check(c: dict, revision, dry: bool = False) -> list[dict]:
    """[{at, scene}] for each new part of c, applied (parts queued) unless dry; [] when it is one conversation, on any
    error, and when c changed during the call."""
    rows = conversation_rows(c)
    try:
        cuts = [{"at": rows[x["i"]]["start"], "scene": x["scene"]} for x in await ask(rows)]
    except Exception as e:  # HTTP errors, timeouts, a reply that is not JSON: summarized whole, checked again next time
        log.warning("split %s failed: %r", c["id"], e)
        return []
    if dry:
        return cuts
    try:
        parts = apply(c, [x["at"] for x in cuts], revision)
    except Exception:  # rolled back: never stop the summary worker over it
        log.exception("split %s not applied", c["id"])
        return []
    for p in parts or []:
        enqueue_summary(p)
        await ws.broadcast({"type": "conversation", **p})
    return cuts if parts else []


def checked(c: dict, revision) -> bool:
    return db.kv(f"split_checked:{c['id']}") == (revision or 0)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alldayrec.split", description=__doc__.split("\n\n")[0],
                                epilog="Run it on the server's host with LIVE_RECORD_URL=http://127.0.0.1:8770 (its loopback "
                                       "address): a proxy in front cuts a request this long.")
    p.add_argument("--since", required=True, type=dt.date.fromisoformat, help="conversations started on or after this day (YYYY-MM-DD)")
    p.add_argument("--apply", action="store_true", help="split them; without it a dry run that writes nothing")
    a = p.parse_args(argv)
    url = os.environ.get("LIVE_RECORD_URL") or sys.exit("LIVE_RECORD_URL is not set")
    token = os.environ.get("LIVE_RECORD_TOKEN") or ("" if sys.stdin.isatty() else sys.stdin.readline().strip())
    q = urllib.parse.urlencode({"since": a.since.isoformat(), "apply": str(a.apply).lower()})
    req = urllib.request.Request(url.rstrip("/") + "/api/split?" + q, method="POST",
                                 headers={"Authorization": "Bearer " + token} if token else {})
    with urllib.request.urlopen(req, timeout=6 * 3600) as r:  # one model call per conversation, one after another
        res = json.load(r)
    for x in res:
        print(x["start"][:16].replace("T", " "), x["id"], " | ".join(f'{c["at"][11:19]} {c["scene"]}' for c in x["cuts"]) or "-")
    print(f"{len(res)} checked, {sum(bool(x['cuts']) for x in res)} split" + ("" if a.apply else " (dry run: nothing written)"))
    return res


if __name__ == "__main__":
    main()
