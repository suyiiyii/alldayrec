"""Sound events (environmental sound, docs: reference/data, sound_wrong; explanation/privacy, 环境声音): what the sounds
table's scores say a device's minute had, only the events the scores tell well, every one a guess the pages word as
「可能」. Everything shown of them (a conversation's tags, a day's bar, the MCP tools, the sound filter of search) is made
here from the stored scores as asked, nothing kept but the corrections (sound_wrong). Sound events only: never a place
(where one was is the summary's to say from the words).

A minute's events, from one model's scores (SOUND_EVENTS_MODEL, default the first of SOUND_MODEL) and its levels:
- speech, music, vehicle: the event's classes' highest mean over the minute's frames (sounds.minutes) at THRESH or
  more; vehicle only in a run of VEHICLE_RUN minutes or more (one passing car is not a ride);
- quiet: the minute's level (the mean power of its frames; none in a row's gaps, none at the -127 floor: digital zeros,
  the microphone sent nothing) under QUIET_DB, with no music; speech is not said of a quiet minute.
A minute with no frame left is no data, never quiet. Each event's conf: the share of the minute's frames that had it
(a class at sounds.HIT or more; quiet: the frames under QUIET_DB); yamnet's music, on at 0.1 under HIT, can be 0.

Left out, the scores do not tell them (the owner's labels, sound-bench v1): media (TV, a video, a call on speaker: all
8 heard as speech), typing, footsteps, rubbing, wind, crowd, hum, dishes, turn signals.

Off (the tenant's "sound" false, no SOUND_MODEL: sounds.on) or no rows: none of it, every page and tool as before.
"""
import datetime as dt
import json
import os
import time

import numpy as np

from alldayrec import db, sound, sounds

EVENTS = ("speech", "music", "vehicle", "quiet")
# AudioSet classes of each event, in each model's own order (YAMNet's yamnet_class_map.csv, mn10_as's 527 AudioSet
# labels): speech = Speech, the speech kinds and Conversation; music = every music, singing and instrument class;
# vehicle = vehicles, engines, traffic, rail, aircraft (one event for a car, a bus, a subway or a plane: the scores do
# not tell them apart)
CLASSES = {
    "mn10_as": {
        "speech": [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 15],
        "music": [27, 28, 30, 35, 36, 137, 138, 139, 140, 142, 146, 149, 151, 152, 153, 155, 158, 161, 162, 163, 164, 167, 168, 175,
                  180, 182, 184, 185, 187, 189, 191, 194, 195, 196, 197, 200, 202, 205, 208, 209, 214, 216, 217, 218, 219, 224, 227,
                  228, 230, *range(232, 250), 251, 252, 253, 254, 256, *range(258, 263), *range(264, 283), 495],
        "vehicle": [300, 301, *range(304, 309), 310, 312, 313, 314, 316, 321, *range(327, 338), 339, 340, 343, *range(350, 354), 492],
    },
    "yamnet": {
        "speech": [0, 1, 2, 3, 4, 5, 6, 9, 12],
        "music": [24, 25, 27, 30, 31, 132, 133, 134, 135, 137, 141, 144, 146, 147, 148, 150, 153, 156, 157, 158, 159, 162, 163, 170,
                  175, 177, 179, 180, 182, 184, 186, 189, 190, 191, 192, 195, 197, 200, 203, 204, 209, 211, 212, 213, 214, 219, 222,
                  223, 225, *range(227, 245), 246, 247, 248, 249, 251, *range(253, 258), *range(259, 277), 489],
        "vehicle": [294, 295, *range(298, 303), 304, 306, 307, 308, 310, 315, *range(321, 332), 333, 334, 337, *range(344, 348), 486],
    },
}
assert CLASSES["mn10_as"]["music"][5] == sounds.MUSIC and CLASSES["yamnet"]["music"][5] == 132  # Music: the orders are AudioSet's
# The minute's mean score from which an event is said, per model. Set on the owner's labelled 10 s windows (sound-bench
# v1: 84 with events, calibrated, provisional mappings left out), the statistic a minute's mean is of one window:
# - mn10_as: speech 0.3, quiet ones left out, heard 24/26 of the windows with speech, 24/40 of those it named had it
#   (the rest mostly media: 8/8 media windows score as speech); music 0.3: 12/13, 12/13 (0.2: 13/13 but 13/20); vehicle 0.3: 3/4, 3/6, with
#   the run of VEHICLE_RUN minutes against the rest.
# - yamnet (the window's mean): speech 0.2, quiet ones left out: 23/26, 23/35; music 0.1: 6/13, 6/6 (YAMNet hears little music: 0.2 is
#   5/13); vehicle 0.2: 2/4, 2/2.
THRESH = {"mn10_as": {"speech": 0.3, "music": 0.3, "vehicle": 0.3}, "yamnet": {"speech": 0.2, "music": 0.1, "vehicle": 0.2}}
# Quiet by the level alone, under -55 dBFS calibrated, with no music: 13/17 of the quiet windows, 13/16 of those named
# (YAMNet's Silence: 17/17 but 17/28). Audio with no calibration (gain_db 0): under -60, the bench's uncalibrated cut.
QUIET_DB, QUIET_DB_RAW = -55, -60
VEHICLE_RUN = 3  # minutes in a row
# A conversation's tags: an event of at least TAG_MIN minutes and TAG_SHARE of its minutes with scores, most first, at
# most TAGS; speech is not one (every conversation has it); quiet over half of them is 大半时间安静 (mostly)
TAG_MIN, TAG_SHARE, TAGS, MOSTLY = 2, 0.1, 3, 0.5
# the conversations a sound search looks at per call (each one's rows decoded, about 0.5 ms): a call stays under 100 ms
# on a month of rows (test_big_store_fast) and the DB threads free; the caller goes on from where it stopped
SCAN = 100


