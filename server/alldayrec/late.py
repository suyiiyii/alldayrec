"""One serial late-audio processor per tenant, beside its live device pipelines."""
import datetime as dt
import json
import logging
import os
import re
from contextlib import nullcontext

import anyio
import numpy as np

from alldayrec import asr, audio, db, late_merge, late_store, threads, ws
from alldayrec.conversations import GAP_S
from alldayrec.core import conversation_rows, iso, owner_npz, state
from alldayrec.diarist import Diarist, relabel
from alldayrec.segmenter import CHUNK_S, CUT_SEARCH_S, SR, Segmenter, next_cut, speech_in
from alldayrec.tenant import T
from alldayrec.transcript import final_rows, label_sentences

log = logging.getLogger("live")
LATE_PROCESS_TIMEOUT_S = float(os.environ.get("LATE_PROCESS_TIMEOUT_S", "1800"))


def read_exact(device: str, a: int, b: int) -> np.ndarray:
    """Exact [a,b) on its wall clock. Playback cut() pads and concatenates holes; neither fits ASR timestamps."""
    x = np.zeros(round((b - a) * SR / 1000), np.int16)
    end = a
    for p in db.conn().execute("SELECT * FROM pieces WHERE device = ? AND start_ms < ? AND start_ms + seconds * 1000 > ? ORDER BY start_ms",
                               (device, b, a)).fetchall():
        if p["start_ms"] + p["seconds"] * 1000 <= end:
            continue
        offset = max(0, round((max(a, end) - p["start_ms"]) * SR / 1000))
        start = p["start_ms"] + offset * 1000 / SR
        i = round((start - a) * SR / 1000)
        if i >= len(x):
            break
        # Long imports decode only this window; short pieces retain codec priming/dehum.
        y = audio.piece_window(p, offset, len(x) - i)
        if y is None:
            raise ValueError("stored late audio could not be decoded")
        n = min(len(y), len(x) - i)
        if n > 0 and i >= 0:
            x[i:i + n] = y[:n]
            end = start + n * 1000 / SR
    return x


async def transcribe(span: dict) -> tuple[list[dict], dict]:
    if not await quiet_seam(span):
        return [], {}  # the final reader invalidated this claim during lookahead
    vp = owner_npz()
    with np.load(vp) if vp.exists() else nullcontext({}) as bank:
        owner = dict(bank)
        dia = Diarist(owner, people={k: e for k, (_, e) in db.people().items()})
    rows, embeddings, windows = [], {}, []
    # Renames during model calls teach this diarist just like a live one.
    T().dias.append(dia)
    try:
        for a, b in json.loads(span["ranges"]):
            # Feed at most one final window at a time; a long backlog never becomes a whole-day PCM allocation.
            seg, utts, done = Segmenter(), [], 0
            at0 = dt.datetime.fromtimestamp(a / 1000)
            pos = a
            while pos < b or done < seg.samples:
                if pos < b:
                    end = min(b, pos + 30_000)
                    x = await anyio.to_thread.run_sync(read_exact, span["device"], pos, end, limiter=threads.MEDIA)
                    utts += [(u[0], u[1]) for u in await seg.apush(dt.datetime.fromtimestamp(pos / 1000), x)]
                    pos = end
                while done < seg.samples:
                    relative = [((s - at0).total_seconds() - done / SR, (e - at0).total_seconds() - done / SR)
                                for s, e in utts if (e - at0).total_seconds() > done / SR]
                    cut = next_cut(relative, (seg.samples - done) / SR, seg.speaking(), force=pos >= b)
                    if cut is None:
                        break
                    n = min(seg.samples - done, round(min(cut, CHUNK_S) * SR))
                    if n <= 0:
                        done = seg.samples
                        break
                    x = seg.audio[done - seg.audio_base:done - seg.audio_base + n]
                    base = at0 + dt.timedelta(seconds=done / SR)
                    speech = speech_in(seg, done, n)
                    segs = await asr.engine_segs(x, speech, kind="late", device=span["device"]) if speech and n >= SR // 40 else []
                    raw = [dict(s) for s in segs]
                    marks = await anyio.to_thread.run_sync(label_sentences, dia, state["embed"], segs, x, state["music"], limiter=threads.INFER)
                    # Clamp providers' edge jitter to this exact uncovered range before final_rows.
                    for g in segs:
                        g["start_ms"] = max(0, min(n * 1000 / SR, g["start_ms"]))
                        g["end_ms"] = max(g["start_ms"], min(n * 1000 / SR, g["end_ms"]))
                    windows.append((base, n, segs, marks))
                    lo, hi = base, base + dt.timedelta(seconds=n / SR)
                    with open(T().data / f"{lo.date()}.passes.jsonl", "a") as f:
                        f.write(json.dumps({"at": iso(dt.datetime.now()), "layer": "late", "device": span["device"],
                                            "span": span["id"], "from": iso(lo), "to": iso(hi), "segs": raw}, ensure_ascii=False) + "\n")
                    done += n
                if pos >= b and seg.samples - done <= SR // 20:
                    break
        # The live layer relabels at close with all the evidence in view. Do the same for these
        # new sentences: a fresh Diarist's first few S? labels must not become permanent.
        gs = [(base, g, m) for base, _, segs, marks in windows for g, m in zip(segs, marks)]
        es = [dia.E[m[0] - 1] if m else None for _, _, m in gs]
        labels = await anyio.to_thread.run_sync(
            lambda: relabel(es, [(g["end_ms"] - g["start_ms"]) / 1000 for _, g, _ in gs], owner,
                            [(iso(base), g.get("engine_speaker")) if g.get("engine_speaker") and not g.get("music") else ("alone", i)
                             for i, (base, g, _) in enumerate(gs)], people=dia.people), limiter=threads.INFER)
        for (_, g, _), e, label in zip(gs, es, labels):
            if re.fullmatch(r"S(?:\d+|\?)", g["speaker"]):
                g["speaker"] = label
            if e is not None:
                embeddings.setdefault(g["speaker"], []).append(e)
        for base, n, segs, _ in windows:
            rs = final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, base, n / SR, since=0, edge=0)[2]
            for r in rs:
                r.update(source=span["device"], engine=segs[0]["engine"], shown_at=iso(dt.datetime.now()))
            rows += rs
        return rows, {k: late_merge.unit(np.mean(es, 0)) for k, es in embeddings.items()}
    finally:
        T().dias.remove(dia)


