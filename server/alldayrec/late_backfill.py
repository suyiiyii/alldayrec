"""Find historical late speech and enqueue it on the running instance. A dry run writes nothing.

    uv run python -m alldayrec.late_backfill --days 30 --dry-run

LIVE_RECORD_URL selects the server. LIVE_RECORD_TOKEN, else stdin's first line, supplies its token.
The manager runs the dry run first and obtains the owner's approval before running without --dry-run.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import anyio

from alldayrec import db, late_store, threads
from alldayrec.late import read_exact
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
                    if p.get("layer") != "final":
                        continue
                    a, b = [round(dt.datetime.fromisoformat(p[k]).timestamp() * 1000) for k in ("from", "to")]
                except (ValueError, KeyError, TypeError):
                    continue  # an interrupted last trace line cannot prove a final pass
                device = p.get("device")
                if device is None:
                    ds = [r[0] for r in db.conn().execute(
                        "SELECT DISTINCT device FROM pieces WHERE start_ms < ? AND start_ms + seconds * 1000 > ?", (b, a))]
                    if len(ds) != 1:
                        uncertain.append([a, b])
                        continue
                    device = ds[0]
                if "audio_ranges" in p:
                    ranges = [[round(dt.datetime.fromisoformat(x).timestamp() * 1000) for x in pair] for pair in p["audio_ranges"]]
                else:
                    # A legacy from/to envelope can jump over hours with no samples in the ASR window.
                    # Its existing final rows prove transcription; its gaps do not. Conservatively recheck
                    # remaining VAD speech rather than treating that envelope as continuous coverage.
                    ranges = [[round(dt.datetime.fromisoformat(r[k]).timestamp() * 1000) for k in ("start", "end")]
                              for r in db.sentences(dt.datetime.fromtimestamp(a / 1000).isoformat(),
                                                    dt.datetime.fromtimestamp(b / 1000).isoformat(), source=device)]
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
        rs = late_store.subtract([[a, b]], covers + passes.get(device, []) + pending)
        if any(x < b and y > a for x, y in uncertain):
            ambiguous += len(rs)
            continue  # do not guess which device an old final pass heard
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
                daily.append([a, end])
                a = end
        for a, b in daily:
            same_day = spans and dt.datetime.fromtimestamp(a / 1000).date() == dt.datetime.fromtimestamp(spans[-1][0][0] / 1000).date()
            if same_day and a <= spans[-1][-1][1] + late_store.JOIN_MS:
                spans[-1].append([a, b])
            else:
                spans.append([[a, b]])
        grouped[device] = spans
    return grouped, {"skipped_null_received_days": sorted(skipped), "skipped_ambiguous_spans": ambiguous}


async def scan(days: int, dry_run: bool = True) -> dict:
    grouped, info = await anyio.to_thread.run_sync(candidates, days, limiter=threads.DB)
    per_day, found = {}, []
    for device, spans in grouped.items():
        for ranges in spans:
            speech_ms = 0
            for a, b in ranges:
                seg, pos = Segmenter(), a
                while pos < b:
                    end = min(b, pos + 30_000)
                    x = await anyio.to_thread.run_sync(read_exact, device, pos, end, limiter=threads.MEDIA)
                    await seg.apush(dt.datetime.fromtimestamp(pos / 1000), x)
                    start_sample = round((pos - a) * SR / 1000)
                    speech_ms += sum(max(0, min(len(x) / SR, y) - max(0, z)) * 1000
                                     for z, y in speech_in(seg, start_sample, len(x)))
                    pos = end
            if not speech_ms:
                continue
            found.append((device, ranges))
            day = dt.datetime.fromtimestamp(ranges[0][0] / 1000).date().isoformat()
            d = per_day.setdefault(day, {"spans": 0, "minutes": 0.0, "speech_minutes": 0.0})
            d["spans"] += 1
            d["minutes"] += sum(b - a for a, b in ranges) / 60_000
            d["speech_minutes"] += speech_ms / 60_000
    enqueued = 0
    if not dry_run:
        now = round(time.time() * 1000)
        with db.tx() as t:
            for device, ranges in found:
                # Recheck after VAD: normal ingestion/processing could have added this same history meanwhile.
                old = [x for r in t.execute("SELECT ranges FROM late_spans WHERE device = ?", (device,)) for x in json.loads(r[0])]
                rs = late_store.subtract(ranges, old)
                for a, b in rs:
                    late_store.enqueue(t, device, a, b, now, ready=True)
                enqueued += bool(rs)
    return {"days": [{"date": d, **{k: round(v, 2) if isinstance(v, float) else v for k, v in x.items()}}
                     for d, x in sorted(per_day.items())], "dry_run": dry_run, "enqueued": enqueued, **info}


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
    print("Skipped NULL received_ms days: " + (", ".join(result["skipped_null_received_days"]) or "none"))
    print(f"Skipped ambiguous legacy spans: {result['skipped_ambiguous_spans']}; enqueued: {result['enqueued']}")
    return result


if __name__ == "__main__":
    main()
