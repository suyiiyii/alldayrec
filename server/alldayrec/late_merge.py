"""Attach late final rows and merge closed conversations, with all references in one transaction.

Only a later conversation's anonymous labels may change. Earlier rows and user edits stay intact.
"""
import datetime as dt
import hashlib
import json
import re

import numpy as np

from alldayrec import db
from alldayrec.conversations import GAP_S, IGNORE, split
from alldayrec.diarist import TH
from alldayrec.tenant import T

P = dt.datetime.fromisoformat
ANON = re.compile(r"S\d+")


def unit(e):
    return e / max(float(np.linalg.norm(e)), 1e-12)


def label_map(labels, vectors: dict, target: dict, used: set) -> dict:
    """Diarist's cosine distance and threshold; reserve every occupied label before assigning new ones."""
    out = {}
    for label in sorted(set(labels)):
        if not ANON.fullmatch(label):
            out[label] = label
            continue
        e = vectors.get(label)
        score, near = max(((float(unit(e) @ unit(v)), k) for k, v in target.items() if v is not None),
                          default=(-1.0, None)) if e is not None else (-1.0, None)
        if near is not None and score > 1 - TH:
            out[label] = near
        else:
            n = max([int(x[1:]) for x in used if ANON.fullmatch(x)] or [0]) + 1
            out[label] = f"S{n}"
            used.add(out[label])
        if e is not None:
            target.setdefault(out[label], e)
    return out


def rewrite(value, ids: dict, voices: dict | None = None, labels: dict | None = None, field: str = ""):
    """Rewrite structured IDs (including dictionary keys), never replace words in users' prose."""
    if isinstance(value, dict):
        cid = value.get("conv") or value.get("conversation")
        if isinstance(cid, str) and cid in ids and voices:
            labels = {k.split("|", 1)[1]: v.split("|", 1)[1] for k, v in voices.items() if k.startswith(cid + "|")}
        return {rewrite(k, ids, voices): rewrite(v, ids, voices, labels, k) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(x, ids, voices, labels, field) for x in value]
    if not isinstance(value, str):
        return value
    if field in ("text", "quote", "title", "summary", "content", "note", "clip_text", "name"):
        return value
    if voices and value in voices:
        return voices[value]
    if value in ids:
        return ids[value]
    for old, new in ids.items():
        if ":" not in old and value.startswith(old + "|"):
            return new + "|" + (labels or {}).get(value[len(old) + 1:], value[len(old) + 1:])
    if labels and field in ("speaker", "label", "who", "by", "owner", "to", "participants"):
        return labels.get(value, value)
    return value


def put_voice(t, v: dict):
    """Move a voice without dropping a colliding target's clip or enrolled evidence."""
    if not v.get("label"):
        return
    v = {"n": 0, "speech_s": 0.0, "k": 1, **v}
    old = t.execute("SELECT * FROM voices WHERE conv = ? AND label = ?", (v["conv"], v["label"])).fetchone()
    if old:
        old = dict(old)
        es = [(db.vec(x["e"]), x.get("k") or 1) for x in (old, v) if x.get("e")]
        old["e"] = db.blob(unit(sum(e * k for e, k in es))) if es else None
        for k in ("n", "speech_s", "k"):
            old[k] = (old.get(k) or 0) + (v.get(k) or 0)
        v = old
    t.execute(f"INSERT OR REPLACE INTO voices ({','.join(db.V_COLS)}) VALUES ({','.join('?' * len(db.V_COLS))})",
              tuple(v.get(k) for k in db.V_COLS))


def voice_record(cid: str, label: str, e, rows: list[dict]) -> dict:
    rs = [r for r in rows if r.get("speaker") == label and not r.get("music")]
    best = max(rs, key=lambda r: (P(r["end"]) - P(r["start"])).total_seconds(), default=None)
    return {"conv": cid, "label": label, "device": "live", "e": db.blob(e), "k": 1, "n": len(rs),
            "speech_s": sum((P(r["end"]) - P(r["start"])).total_seconds() for r in rs),
            **({"clip_start": best["start"], "clip_end": min(best["end"], (P(best["start"]) + dt.timedelta(seconds=10)).isoformat()),
                "clip_text": best.get("text")} if best else {})}