def model() -> str | None:
    """The model the events are made from, None while off (sounds.on)."""
    return (os.environ.get("SOUND_EVENTS_MODEL") or sound.names()[0]) if sounds.on() else None


def check():
    """SystemExit on a SOUND_EVENTS_MODEL that is not one of SOUND_MODEL's (at the server's start)."""
    if (m := os.environ.get("SOUND_EVENTS_MODEL")) and m not in sound.names():
        raise SystemExit(f"SOUND_EVENTS_MODEL: {m} is not one of SOUND_MODEL's ({', '.join(sound.names()) or 'none'})")


def ms(t: str) -> int:
    return round(dt.datetime.fromisoformat(t).timestamp() * 1000)


def levels(device: str, name: str, a: int, b: int) -> dict[int, tuple[float, int, int, int]]:
    """Unix minute -> (level dBFS, frames, frames under the quiet cut, the cut) over [a, b): the frames by their middle,
    none in a row's gaps or at the -127 floor; the cut QUIET_DB, QUIET_DB_RAW where a row of it had no calibration."""
    out = {}
    for s, e, hop, win, n, lv, gaps, g in db.conn().execute(
            f"SELECT start_ms, end_ms, hop_ms, window_ms, n, db, gaps, gain_db FROM sounds WHERE device = ? AND model = ? AND {sounds.SPAN}",
            (device, sound.model_id(name), *sounds.span_args(a, b))):
        i = np.arange(n)
        mid = np.minimum(s + i * hop + np.minimum(win, e - s - i * hop) // 2, e - 1)
        lv = np.frombuffer(lv, np.int8).astype(np.float64)
        keep = (lv > -127) & (mid >= a) & (mid < b)
        for x, y in json.loads(gaps):
            keep &= (mid < x) | (mid >= y)
        cut = QUIET_DB if g else QUIET_DB_RAW  # shortcut: gain_db 0 reads as uncalibrated, a gain registered as exactly 0 dB too
        ms_, k = np.unique(mid[keep] // 60000, return_inverse=True)
        p, c, q = np.bincount(k, 10 ** (lv[keep] / 10), len(ms_)), np.bincount(k, None, len(ms_)), np.bincount(k, lv[keep] < cut, len(ms_))
        for m, p1, n1, q1 in zip(ms_.tolist(), p.tolist(), c.tolist(), q.tolist()):
            p0, n0, q0, c0 = out.get(m, (0.0, 0, 0, QUIET_DB))
            out[m] = (p0 + p1, n0 + int(n1), q0 + int(q1), min(c0, cut))
    return {m: (10 * np.log10(p / n), n, q, cut) for m, (p, n, q, cut) in out.items()}


def minutes(device: str, a: int, b: int) -> dict[int, dict[str, float]]:
    """Unix minute -> {event: conf} of device's minutes in [a, b) with scores (an empty dict: heard, none of EVENTS);
    a minute with none is not in it. Off: {}."""
    if not (name := model()):
        return {}
    pad = VEHICLE_RUN * 60000  # a ride across a or b still counts its whole run
    (ms_, frames, mean, hits), lv, th = sounds.minute_rows(device, name, a - pad, b + pad), levels(device, name, a - pad, b + pad), THRESH[name]
    row = {m: i for i, m in enumerate(ms_.tolist())}
    on = {e: (mean[:, CLASSES[name][e]].max(1) >= th[e]).tolist() for e in ("speech", "music", "vehicle")} if len(ms_) else {}
    conf = {e: (hits[:, CLASSES[name][e]].max(1) / frames).tolist() for e in on}
    out = {}
    for m, (level, n, below, cut) in sorted(lv.items()):
        if (i := row.get(m)) is None:
            continue
        ev = {e: round(conf[e][i], 2) for e in on if on[e][i]}
        if level < cut and "music" not in ev:
            ev.pop("speech", None)
            ev["quiet"] = round(below / n, 2)
        out[m] = ev
    run = []
    for m in [*sorted(out), None]:  # vehicle only in runs of VEHICLE_RUN minutes or more
        if m is not None and "vehicle" in out[m] and (not run or run[-1] == m - 1):
            run.append(m)
            continue
        if len(run) < VEHICLE_RUN:
            for r in run:
                out[r].pop("vehicle")
        run = [m] if m is not None and "vehicle" in out[m] else []
    return {m: ev for m, ev in out.items() if a // 60000 <= m <= (b - 1) // 60000}


def wrong_ids(device: str, a: int, b: int) -> dict[str, int]:
    """event -> the owner's 不对 (sound_wrong id; the newest of several) on device covering more than half of [a, b):
    still said of a conversation grown by late audio or merged since, or of the bar's runs cut anew; any model."""
    out = {}
    for i, e, s, t in db.conn().execute("SELECT id, event, start_ms, end_ms FROM sound_wrong WHERE device = ? AND start_ms < ? AND end_ms > ? ORDER BY id",
                                        (device, b, a)):
        if 2 * (min(t, b) - max(s, a)) > b - a:
            out[e] = i
    return out


def tags(c: dict) -> dict | None:
    """A conversation's sound: {"covered": minutes with scores, "events": [{event, minutes, conf, wrong?}], from, to,
    device} (TAG_MIN, TAG_SHARE, TAGS); None while off, with no scores or no tag."""
    if not c.get("device") or not model():
        return None
    a, b = ms(c["start"]), ms(c["end"]) + 1000  # its end is its last sentence's end, to the second
    mins = minutes(c["device"], a, b)
    if not mins:
        return None
    n, wrong = len(mins), wrong_ids(c["device"], a, b)
    out = []
    for e in ("music", "vehicle", "quiet"):
        on = [x[e] for x in mins.values() if e in x]
        if len(on) >= TAG_MIN and len(on) >= TAG_SHARE * n:
            out.append({"event": e, "minutes": len(on), "conf": round(sum(on) / len(on), 2),
                        **({"mostly": True} if e == "quiet" and len(on) > MOSTLY * n else {}), **({"wrong": wrong[e]} if e in wrong else {})})
    out = sorted(out, key=lambda x: -x["minutes"])[:TAGS]
    return {"covered": n, "events": out, "device": c["device"], "from": a, "to": b} if out else None


def with_tags(cs: list[dict]) -> list[dict]:
    """Conversations with their "sounds" (tags) where they have some: off, the list as it was."""
    if not model():
        return cs
    return [{**c, "sounds": s} if (s := tags(c)) else c for c in cs]


def runs(device: str, a: int, b: int) -> list[dict]:
    """device's minutes in [a, b) as runs of the same events (and the same conversation): {from, to (ms), events
    {event: conf}, conv (id or None), wrong {event: id}}; no scores: no run (a gap between two)."""
    mins = minutes(device, a, b)
    if not mins:
        return []
    convs = [(ms(c["start"]), ms(c["end"]) + 1000, c["id"]) for c in db.conversations(
        dt.datetime.fromtimestamp(a / 1000 - 86400).isoformat(), dt.datetime.fromtimestamp(b / 1000).isoformat()) if c.get("device") == device]
    conv = lambda m: next((i for s, e, i in convs if s < (m + 1) * 60000 and m * 60000 < e), None)
    out = []
    for m in sorted(mins):
        key, cid = sorted(mins[m]), conv(m)
        if out and out[-1]["to"] == m * 60000 and out[-1]["_k"] == key and out[-1]["conv"] == cid:
            out[-1]["to"] += 60000
            out[-1]["_c"].append(mins[m])
        else:
            out.append({"from": m * 60000, "to": (m + 1) * 60000, "events": {}, "conv": cid, "_k": key, "_c": [mins[m]]})
    for r in out:
        cs, _ = r.pop("_c"), r.pop("_k")
        r["events"] = {e: round(sum(x[e] for x in cs) / len(cs), 2) for e in cs[0]}
        r["wrong"] = wrong_ids(device, r["from"], r["to"])
    return out


def devices(a: int, b: int) -> list[str]:
    """Devices with rows of the events' model over [a, b)."""
    if not (name := model()):
        return []
    out, d, c = [], "", db.conn()
    # the index's devices one seek each (not a scan of every row's entry), each looked up over [a, b) on it
    while (d := c.execute("SELECT min(device) FROM sounds WHERE device > ?", (d,)).fetchone()[0]) is not None:
        if c.execute(f"SELECT 1 FROM sounds WHERE device = ? AND model = ? AND {sounds.SPAN} LIMIT 1", (d, sound.model_id(name), *sounds.span_args(a, b))).fetchone():
            out.append(d)
    return out


def day(date: str) -> list[dict]:
    """Each device's runs (runs()) of the day: [{device, runs}], none while off."""
    d = dt.date.fromisoformat(date)
    a, b = ms(d.isoformat()), ms((d + dt.timedelta(days=1)).isoformat())
    return [{"device": x, "runs": r} for x in devices(a, b) if (r := runs(x, a, b))]


def search(sound_: str, q: str = "", tag: str = "", who: str = "", after: str = "", before: str = "", limit: int = 30) -> tuple[list[dict], str | None]:
    """db.search's conversations that have the tag `sound_` (music, vehicle or quiet), newest first, each with its
    sounds, and where it stopped looking: having looked at SCAN of them before `limit` were found, the start of the
    last one looked at (the next call's `before` for the rest), else None. Off: db.search's, the filter ignored (as
    before it was one), and None."""
    if not (name := model()):
        return db.search(q, tag, who, after, before, limit), None
    first = db.conn().execute("SELECT min(start_ms) FROM sounds WHERE model = ?", (sound.model_id(name),)).fetchone()[0]
    if first is None:
        return [], None
    after = max(after, dt.datetime.fromtimestamp(first / 1000 - 86400).isoformat(timespec="seconds"))  # none before the first scores
    out, seen, limit = [], 0, limit if limit > 0 else 10 ** 9
    while True:
        cs = db.search(q, tag, who, after, before, n := min(50, SCAN - seen))
        for c in cs:
            if (s := tags(c)) and any(x["event"] == sound_ for x in s["events"]):
                out.append({**c, "sounds": s})
                if len(out) == limit:
                    return out, None
        seen += len(cs)
        if len(cs) < n:
            return out, None
        before = cs[-1]["start"]
        if seen >= SCAN:
            return out, before


def wrong(device: str, a: int, b: int, event: str, conv: str | None) -> int:
    """The owner's 不对 on an event of [a, b) of device, kept with the model it came from; its id."""
    with db.tx() as c:
        return c.execute("INSERT INTO sound_wrong (at, device, start_ms, end_ms, event, model, conv) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (dt.datetime.now().isoformat(timespec="seconds"), device, a, b, event, sound.model_id(model()), conv)).lastrowid


def wrongs() -> list[dict]:
    """Every 不对, oldest first: the export."""
    return [dict(r) for r in db.conn().execute("SELECT * FROM sound_wrong ORDER BY id")]


def hm(t_ms: int) -> str:
    return time.strftime("%H:%M", time.localtime(t_ms / 1000))
