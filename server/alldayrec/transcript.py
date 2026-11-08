"""A window's engine sentences become rows: the turns already final clipped off, speakers labelled by the diarist, the
final rows of a span, and how drafts and live analyses line up with them."""
import datetime as dt
import os

import numpy as np

from alldayrec.segmenter import SR


TURN_GAP_S = float(os.environ.get("TURN_GAP_S", "1.0"))  # same speaker, closer than this: one final row; 0 keeps the engine's sentences as rows
EDGE_S = 3.0                               # rows touching the window edges may be cut: left to the next pass


# Two people in one engine sentence (#419: a quick back-and-forth the engine keeps as one): 1.5 s voiceprints every 0.5 s
# over a sentence of 3 s or more; where the windows in the SPLIT_CTX_S before a time and those in the SPLIT_CTX_S after
# it are least alike, under SPLIT_COS, it is cut at the nearest word and each part labelled (and cut) on its own. On the
# five GT cases 0.35 found 71% of two speakers' turns joined, 89% of A-B-A (whole sides instead of 3 s: 18%), and cut
# 4.6% of single-speaker spans. 0: never cut
SPLIT_COS = float(os.environ.get("SPLIT_COS", "0.35"))
SPLIT_WIN_S, SPLIT_HOP_S, SPLIT_CTX_S = 1.5, 0.5, 3.0
SPLIT_MIN_S = 1.0  # a part shorter than this (the word nearest the change is far from it): no cut


CLIP_JITTER_MS = 300  # a re-cut turn's start moves about this much between passes


def clip_turns(segs: list[dict], since_s: float) -> list[dict]:
    """The previous pass finalized every turn starting before `since` (its `to` is the start of the first
    turn it left out), so those are dropped here, whole: keeping the part after `since` re-emitted the
    sentence of a speaker overlapping the cut turn (phone case 09-29 13:12). Only a turn starting within
    the re-cut jitter of `since` is kept, clipped to it."""
    out = []
    for g in segs:
        if g["start_ms"] < since_s * 1000 - CLIP_JITTER_MS or g["end_ms"] <= since_s * 1000 + 200:
            continue
        if g["start_ms"] < since_s * 1000:
            g = {**g, "start_ms": int(since_s * 1000)}
        out.append(g)
    return out


def _part(g: dict, words: list[dict]) -> dict:
    return {**g, "start_ms": words[0]["start_ms"], "end_ms": words[-1]["end_ms"], "text": "".join(w["text"] for w in words), "words": words}


def split_speakers(g: dict, x: np.ndarray, embed) -> list[dict]:
    """g as the parts different people said (SPLIT_COS), each cut again while it is long enough; [g] when it has no
    words to cut at."""
    words = g.get("words") or []
    a, b = g["start_ms"] / 1000, g["end_ms"] / 1000
    if SPLIT_COS <= 0 or len(words) < 2 or b - a < 2 * SPLIT_WIN_S or not all(w.get("text") for w in words):
        return [g]
    y = x[int(a * SR):int(b * SR)]
    w, h = int(SPLIT_WIN_S * SR), int(SPLIT_HOP_S * SR)
    starts = list(range(0, len(y) - w + 1, h))
    if not starts:
        return [g]
    if starts[-1] != len(y) - w:
        starts.append(len(y) - w)
    es = [(s / SR, embed(y[s:s + w])) for s in starts]
    unit = lambda v: v / np.linalg.norm(v)
    best = None
    for t in np.arange(SPLIT_WIN_S, len(y) / SR - SPLIT_WIN_S + 1e-6, SPLIT_HOP_S):
        left = [e for t0, e in es if t - SPLIT_CTX_S - 1e-6 <= t0 and t0 + SPLIT_WIN_S <= t + 1e-6]
        right = [e for t0, e in es if t - 1e-6 <= t0 and t0 + SPLIT_WIN_S <= t + SPLIT_CTX_S + 1e-6]
        if left and right and (c := float(unit(np.mean(left, 0)) @ unit(np.mean(right, 0)))) < (best or (SPLIT_COS, 0))[0]:
            best = (c, a + t)
    if not best:
        return [g]
    # the gap between two words nearest the change
    i = min(range(1, len(words)), key=lambda k: abs((words[k - 1]["end_ms"] + words[k]["start_ms"]) / 2000 - best[1]))
    l, r = _part(g, words[:i]), _part(g, words[i:])
    if min(p["end_ms"] - p["start_ms"] for p in (l, r)) < SPLIT_MIN_S * 1000:
        return [g]
    return split_speakers(l, x, embed) + split_speakers(r, x, embed)


def voices(embed, segs: list[dict], x: np.ndarray, music=None) -> list:
    """label_sentences' voiceprints, the diarist untouched: per sentence None (too short to embed, or music: music 1)
    or its parts (split_speakers) each with the voiceprint dia.add takes. Nothing here depends on another piece: the
    final layer runs it for several pieces at once and labels them in order (label)."""
    out = []
    for g in segs:
        a, b = g["start_ms"] / 1000, g["end_ms"] / 1000
        g["engine_speaker"] = g.get("speaker")
        y = x[int(a * SR):int(min(b, a + 15) * SR)]
        if b - a >= 0.3 and music and music(y):
            g["music"] = 1
        if b - a < 0.3 or g.get("music"):
            out.append(None)
            continue
        parts, es = split_speakers(g, x, embed), []
        for p in parts:
            if len(parts) > 1:
                pa, pb = p["start_ms"] / 1000, p["end_ms"] / 1000
                y = x[int(pa * SR):int(min(pb, pa + 15) * SR)]
            es.append(embed(y))
        out.append(list(zip(parts, es)))
    return out