def merge(t, early: dict, later: dict, mapping: dict) -> dict:
    """Every stored conversation reference: relational columns, structured JSON, voice keys and todo IDs."""
    old, new = later["id"], early["id"]
    ids, voices = {old: new}, {f"{old}|{k}": f"{new}|{v}" for k, v in mapping.items()}
    # Todo identity includes its conversation. Pin a unique identity first, then rewrite cached recap IDs.
    src = db.summary(old) or {}
    dst = db.summary(new) or {"conversation": new, "status": "pending"}
    moved = []
    for tid, todo in zip(db.todo_ids(old, src.get("todos") or []), src.get("todos") or []):
        nid = f"{new}:merged-{hashlib.sha1(tid.encode()).hexdigest()[:24]}"
        ids[tid] = nid
        moved.append({**rewrite(todo, ids, voices, mapping), "id": nid})
    # Also keep a mark whose summary is missing/corrupt. It must not silently become another action's mark.
    for r in t.execute("SELECT * FROM todo_marks WHERE conv = ?", (old,)).fetchall():
        nid = ids.setdefault(r["id"], f"{new}:merged-{hashlib.sha1(r['id'].encode()).hexdigest()[:24]}")
        t.execute("UPDATE todo_marks SET id = ?, conv = ? WHERE id = ?", (nid, new, r["id"]))
    dst["todos"] = (dst.get("todos") or []) + moved
    db.put_summary(dst, t)
    for v in t.execute("SELECT * FROM voices WHERE conv = ?", (old,)).fetchall():
        v = dict(v)
        v.update(conv=new, label=mapping.get(v["label"], v["label"]))
        put_voice(t, v)
    t.execute("DELETE FROM voices WHERE conv = ?", (old,))
    pins = t.execute("SELECT voice, grp FROM apart").fetchall()
    for voice, grp in pins:
        # A group can itself be an unnamed voice's key.
        nv, ng = rewrite(voice, ids, voices), rewrite(grp, ids, voices)
        if (nv, ng) != (voice, grp):
            t.execute("DELETE FROM apart WHERE voice = ? AND grp = ?", (voice, grp))
            t.execute("INSERT OR IGNORE INTO apart VALUES (?, ?)", (nv, ng))
    for table in ("feedback", "stars", "enrolled"):
        t.execute(f"UPDATE {table} SET conv = ? WHERE conv = ?", (new, old))
    h = db.name_hints(old).get(old)
    if h:
        h = rewrite(h, ids, voices, mapping)
        target_h = db.name_hints(new).get(new)
        if target_h:
            target_h["names"] = (target_h.get("names") or []) + (h.get("names") or [])
            h = target_h
        db.put_name_hints(h, t)
    timeline = db.timeline(old)
    if timeline:
        timeline = rewrite(timeline, ids, voices, mapping)
        target_tl = db.timeline(new)
        if target_tl:
            target_tl.setdefault("merged", []).append(timeline)
            timeline = target_tl
        db.put_timeline(new, timeline, t)
    for table, col in (("summaries", "conversation"), ("timelines", "conversation"), ("name_hints", "conv")):
        t.execute(f"DELETE FROM {table} WHERE {col} = ?", (old,))
    for name, body in t.execute("SELECT name, body FROM profiles").fetchall():
        changed = rewrite(json.loads(body), ids, voices)
        t.execute("UPDATE profiles SET body = ? WHERE name = ?", (json.dumps(changed, ensure_ascii=False), name))
    for key, body in t.execute("SELECT key, value FROM kv WHERE key LIKE 'recap:%' OR key LIKE 'live:%' OR key IN ('me_centroids','selfvoice')").fetchall():
        if body is not None:
            db.put_kv(key, rewrite(json.loads(body), ids, voices), t)
    t.execute("INSERT INTO conversation_aliases (old,new,labels) VALUES (?,?,?)", (old, new, json.dumps(mapping)))
    t.execute("DELETE FROM conversations WHERE id = ?", (old,))
    return ids