async def quiet_seam(span):
    """A bounded VAD-only lookahead; ASR still hears each sample in exactly one job."""
    ranges = json.loads(span["ranges"])
    # Length cannot distinguish a chosen seam from a short remainder of the next job.
    if span.get("seam_ms") == span["end_ms"]:
        return True
    if not db.conn().execute("SELECT 1 FROM late_spans WHERE device=? AND start_ms=? "
                             "AND status IN ('waiting','deferred') LIMIT 1",
                             (span["device"], span["end_ms"])).fetchone():
        return True
    a, b = ranges[-1]
    a = max(a, b - round(CUT_SEARCH_S * 1000))
    if b - a < 2 * late_store.MIN_LATE_MS:
        return True
    x = await anyio.to_thread.run_sync(read_exact, span["device"], a, b, limiter=threads.MEDIA)
    seg = Segmenter()
    await seg.apush(dt.datetime.fromtimestamp(a / 1000), x)
    # A VAD max-utterance split ends at a frame's start and resumes at the next frame.
    # Include that last 10 ms frame: its bookkeeping gap is not an acoustic silence.
    speech = [[max(a, a + round(lo * 1000)), min(b, a + round(hi * 1000) + 10)]
              for lo, hi in speech_in(seg, 0, len(x))]
    pauses = late_store.subtract([[a, b]], speech)
    # Keep the cut on the VAD's 10 ms frame grid, including when its midpoint is at 5 ms.
    pauses = [(hi - lo, (lo + hi) // 20 * 10) for lo, hi in pauses if hi > lo]
    pauses = [(width, cut) for width, cut in pauses
              if ranges[-1][0] + late_store.MIN_LATE_MS <= cut <= b - late_store.MIN_LATE_MS]
    if not pauses:
        return True  # no VAD silence: the hard cap remains the bounded fallback
    return late_store.move_seam(span, max(pauses)[1])


async def conversation_vectors(span: dict) -> dict:
    lo = iso(dt.datetime.fromtimestamp(span["start_ms"] / 1000) - dt.timedelta(seconds=GAP_S))
    hi = iso(dt.datetime.fromtimestamp(span["end_ms"] / 1000) + dt.timedelta(seconds=GAP_S))
    cs = [db._conversation(r) for r in db.conn().execute(
        "SELECT * FROM conversations WHERE device = ? AND start <= ? AND end >= ?", (span["device"], hi, lo))]
    out = {}
    for c in cs:
        vs = {r["label"]: db.vec(r["e"]) for r in db.conn().execute("SELECT * FROM voices WHERE conv = ? AND e IS NOT NULL", (c["id"],))}
        missing = set(c["participants"]) - set(vs)
        if missing and state.get("embed"):
            # Closed conversations may not have reached the five-minute voices loop (or be historical).
            rs = conversation_rows(c)
            for label in missing:
                es = []
                for r in sorted((r for r in rs if r.get("speaker") == label and not r.get("music")),
                                key=lambda r: (r["end"], r["start"]), reverse=True):
                    a = round(dt.datetime.fromisoformat(r["start"]).timestamp() * 1000)
                    b = min(a + 15_000, round(dt.datetime.fromisoformat(r["end"]).timestamp() * 1000))
                    if b - a < 1500:
                        continue
                    x = await anyio.to_thread.run_sync(read_exact, span["device"], a, b, limiter=threads.MEDIA)
                    es.append(await anyio.to_thread.run_sync(state["embed"], x, limiter=threads.INFER))
                    if len(es) >= 4:
                        break
                if es:
                    vs[label] = late_merge.unit(np.mean(es, 0))
        out[c["id"]] = vs
    return out


async def process(span: dict):
    rows, vectors = await transcribe(span)
    existing = await conversation_vectors(span) if rows else {}
    changed, removed = late_merge.attach(span, rows, vectors, existing)
    # One range update replaces drafts and refreshes an open transcript with existing edited rows included.
    if changed:
        lo = min(c["start"] for c in changed)
        hi = iso(dt.datetime.fromisoformat(max(c["end"] for c in changed)) + dt.timedelta(milliseconds=1))
        await ws.broadcast({"type": "final", "device": span["device"], "from": lo, "to": hi,
                            "rows": db.sentences(lo, hi, source=span["device"])})
    for old in removed:
        await ws.broadcast({"type": "conversation_removed", "id": old, "new": db.resolve_conversation(old)})
    for c in changed:
        await ws.broadcast({"type": "conversation", **c})


async def processor():
    late_store.recover()
    while True:
        span = late_store.claim()
        if not span:
            late_store.queue_summaries()
            await anyio.sleep(2)
            continue
        try:
            with anyio.fail_after(LATE_PROCESS_TIMEOUT_S):
                await process(span)
        except Exception as e:
            late_store.failed(span, e)
            log.warning("late span %s: %s", span["id"], type(e).__name__)
        late_store.queue_summaries()
