"""Voice evidence for an agent checking who said a sentence (tenant "voice_tools", off by default; MCP's voice tools,
GET /api/voiceprint*): the voiceprint of a sentence or of a stretch of it, and how close it is to another sentence, a
known person (the people bank's samples, 我's voiceprint) or a conversation's label (voices). Read only: a voiceprint
not kept (seg_emb) is made from the audio and dropped.

One made from the audio is one model call of at most MAX_S s of it, at most CALLS at once over the process; one more
is refused at once (Refused, 503), never queued in the DB threads MCP runs in. On an infer service it is one try of
TIMEOUT_S, none while the service fails or for COOL_S after one failed: the pipelines' calls wait for it, an agent's
must not.
"""
import datetime as dt
import json
import logging
import re
import threading
import time

import numpy as np

from alldayrec import audio
from alldayrec import bank
from alldayrec import compute
from alldayrec import core
from alldayrec import db
from alldayrec import infer
from alldayrec import store
from alldayrec.diarist import _rows
from alldayrec.tenant import T

log = logging.getLogger(__name__)

MAX_S = 15.0  # of a sentence's audio one voiceprint takes, as label_voice and enroll do
MIN_S = 0.5   # a stretch asked for is at least this long (diarist.MIN_DUR: under 1 s a voiceprint is weak already)
CALLS = threading.BoundedSemaphore(1)  # beside the pipelines' threads.INFER calls; a 15 s voiceprint is ~0.3 s of 4 cores
TIMEOUT_S = 10.0  # an infer service's one try
COOL_S = 30.0  # after a try failed: none for this long (the service may still be working on it: no pile of them there)
cool_until = 0.0  # time.monotonic() before which none is tried
TIME = re.compile(r"\d\d:\d\d:\d\d")


class Refused(Exception):
    """What the agent is told instead (Chinese, for the model to read), with the HTTP status that goes with it."""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def on() -> bool:
    return T().voice_tools


def model() -> str:
    """The voiceprint model as seg_emb names it."""
    return f"{infer.EMB}@{infer.MODELS[infer.EMB][:12]}"


def unit(v) -> np.ndarray:
    v = np.asarray(v, np.float64)
    return v / (np.linalg.norm(v) or 1)