def remap_rows(t, c: dict, mapping: dict):
    changes = [(k, v) for k, v in mapping.items() if k != v]
    if not changes:
        return
    cases = " ".join("WHEN ? THEN ?" for _ in changes)
    t.execute(f"UPDATE sentences SET speaker = CASE speaker {cases} ELSE speaker END WHERE source = ? AND start >= ? AND start <= ?",
              (*[x for pair in changes for x in pair], c.get("device"), c["start"], c["end"]))


def record(group: dict, device: str) -> dict:
    cid = "c" + group["start"].replace("-", "").replace(":", "").replace("T", "")[:14]
    if db.conversation(cid) or db.conn().execute("SELECT 1 FROM conversation_aliases WHERE old = ?", (cid,)).fetchone():
        cid += "-" + re.sub(r"[^\w]", "_", device) + "-" + hashlib.sha1(group["start"].encode()).hexdigest()[:8]
    return {**group, "id": cid, "device": device, "sentences": len(group["sentences"])}


def attach(span: dict, rows: list[dict], vectors: dict, existing_vectors: dict) -> tuple[list[dict], list[str]]:
    """Insert-only late rows, attach their connected groups, then move references atomically.

    Existing conversations are intervals; late speech connects them only across gaps shorter than GAP_S.
    Disconnected rows use the same Grouper MIN/MAX rules as the live layer.
    """
    device = span["device"]
    changed, removed = [], []
    with db.tx() as t:
        # An open conversation may have appeared while model calls ran: caller retries after it closes.
        from alldayrec.late_store import blocked
        if blocked(span):
            t.execute("UPDATE late_spans SET status = 'deferred', tries = max(0,tries-1) WHERE id = ?", (span["id"],))
            return [], []
        kept = []
        for r in rows:
            # Full containment in one exact uncovered range; existing corrections and overlapping final rows win.
            a, b = round(P(r["start"]).timestamp() * 1000), round(P(r["end"]).timestamp() * 1000)
            if not any(lo <= a < b <= hi for lo, hi in json.loads(span["ranges"])):
                continue
            if t.execute("SELECT 1 FROM sentences WHERE source = ? AND (id = ? OR (start < ? AND end > ?)) LIMIT 1",
                         (device, r["id"], r["end"], r["start"])).fetchone():
                continue
            db.insert_sentences(t, [r])
            t.execute("UPDATE sentences SET late = 1 WHERE id = ? AND source = ?", (r["id"], device))
            kept.append(r)
        if kept:
            low, high = min(r["start"] for r in kept), max(r["end"] for r in kept)
            convs = [db._conversation(r) for r in t.execute(
                "SELECT * FROM conversations WHERE device = ? AND start <= ? AND end >= ? ORDER BY start, id",
                (device, (P(high) + dt.timedelta(seconds=GAP_S)).isoformat(),
                 (P(low) - dt.timedelta(seconds=GAP_S)).isoformat()))]
            # Connected components of late rows plus existing closed conversation intervals.
            events = [(c["start"], c["end"], "conv", c) for c in convs] + [(r["start"], r["end"], "row", r) for r in kept]
            components = []
            for a, b, kind, item in sorted(events, key=lambda e: e[0]):
                if not components or (P(a) - P(components[-1]["end"])).total_seconds() >= GAP_S:
                    components.append({"end": b, "convs": [], "rows": []})
                comp = components[-1]
                comp["end"] = max(comp["end"], b)
                comp["convs" if kind == "conv" else "rows"].append(item)
            for comp in components:
                if not comp["rows"]:
                    continue
                cs, rs = comp["convs"], comp["rows"]
                if not cs:
                    for group in split(rs):
                        rec = record(group, device)
                        db.insert_conversation(t, rec)
                        for label in rec["participants"]:
                            if label in vectors:
                                put_voice(t, voice_record(rec["id"], label, vectors[label], group["sentences"]))
                        changed.append(rec)
                    continue
                early, *later = cs
                new_ids = {r["id"] for r in kept}
                for c in cs:
                    for label, e in existing_vectors.get(c["id"], {}).items():
                        if label in c["participants"] and not t.execute("SELECT 1 FROM voices WHERE conv = ? AND label = ? AND e IS NOT NULL", (c["id"], label)).fetchone():
                            prior = [r for r in db.sentences(c["start"], c["end"], True, device) if r["id"] not in new_ids]
                            put_voice(t, voice_record(c["id"], label, e, prior))
                target = {k: v for k, v in existing_vectors.get(early["id"], {}).items() if k in early["participants"]}
                used = set(early["participants"]) | set(target)
                for c in later:
                    lm = label_map(c["participants"], existing_vectors.get(c["id"], {}), target, used)
                    remap_rows(t, c, lm)
                    # merge() does reference migration; rows have already been remapped simultaneously.
                    merge(t, early, c, lm)
                    removed.append(c["id"])
                lm = label_map({r.get("speaker", "S?") for r in rs}, vectors, target, used)
                for r in rs:
                    r["speaker"] = lm.get(r.get("speaker"), r.get("speaker"))
                    t.execute("UPDATE sentences SET speaker = ? WHERE id = ? AND source = ?", (r["speaker"], r["id"], device))
                for lab in {r.get("speaker") for r in rs}:
                    es = [v for k, v in vectors.items() if lm.get(k, k) == lab]
                    if es:
                        put_voice(t, voice_record(early["id"], lab, unit(np.mean(es, 0)), rs))
                early["start"] = min([early["start"]] + [c["start"] for c in later] + [r["start"] for r in rs])
                early["end"] = max([early["end"]] + [c["end"] for c in later] + [r["end"] for r in rs])
                all_rows = db.sentences(early["start"], early["end"], True, device)
                early.update(sentences=len(all_rows), speech_s=round(sum((P(r["end"]) - P(r["start"])).total_seconds()
                             for r in all_rows if r.get("speaker") not in IGNORE), 1),
                             participants=sorted({r["speaker"] for r in all_rows if r.get("speaker") and r["speaker"] not in IGNORE}))
                db.insert_conversation(t, early)
                for label in early["participants"]:
                    labeled = [r for r in all_rows if r.get("speaker") == label and not r.get("music")]
                    t.execute("UPDATE voices SET n = ?, speech_s = ? WHERE conv = ? AND label = ?",
                              (len(labeled), sum((P(r["end"]) - P(r["start"])).total_seconds() for r in labeled), early["id"], label))
                changed.append(early)
        for c in changed:
            s = db.summary(c["id"]) or {"conversation": c["id"]}
            db.put_summary({**s, "status": "pending", "error": None}, t)
            key = f"conversation_revision:{c['id']}"
            db.put_kv(key, (db.kv(key) or 0) + 1, t)
            # Preserve the cached recap and references; regenerate once its new summaries have finished.
            days = {c["start"][:10], c["end"][:10]} | {r["start"][:10] for r in kept}
            for day in days:
                if recap := db.kv(f"recap:{day}"):
                    db.put_kv(f"recap:{day}", {**recap, "stale": True}, t)
        t.execute("UPDATE late_spans SET status = 'done', done_ms = ?, note = NULL WHERE id = ?",
                  (round(dt.datetime.now().timestamp() * 1000), span["id"]))
    # An undo snapshot predating a merge could restore obsolete voice rows and participant lists. Expire it.
    touched = {c["id"] for c in changed} | set(removed)
    for key, bs in list(T().undo.items()):
        if any(b.get("conv") in touched for b in bs):
            del T().undo[key]
    T().speakers["at"] = 0.0
    T().tags["at"] = 0.0
    return [db.conversation(c["id"]) for c in changed], removed
