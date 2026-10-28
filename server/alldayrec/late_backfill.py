"""Find historical late speech and enqueue it on the running instance. A dry run writes nothing.

    uv run python -m alldayrec.late_backfill --days 30 --dry-run

LIVE_RECORD_URL selects the server. LIVE_RECORD_TOKEN, else stdin's first line, supplies its token.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from contextlib import nullcontext

import anyio

from alldayrec import db, late_merge, late_store, threads
from alldayrec.late import read_exact
from alldayrec.conversations import GAP_S, split
from alldayrec.segmenter import SR, Segmenter, speech_in
from alldayrec.tenant import T


def candidates(days: int, today: dt.date | None = None) -> tuple[dict, dict]:
    today = today or dt.date.today()
    first = today - dt.timedelta(days=days - 1)
    lo = round(dt.datetime.combine(first, dt.time()).timestamp() * 1000)
    hi = round(dt.datetime.combine(today + dt.timedelta(days=1), dt.time()).timestamp() * 1000)
    out, skipped, ambiguous = {}, set(), 0
    # New traces carry device. Legacy traces can be attributed only when their audio belongs to one device.
    passes = {}
    uncertain = []
    for path in sorted(T().data.glob("*.passes.jsonl")):
        if not (str(first - dt.timedelta(days=1)) <= path.name[:10] <= str(today)):
            continue
        with path.open() as f:
            for line in f:
                try:
                    p = json.loads(line)
                    if not isinstance(p, dict):
                        raise ValueError("invalid pass record")
                    if p.get("layer") != "final":
                        continue
                    a, b = [round(dt.datetime.fromisoformat(p[k]).timestamp() * 1000) for k in ("from", "to")]
                except (ValueError, KeyError, TypeError):
                    day = dt.date.fromisoformat(path.name[:10])
                    start = round(dt.datetime.combine(day, dt.time()).timestamp() * 1000)
                    end = round(dt.datetime.combine(day + dt.timedelta(days=1), dt.time()).timestamp() * 1000)
                    uncertain.append((None, start, end))
                    continue  # an interrupted trace cannot prove that its day's remaining speech was missed
                device = p.get("device")
                if device is None:
                    ds = [r[0] for r in db.conn().execute(
                        "SELECT DISTINCT source FROM sentences WHERE start >= ? AND start < ? AND source IS NOT NULL",
                        (p["from"], p["to"]))]
                    if not ds:
                        ds = [r[0] for r in db.conn().execute(
                            "SELECT DISTINCT device FROM pieces WHERE start_ms < ? AND start_ms + seconds * 1000 > ?", (b, a))]
                    if len(ds) != 1:
                        uncertain.append((None, a, b))
                        continue
                    device = ds[0]
                try:
                    if "audio_ranges" in p:
                        ranges = [[round(dt.datetime.fromisoformat(x).timestamp() * 1000) for x in pair] for pair in p["audio_ranges"]]
                        if any(len(r) != 2 or r[1] <= r[0] for r in ranges):
                            raise ValueError("invalid coverage")
                    else:
                        # Legacy passes heard whole pieces already received at completion, including pauses
                        # and sentence edges. Their rows alone cannot establish acoustic coverage.
                        completed = round(dt.datetime.fromisoformat(p["at"]).timestamp() * 1000)
                        ranges = [[max(a, r[0]), min(b, round(r[0] + r[1] * 1000))] for r in db.conn().execute(
                            "SELECT start_ms,seconds FROM pieces WHERE device=? AND start_ms < ? AND start_ms + seconds * 1000 > ? "
                            "AND received_ms IS NOT NULL AND received_ms <= ?", (device, b, a, completed))]
                except (ValueError, KeyError, TypeError):
                    uncertain.append((device, a, b))
                    continue
                passes.setdefault(device, []).extend(ranges)
    # Descending per-device scan: the minimum receipt of a later piece is an O(n) suffix minimum.
    # Do not materialize a month's one-second pieces or run a history scan for each piece.
    future = {r[0]: r[1] for r in db.conn().execute("SELECT device,min(received_ms) FROM pieces WHERE start_ms >= ? GROUP BY device", (hi,))}
    ps = db.conn().execute("SELECT device,start_ms,seconds,received_ms FROM pieces WHERE start_ms + seconds * 1000 > ? "
                           "AND start_ms < ? ORDER BY device,start_ms DESC", (lo, hi))
    for p in ps:
        a, b, device = max(lo, p["start_ms"]), min(hi, round(p["start_ms"] + p["seconds"] * 1000)), p["device"]
        newer = future.get(device)
        if p["received_ms"] is None:
            skipped.add(dt.datetime.fromtimestamp(a / 1000).date().isoformat())
            continue
        future[device] = min(newer, p["received_ms"]) if newer is not None else p["received_ms"]
        if newer is None or newer >= p["received_ms"]:
            continue
        covers = [[r[0], round(r[0] + r[1] * 1000)] for r in db.conn().execute(
            "SELECT start_ms,seconds FROM pieces WHERE device = ? AND start_ms < ? AND start_ms + seconds * 1000 > ? "
            "AND (received_ms IS NULL OR received_ms < ?)", (device, b, a, p["received_ms"]))]
        pending = [x for r in db.conn().execute("SELECT ranges FROM late_spans WHERE device = ? AND start_ms < ? AND end_ms > ?", (device, b, a))
                   for x in json.loads(r[0])]
        final = passes.get(device, []) + db.processed(device, a, b)
        rs = late_store.substantial(late_store.subtract([[a, b]], covers + final + pending))
        if any((d is None or d == device) and x < b and y > a for d, x, y in uncertain):
            ambiguous += len(rs)
            continue  # no device/receipt evidence: skip, never reinterpret sentence edges as missing speech
        out.setdefault(device, []).extend(rs)
    # Merge neighboring arrivals as the live ledger does, but preserve exact ranges across small covered holes.
    grouped = {}
    for device, ranges in out.items():
        spans = []
        daily = []
        for a, b in late_store.union(ranges):
            while a < b:
                midnight = dt.datetime.combine(dt.datetime.fromtimestamp(a / 1000).date() + dt.timedelta(days=1), dt.time())
                end = min(b, round(midnight.timestamp() * 1000))
                if end - a >= late_store.MIN_LATE_MS:
                    daily.append([a, end])
                a = end
        for a, b in daily:
            same_day = spans and dt.datetime.fromtimestamp(a / 1000).date() == dt.datetime.fromtimestamp(spans[-1][0][0] / 1000).date()
            if same_day and a <= spans[-1][-1][1] + late_store.JOIN_MS:
                spans[-1].append([a, b])
            else:
                spans.append([[a, b]])
        grouped[device] = [part for ranges in spans for part in late_store.parts(ranges)]
    return grouped, {"skipped_null_received_days": sorted(skipped), "skipped_ambiguous_spans": ambiguous}


def estimates(plans) -> dict:
    # Job boundaries are commit boundaries, not conversation boundaries. Estimate a continuous
    # stretch together, as before the cap, rather than billing one summary for every part.
    stretches = []
    for device, ranges, speech in plans:
        if (stretches and stretches[-1][0] == device
                and ranges[0][0] <= stretches[-1][1][-1][1] + late_store.JOIN_MS
                and dt.datetime.fromtimestamp(ranges[0][0] / 1000).date()
                == dt.datetime.fromtimestamp(stretches[-1][1][0][0] / 1000).date()):
            stretches[-1][1].extend(ranges)
            stretches[-1][2].extend(speech)
        else:
            stretches.append((device, list(ranges), list(speech)))
    targets, new_count, days = set(), 0, set()
    for device, ranges, speech_ms in stretches:
        lo, hi = ranges[0][0], ranges[-1][1]
        stamp = lambda ms: dt.datetime.fromtimestamp(ms / 1000).isoformat(timespec="milliseconds")
        rows = [{"start": stamp(a), "end": stamp(b), "speaker": "S1"} for a, b in speech_ms]
        convs = [db._conversation(r) for r in db.conn().execute(
            "SELECT * FROM conversations WHERE device=? AND start<=? AND end>=? ORDER BY start",
            (device, stamp(hi + GAP_S * 1000), stamp(lo - GAP_S * 1000)))]
        for comp in late_merge.components(rows, convs):
            if not comp["rows"]:
                continue
            cs = comp["convs"]
            if cs:
                targets.add(cs[0]["id"])
                days.update(c[k][:10] for c in cs for k in ("start", "end"))
            else:
                new_count += len(split(comp["rows"]))
            days.update(r["start"][:10] for r in comp["rows"])
    return {"estimated_conversations": len(targets) + new_count,
            "estimated_recaps": sum(bool(db.kv(f"recap:{day}")) for day in days)}


async def scan(days: int, dry_run: bool = True) -> dict:
    grouped, info = await anyio.to_thread.run_sync(candidates, days, limiter=threads.DB)
    plans = []
    for device, spans in grouped.items():
        for ranges in spans:
            speech = []
            for a, b in ranges:
                seg, pos = Segmenter(), a
                while pos < b:
                    end = min(b, pos + 30_000)
                    x = await anyio.to_thread.run_sync(read_exact, device, pos, end, limiter=threads.MEDIA)
                    await seg.apush(dt.datetime.fromtimestamp(pos / 1000), x)
                    start_sample = round((pos - a) * SR / 1000)
                    speech += [[max(pos, round(pos + z * 1000)), min(end, round(pos + y * 1000))]
                               for z, y in speech_in(seg, start_sample, len(x)) if min(len(x) / SR, y) > max(0, z)]
                    pos = end
            speech = late_store.union(speech)
            if sum(b - a for a, b in speech) >= late_store.MIN_LATE_MS:
                plans.append((device, ranges, speech))
    accepted = []
    now = round(time.time() * 1000)
    enqueued = 0
    with db.tx() if not dry_run else nullcontext(db.conn()) as t:
        for device, ranges, speech in plans:
            if not dry_run:
                old = [x for r in t.execute("SELECT ranges FROM late_spans WHERE device=? AND start_ms < ? AND end_ms > ?",
                       (device, ranges[-1][1], ranges[0][0])) for x in json.loads(r[0])]
                old += db.processed(device, ranges[0][0], ranges[-1][1])
                if late_store.subtract(ranges, old) != ranges:
                    info["skipped_changed_spans"] = info.get("skipped_changed_spans", 0) + 1
                    continue  # a concurrent live/late commit invalidated the plan: never enqueue un-VADed fragments
                late_store.enqueue_ranges(t, device, ranges, now, ready=True, join=False)
                enqueued += 1
            accepted.append((device, ranges, speech))
    per_day = {}
    for _, ranges, speech in accepted:
        day = dt.datetime.fromtimestamp(ranges[0][0] / 1000).date().isoformat()
        d = per_day.setdefault(day, {"spans": 0, "minutes": 0.0, "speech_minutes": 0.0})
        d["spans"] += 1
        d["minutes"] += sum(b - a for a, b in ranges) / 60_000
        d["speech_minutes"] += sum(b - a for a, b in speech) / 60_000
    return {"days": [{"date": d, **{k: round(v, 2) if isinstance(v, float) else v for k, v in x.items()}}
                     for d, x in sorted(per_day.items())], "dry_run": dry_run, "enqueued": enqueued,
            **info, **estimates(accepted)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    if not 1 <= a.days <= 366:
        p.error("days must be between 1 and 366")
    url = os.environ.get("LIVE_RECORD_URL") or sys.exit("LIVE_RECORD_URL is not set")
    token = os.environ.get("LIVE_RECORD_TOKEN") or ("" if sys.stdin.isatty() else sys.stdin.readline().strip())
    query = urllib.parse.urlencode({"days": a.days, "dry_run": str(a.dry_run).lower()})
    req = urllib.request.Request(url.rstrip("/") + "/api/late-backfill?" + query, method="POST",
                                 headers={"Authorization": "Bearer " + token} if token else {})
    # VAD over history may take minutes. An uncertain result is never automatically retried.
    with urllib.request.urlopen(req, timeout=6 * 3600) as response:
        result = json.load(response)
    for day in result["days"]:
        print(f"{day['date']}: {day['spans']} spans, {day['minutes']:.2f} minutes, {day['speech_minutes']:.2f} speech minutes")
    print(f"Estimated summaries: {result['estimated_conversations']}; estimated recaps: {result['estimated_recaps']}")
    print("Skipped NULL received_ms days: " + (", ".join(result["skipped_null_received_days"]) or "none"))
    print(f"Skipped ambiguous legacy spans: {result['skipped_ambiguous_spans']}; enqueued: {result['enqueued']}")
    return result


if __name__ == "__main__":
    main()