def seconds(r: dict) -> float:
    return (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds()


def sentence(ref: str) -> dict:
    """The row a reference names, with its rowid and its conversation (conv, None: in none closed): a sentence id
    (grep's: of one device only), or "<conversation id>@HH:MM:SS", a transcript line's time (its device's row starting
    in that second; the first of two)."""
    ref = str(ref or "").strip()
    if "@" in ref:
        cid, at = ref.split("@", 1)
        c = db.conversation(cid)
        if not c:
            raise Refused(f"没有这段对话：{cid!r}", 404)
        if not TIME.fullmatch(at):
            raise Refused(f"「对话 id@时:分:秒」的时间要写成 HH:MM:SS，不是 {at!r}")
        rows = [dict(r) for r in db.conn().execute(
            "SELECT rowid, * FROM sentences WHERE start >= ? AND start <= ? AND (? IS NULL OR source = ?) ORDER BY start",
            (c["start"], c["end"], c.get("device"), c.get("device"))) if r["start"][11:19] == at]
        if not rows:
            raise Refused(f"对话 {c['id']} 里没有 {at} 开始的句子：时间照抄 transcript 每行开头的", 404)
    else:
        rows = [dict(r) for r in db.conn().execute("SELECT rowid, * FROM sentences WHERE id = ?", (ref,))]
        if not rows:
            raise Refused(f"没有这句：{ref!r}（写成「对话 id@时:分:秒」：grep 每行的对话 id 和时间，或 transcript 每行开头的时间）", 404)
        if len({r["source"] for r in rows}) > 1:
            raise Refused("两台设备都有这个句子 id：改写成「对话 id@时:分:秒」")
    r = rows[0]
    c = db.conn().execute(f"SELECT {db.conv_at('s')} FROM (SELECT ? AS start, ? AS device) s", (r["start"], r["source"])).fetchone()
    return {**r, "conv": c[0]}


def from_audio(r: dict, frm: float, to: float) -> np.ndarray:
    """The voiceprint of [frm, to] s into row r, decoded and embedded under the limits above (Refused when one
    cannot be had now)."""
    global cool_until
    if compute.REMOTE and time.monotonic() < cool_until:
        raise Refused("推理服务刚才没有回应，过半分钟再试", 503)
    if not CALLS.acquire(blocking=False):
        raise Refused("正在算别的声纹，过几秒再试", 503)
    try:
        a = dt.datetime.fromisoformat(r["start"])
        try:
            x = audio.cut(a + dt.timedelta(seconds=frm), a + dt.timedelta(seconds=to), "pcm", r["source"])
        except (audio.DecoderTerminated, store.Unreachable):
            raise Refused("录音现在读不到，稍后再试", 503)
        if x is None:
            raise Refused("这句的录音已经不在了（裁掉了，或者是导入的历史对话），算不了声纹", 404)
        try:
            if compute.REMOTE:
                return unit(compute.REMOTE.once("embed", x, compute.voiceprint, TIMEOUT_S))
            return unit(core.state["embed"](x))
        except Exception as e:  # the service down or slow, a wrong answer
            if compute.REMOTE:
                cool_until = time.monotonic() + COOL_S
            log.warning("voice tool: no voiceprint: %s", type(e).__name__)
            raise Refused("推理服务现在不可用，稍后再试", 503)
    finally:
        CALLS.release()


def kept(r: dict) -> list[dict]:
    """The row's voiceprints in seg_emb (by db seg_emb's rule, of today's model): each engine segment or part of one,
    its span in the sentence (s) and unit vector."""
    t0 = db.ms(r["start"])
    return [{"from": round((a - t0) / 1000, 2), "to": round((b - t0) / 1000, 2), "e": unit(np.frombuffer(e, np.float16))}
            for a, b, e in db.conn().execute("SELECT start_ms, end_ms, e FROM seg_emb WHERE sentence = ? AND device IS ? "
                                             "AND sentence_start = ? AND model = ? ORDER BY start_ms, part",
                                             (r["rowid"], r["source"], r["start"], model()))]


def voiceprint(ref: str, frm: float | None = None, to: float | None = None) -> dict:
    """The sentence's voiceprint: the kept ones' mean weighted by their length (each in parts when more than one),
    else made from its first MAX_S s; with frm / to (s into it), of that stretch, from the audio."""
    r = sentence(ref)
    long, parts = seconds(r), []
    if frm is None and to is None and (parts := kept(r)):
        e, source = unit(sum((max(p["to"] - p["from"], 0.01)) * p["e"] for p in parts)), "seg_emb"
        span = [min(p["from"] for p in parts), max(p["to"] for p in parts)]
    else:
        frm = 0.0 if frm is None else float(frm)
        to = min(long, frm + MAX_S) if to is None else float(to)
        if not (0 <= frm < to <= long + 0.05):
            raise Refused(f"from_s、to_s 是这句里的第几秒：0 ≤ from_s < to_s ≤ {long:.2f}（这句 {long:.2f} 秒）")
        if long < MIN_S:
            raise Refused(f"这句太短（{long:.2f} 秒），算不了声纹")
        if to - frm > MAX_S + 1e-9 or to - frm < MIN_S:
            raise Refused(f"一段要 {MIN_S}–{MAX_S:.0f} 秒，不是 {to - frm:.2f} 秒")
        e, source, span, parts = from_audio(r, frm, to), "audio", [round(frm, 2), round(to, 2)], []
    return {"sentence": {k: r.get(k) for k in ("id", "start", "end", "speaker", "text", "source", "conv", "music")},
            "rowid": r["rowid"], "seconds": round(long, 2), "from": source, "span": span, "e": e,
            "parts": parts if len(parts) > 1 else []}


def people(row: int | None, conv: str | None = None) -> dict[str, dict]:
    """Every known voice by name: the people bank's samples (k x dim) and 我's voiceprint. A name whose ledger has
    the sentence of rowid `row` (it taught that name) is made again without it (left_out); taught by it alone: E None.
    我's centroids cannot be made again without one sentence: taught says one of them was learnt from conv (「这是我」,
    kv me_centroids; one picked on the 「哪个是你？」 card is not said)."""
    # shortcut: enrolled.row is a rowid, gone (or another row's) once its sentence is finalized again; the sentence is
    # then not left out (scores high). Upgrade with sentences' explicit INTEGER PRIMARY KEY (db seg_emb's note)
    out = {name: {"E": _rows(E), "left_out": False} for name, (_, E) in db.people().items()}
    if row is not None:
        for (name,) in db.conn().execute("SELECT DISTINCT name FROM enrolled WHERE row = ?", (row,)).fetchall():
            rest = [(c, rw, n, db.vec(e)) for c, rw, n, e in db.conn().execute(
                "SELECT conv, row, n, e FROM enrolled WHERE name = ? AND row IS NOT ?", (name, row))]
            out[name] = {"E": bank.samples(rest) if rest else None, "left_out": True}
    from alldayrec.routes_speakers import owner_bank
    me = T().owner if T().owner is not None else owner_bank()
    if me:
        mine = {k for k, vs in (db.kv("me_centroids") or {}).items() if conv and f"{db.resolve_conversation(conv)}|我" in vs}
        out["我"] = {"E": np.array([unit(v) for v in me.values()]), "left_out": False, "taught": bool(mine & set(me))}
    return out


def labels(conv: str | None, r: dict) -> dict[str, dict]:
    """A conversation's labels' voiceprints (voices: up to 4 of its longest sentences each), each saying whether the
    sentence r is one of them (in_it; not known for a row made before BG_GATE kept what it used: None)."""
    if not conv:
        return {}
    out = {}
    for label, e, used in db.conn().execute("SELECT label, e, used FROM voices WHERE conv = ? AND e IS NOT NULL",
                                            (db.resolve_conversation(conv),)):
        inside = None if used is None else any(a == r["start"] and d == r["source"] for a, _, d in json.loads(used))
        out[label] = {"E": unit(db.vec(e))[None], "in_it": inside}
    return out


def score(e: np.ndarray, E: np.ndarray) -> float:
    """As the diarist names: the nearest of a voice's samples."""
    return round(float(np.max(np.atleast_2d(E) @ e)), 3)


def compare(ref: str, frm=None, to=None, other: str = "", other_from=None, other_to=None, person: str = "", label: str = "") -> dict:
    """How close the sentence (or its stretch) is to exactly one of: another sentence (or a stretch of it), a known
    person, a label ("S1" of the sentence's conversation, or "<conversation id>@S1"); each kept part's too."""
    other, person, label = (str(x or "") for x in (other, person, label))
    if sum(map(bool, (other, person, label))) != 1:
        raise Refused("other、person、label 三个里正好填一个")
    v = voiceprint(ref, frm, to)
    if other:
        w = voiceprint(other, other_from, other_to)
        E, what = w["e"][None], {"kind": "sentence", "sentence": w["sentence"], "span": w["span"], "from": w["from"], "seconds": w["seconds"]}
    elif person:
        ps = people(v["rowid"], v["sentence"]["conv"])
        if person not in ps:
            raise Refused(f"没有「{person}」的声纹：认出名字的人在 names 里，没在人物页上命名过的人没有声纹", 404)
        if ps[person]["E"] is None:
            raise Refused(f"「{person}」的声纹只由这一句教出，去掉这句以后没法比", 404)
        E, what = ps[person]["E"], {"kind": "person", "name": person, "samples": len(ps[person]["E"]),
                                    "left_out": ps[person]["left_out"], "taught": ps[person].get("taught", False)}
    else:
        conv, lab = label.split("@", 1) if "@" in label else (v["sentence"]["conv"], label)
        ls = labels(conv, v["sentence"])
        if lab not in ls:
            raise Refused(f"对话 {conv or '（这句不在已结束的对话里）'} 没有「{lab}」的声纹", 404)
        E, what = ls[lab]["E"], {"kind": "label", "conv": conv, "label": lab, "in_it": ls[lab]["in_it"]}
    return {"sentence": v["sentence"], "span": v["span"], "from": v["from"], "seconds": v["seconds"],
            "with": what, "score": score(v["e"], E), "parts": [{"from": p["from"], "to": p["to"], "score": score(p["e"], E)} for p in v["parts"]]}


def nearest(ref: str, frm=None, to=None, n: int = 5) -> dict:
    """The sentence (or its stretch) against every known person and its own conversation's labels, the closest
    first; each kept part's closest too."""
    n = max(1, min(int(n), 20))
    v = voiceprint(ref, frm, to)
    cands = [({"kind": "person", "name": k, "samples": len(p["E"]), "left_out": p["left_out"], "taught": p.get("taught", False)}, p["E"])
             for k, p in people(v["rowid"], v["sentence"]["conv"]).items() if p["E"] is not None]
    cands += [({"kind": "label", "conv": v["sentence"]["conv"], "label": k, "in_it": x["in_it"]}, x["E"]) for k, x in labels(v["sentence"]["conv"], v["sentence"]).items()]
    ranked = sorted(({**c, "score": score(v["e"], E)} for c, E in cands), key=lambda c: -c["score"])
    best = lambda e: max(({**c, "score": score(e, E)} for c, E in cands), key=lambda c: c["score"], default=None)
    return {"sentence": v["sentence"], "span": v["span"], "from": v["from"], "seconds": v["seconds"], "nearest": ranked[:n],
            "known": len(cands), "parts": [{"from": p["from"], "to": p["to"], "best": best(p["e"])} for p in v["parts"]]}