def label(dia, segs: list[dict], vs: list) -> list:
    """Speaker labels from the conversation clustering, in time order, of segs and their voices(); returns the diarist
    mark after each sentence (None when too short to embed) so a pass can take back the sentences it leaves open. A
    sentence voices() heard as music, or too short, gets S?. A sentence two people said (split_speakers) becomes its
    parts in segs, in place, each with `split` (its place, 1 on) and a label of its own."""
    marks, out = [], []
    for g, v in zip(segs, vs):
        if v is None:
            g["speaker"], g["confidence"] = "S?", 0.0
            marks.append(None)
            out.append(g)
            continue
        done = []
        for p, e in v:
            p["speaker"], p["confidence"] = dia.add(e, (p["end_ms"] - p["start_ms"]) / 1000)
            if done and done[-1][0]["speaker"] == p["speaker"]:  # the diarist hears one person after all: one part
                q = done[-1][0]
                p = {**_part(q, q["words"] + p["words"]), "confidence": max(q["confidence"], p["confidence"])}
            else:
                done.append(None)
            done[-1] = (p, dia.mark())
        for k, (p, m) in enumerate(done, 1):
            if len(done) > 1:
                p["split"] = k
            out.append(p)
            marks.append(m)
    segs[:] = out
    return marks


def label_sentences(dia, embed, segs: list[dict], x: np.ndarray, music=None) -> list:
    """voices() then label(): music (music.heard, the final layer's) hears a sentence as music."""
    return label(dia, segs, voices(embed, segs, x, music))


def final_rows(segs: list[dict], names: dict, t0: dt.datetime, win_s: float, conf: dict | None = None,
               since: float = EDGE_S, at=None, edge: float = EDGE_S) -> tuple[dt.datetime, dt.datetime, list[dict]]:
    """Second pass: the diarized speaker turns of one window become the final rows for [from, to).

    VAD utterances glue fast back-and-forth into one line (no pause to cut at), so the final
    layer is built from the diarized turns instead. Consecutive pieces of one speaker are joined;
    rows near the window edges may be cut mid-turn and are left for the next, later window.
    `since` (seconds into the window) is where the previous pass stopped: rows before it are
    already final and never rewritten. `at` maps seconds into the window to wall time; the default
    is linear from t0, the live server passes the segmenter's clock (a new session's file starting
    inside the window jumps it). `edge` is how close to the window end a row may end: 0 when the window ends in
    a pause the VAD saw.
    """
    rows = []
    for g in sorted(segs, key=lambda g: g["start_ms"]):
        a, b, who = g["start_ms"] / 1000, g["end_ms"] / 1000, names[g["speaker"]]
        if rows and rows[-1]["speaker"] == who and rows[-1]["music"] == g.get("music") and a - rows[-1]["b"] < TURN_GAP_S:
            rows[-1]["b"], rows[-1]["text"] = b, rows[-1]["text"] + g["text"]
        else:
            rows.append(dict(a=a, b=b, speaker=who, text=g["text"], music=g.get("music")))
    lo, hi = since, win_s - edge
    keep = [r for r in rows if r["a"] >= lo - 0.05]  # turns are clipped to `since` by the caller
    cut = next((i for i, r in enumerate(keep) if r["b"] > hi), len(keep))
    to = keep[cut]["a"] if cut < len(keep) else hi
    at = at or (lambda s: t0 + dt.timedelta(seconds=s))
    out = [dict(id="f" + at(r["a"]).strftime("%Y%m%d%H%M%S%f")[:17], start=at(r["a"]).isoformat(timespec="milliseconds"),
                end=at(r["b"]).isoformat(timespec="milliseconds"), speaker=r["speaker"], text=r["text"].strip(),
                speaker_confidence=(conf or {}).get(r["speaker"]), state="final") | ({"music": 1} if r["music"] else {})
           for r in keep[:cut] if r["text"].strip()]
    return at(lo), at(to), out


def overlaps(lv: dict, c: dict) -> bool:
    """A live analysis (start to its last analyzed row, upto) and a conversation (start to end) share time: drafts and
    final rows give the same conversation slightly different starts."""
    return lv["start"] <= c["end"] and lv.get("upto", lv["start"]) >= c["start"]


def merge_drafts(finals: list[dict], drafts: list[dict], final_to: dict[str, str | None]) -> list[dict]:
    """What a page shows: final rows, and per device the drafts after its finalized time (before it, final rows
    cover the time; the drafts stay in the table for comparing the layers)."""
    return sorted(finals + [r for r in drafts if r["start"] >= (final_to.get(r.get("source")) or "")], key=lambda r: r["start"])
