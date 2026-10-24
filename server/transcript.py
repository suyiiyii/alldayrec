"""A window's engine sentences become rows: the turns already final clipped off, speakers labelled by the diarist, the
final rows of a span, and how drafts and live analyses line up with them."""
import datetime as dt
import os

import numpy as np

from segmenter import SR


TURN_GAP_S = float(os.environ.get("TURN_GAP_S", "1.0"))  # same speaker, closer than this: one final row; 0 keeps the engine's sentences as rows
EDGE_S = 3.0                               # rows touching the window edges may be cut: left to the next pass


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


def label_sentences(dia, embed, segs: list[dict], x: np.ndarray) -> list:
    """Speaker labels from the conversation clustering, in time order; returns the diarist mark after each
    sentence (None when too short to embed) so a pass can take back the sentences it leaves open."""
    marks = []
    for g in segs:
        a, b = g["start_ms"] / 1000, g["end_ms"] / 1000
        g["engine_speaker"] = g.get("speaker")
        if b - a < 0.3:
            g["speaker"], g["confidence"] = "S?", 0.0
            marks.append(None)
            continue
        g["speaker"], g["confidence"] = dia.add(embed(x[int(a * SR):int(min(b, a + 15) * SR)]), b - a)
        marks.append(dia.mark())
    return marks


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
        if rows and rows[-1]["speaker"] == who and a - rows[-1]["b"] < TURN_GAP_S:
            rows[-1]["b"], rows[-1]["text"] = b, rows[-1]["text"] + g["text"]
        else:
            rows.append(dict(a=a, b=b, speaker=who, text=g["text"]))
    lo, hi = since, win_s - edge
    keep = [r for r in rows if r["a"] >= lo - 0.05]  # turns are clipped to `since` by the caller
    cut = next((i for i, r in enumerate(keep) if r["b"] > hi), len(keep))
    to = keep[cut]["a"] if cut < len(keep) else hi
    at = at or (lambda s: t0 + dt.timedelta(seconds=s))
    out = [dict(id="f" + at(r["a"]).strftime("%Y%m%d%H%M%S%f")[:17], start=at(r["a"]).isoformat(timespec="milliseconds"),
                end=at(r["b"]).isoformat(timespec="milliseconds"), speaker=r["speaker"], text=r["text"].strip(),
                speaker_confidence=(conf or {}).get(r["speaker"]), state="final")
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
