"""Power tests of a recorder built with LR_DEBUG=1 (CONFIG_LR_POWER_DEBUG, CTRL 0x30 in src/lr_store.c): put it in one
state after another, each held a while, and write when each began and ended, for a meter's log on the battery line
to be averaged per state. States without a link drop it (hold): the recorder takes no connection meanwhile, then
advertises for this script again. Phone states follow LIVE and ACK it as the app does (phone mode: the card off).
    uv run --no-project --with bleak python lr_power.py cmd --name LR-P3-XXXX [SUB A B C D ...]   one command each, or
                                                                  status; prints the replies
    uv run --no-project --with bleak python lr_power.py run --name LR-P3-XXXX --out states.csv [--secs 60] [--only a,b]
    python3 lr_power.py stats states.csv meter.csv [--skip 5]    per state: mean, P1, P99 of the meter's
                                                                  time,value(A) rows, the first --skip s left out
    python3 lr_power.py --selftest
"""
import argparse
import asyncio
import csv
import statistics
import struct
import sys
import time
from datetime import datetime, timedelta

U = lambda n: f"7c3e{n:04x}-1a2b-4c5d-8e9f-6c6976657265"
TIME, CTRL, LIVE = U(1), U(2), U(4)
MIC, CODEC, CARD, LED, ADV, PARAM, HOLD, WRITES = range(1, 9)
CLOCKS, CLOSE, CARD_OFF = 1, 2, 4  # after each card write (WRITES b)
NO_ADV = 0xFFFF
SLOW = (24, 36, 0, 400)  # the released firmware's slow link (prj conf BT_PERIPHERAL_PREF_*): 30-45 ms
# name, what is on (mic codec card led), adv_ms (0 as released), link: None = held (no link) / (min max lat timeout)
# x 1.25 ms / 10 ms, live: follow LIVE and ACK (phone mode); what it says; card writes (every s, after), as released
# when left out
STATES = [
    ("floor", (0, 0, 0, 0), NO_ADV, None, False, "everything off, no advertising: the chip, regulators, leaks"),
    ("adv", (0, 0, 0, 0), 0, None, False, "everything off, advertising as released with no phone (100-150 ms)"),
    ("mic", (1, 0, 0, 0), 0, None, False, "+ mic (PDM, its supply, HFXO)"),
    ("mic_codec", (1, 1, 0, 0), 0, None, False, "+ Opus (frames go nowhere: no card, no link)"),
    ("card_idle", (0, 0, 1, 0), 0, None, False, "the card powered and mounted, nothing written (mic and Opus off)"),
    ("card", (1, 1, 1, 0), 0, None, False, "card mode: mic, Opus, card writes, no phone"),
    ("card_led", (1, 1, 1, 1), 0, None, False, "card mode as released: + LED flashes"),
    ("link_idle", (0, 0, 0, 0), 0, (24, 24, 0, 400), False, "connected at 30 ms, everything else off"),
    ("card_link", (1, 1, 1, 1), 0, SLOW, False, "card mode + a phone connected (slow link, no LIVE)"),
    ("card_live", (1, 1, 1, 1), 0, SLOW, "noack", "card mode + LIVE to a phone that does not ack (older apps)"),
    ("phone_30", (1, 1, 1, 1), 0, (24, 24, 0, 400), True, "phone mode at 30 ms (iPhone): LIVE + ACK, card off"),
    ("phone_15", (1, 1, 1, 1), 0, (12, 12, 0, 400), True, "phone mode at 15 ms (the fastest a Mac grants)"),
    ("phone_45", (1, 1, 1, 1), 0, (36, 36, 0, 400), True, "phone mode at 45 ms (Android's slow end)"),
    ("phone_7_45", (1, 1, 1, 1), 0, (6, 36, 0, 400), True, "phone mode, 7.5-45 ms asked (Android's range): the Mac picks"),
    # what the card costs after a write: written less often, and what each write ends with (debug_drain)
    ("w1", (1, 1, 1, 0), 0, None, False, "card mode, written each batch (~1 s) as before #221", (1, 0)),
    ("w5", (1, 1, 1, 0), 0, None, False, "card mode, written every 5 s", (5, 0)),
    ("w15", (1, 1, 1, 0), 0, None, False, "card mode, written every 15 s (as the ring holds: ~11 s)", (15, 0)),
    ("w1_clk", (1, 1, 1, 0), 0, None, False, "each batch, then CS high + 16 clocks", (1, CLOCKS)),
    ("w15_clk", (1, 1, 1, 0), 0, None, False, "every 15 s, then CS high + 16 clocks", (15, CLOCKS)),
    ("w1_close", (1, 1, 1, 0), 0, None, False, "each batch, then the file closed", (1, CLOSE)),
    ("w15_close", (1, 1, 1, 0), 0, None, False, "every 15 s, then the file closed", (15, CLOSE)),
    ("card_fixed", (1, 1, 1, 0), 0, None, False, "card mode as released since #221: written at 3/4 ring or 10 s", (0, 0)),
    ("w15_off", (1, 1, 1, 0), 0, None, False, "every 15 s, then the card unmounted and off till the next", (15, CARD_OFF)),
]
FIELDS = ["state", "start", "end", "link", "asked", "got_interval_ms", "latency", "timeout_ms", "on", "note"]


def parse_status(d: bytes):
    """0xB0 sub err on adv_ms interval latency timeout [write_s after] -> dict"""
    _, sub, err, on, adv, iv, lat, to = struct.unpack("<BBbBHHHH", d[:12])
    ws, after = struct.unpack("<HB", d[12:15]) if len(d) >= 15 else (0, 0)
    return dict(sub=sub, err=err, mic=on & 1, codec=on >> 1 & 1, card=on >> 2 & 1, led=on >> 3 & 1,
                phone=on >> 4 & 1, card_unpowered=on >> 5 & 1, adv_ms=adv, interval_ms=iv * 1.25, latency=lat,
                timeout_ms=to * 10, write_s=ws, after=after)


def cmd_bytes(sub, a=0, b=0, c=0, d=0):
    return struct.pack("<BBHHHH", 0x30, sub, a, b, c, d)


class Rec:
    """One link to the recorder: commands, their replies, LIVE followed and acked like the app."""

    def __init__(self, name):
        self.name, self.c, self.replies, self.live_at, self.live_n = name, None, None, None, 0
        self.lock = asyncio.Lock()  # one CTRL write at a time: bleak on macOS lost one of two at once (a hang)

    async def connect(self, timeout=90):
        from bleak import BleakClient, BleakScanner
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            dev = await BleakScanner.find_device_by_name(self.name, timeout=10)
            if dev is None:
                continue
            try:
                self.c = BleakClient(dev)
                await self.c.connect(timeout=15)
                self.replies = asyncio.Queue()
                await self.c.start_notify(CTRL, lambda _, d: self.replies.put_nowait(bytes(d)))
                await self.c.write_gatt_char(TIME, struct.pack("<Q", int(time.time() * 1000)), response=True)
                return
            except Exception as e:  # noqa: BLE001 -- a link that failed coming up: try again
                print(f"connect: {type(e).__name__}: {e}", flush=True)
                await asyncio.sleep(2)
        raise RuntimeError(f"{self.name} not connectable for {timeout} s (a phone holding it?)")

    async def send(self, sub, *args):
        async with self.lock:
            while not self.replies.empty():
                self.replies.get_nowait()
            await self.c.write_gatt_char(CTRL, cmd_bytes(sub, *args), response=True)
            while True:  # other CTRL replies (none expected) are passed over
                d = await asyncio.wait_for(self.replies.get(), 15)
                if d[:1] == b"\xb0":
                    return parse_status(d)

    async def follow(self, ack):
        def on_live(_, d):
            f, off = struct.unpack_from("<II", d)
            self.live_at, self.live_n = (f, off + len(d) - 8), self.live_n + 1
        await self.c.start_notify(LIVE, on_live)
        self.acking = ack

    async def ack_loop(self):
        while True:
            await asyncio.sleep(5)
            if self.live_at and getattr(self, "acking", False) is True:
                async with self.lock:
                    await self.c.write_gatt_char(CTRL, struct.pack("<BII", 8, *self.live_at), response=True)

    async def close(self):
        if self.c and self.c.is_connected:
            await self.c.disconnect()


async def set_parts(r, on, adv, writes=(0, 0)):
    for sub, v in zip((MIC, CODEC, CARD, LED), on):
        st = await r.send(sub, v)
        if st["err"]:
            print(f"  sub {sub}={v}: err {st['err']}", flush=True)
    await r.send(WRITES, *writes)
    return await r.send(ADV, adv)


async def run(name, out, secs, only):
    states = [s for s in STATES if not only or s[0] in only]
    r = Rec(name)
    await r.connect()
    print(f"connected {name}: {await r.send(0)}", flush=True)
    fh = open(out, "a", newline="")
    w = csv.DictWriter(fh, FIELDS)
    if fh.tell() == 0:
        w.writeheader()
    for sname, on, adv, link, live, note, *writes in states:
        if not r.c.is_connected:
            await r.connect()
        st = await set_parts(r, on, adv, *writes)
        row = dict(state=sname, on="".join("-MCKL"[i + 1] if v else "." for i, v in enumerate(on)), note=note)
        if link is None:
            st = await r.send(HOLD, secs)
            start = datetime.now()
            row.update(link="none (held, scannable only)", asked="", got_interval_ms="", latency="", timeout_ms="")
            print(f"{sname}: held {secs} s ({note})", flush=True)
            await asyncio.sleep(secs)
            end = datetime.now()
            await asyncio.sleep(1)
            await r.connect()
        else:
            if live:
                await r.follow(live)
                acker = asyncio.create_task(r.ack_loop())
            st = await r.send(PARAM, *link)
            if st["err"] == -120:  # -EALREADY: the last request still open
                await asyncio.sleep(5)
                st = await r.send(PARAM, *link)
            await asyncio.sleep(6)  # the update lands; phone mode: ACKs, then the card off after 10 s idle
            if live is True:
                await asyncio.sleep(19)
            st = await r.send(0)
            start = datetime.now()
            print(f"{sname}: {st}", flush=True)
            await asyncio.sleep(secs)
            end = datetime.now()
            st2 = await r.send(0)
            if live:
                acker.cancel()
                await r.c.stop_notify(LIVE)
                r.acking = False
            asked = f"{link[0] * 1.25:g}-{link[1] * 1.25:g} ms lat {link[2]} to {link[3] * 10} ms"
            row.update(link=f"Mac central{', LIVE+ACK' if live is True else ', LIVE no ACK' if live else ''}",
                       asked=asked, got_interval_ms=st["interval_ms"], latency=st["latency"],
                       timeout_ms=st["timeout_ms"])
            if st2["interval_ms"] != st["interval_ms"] or (live is True and not (st["phone"] and st2["phone"])):
                row["note"] += f"; at the end {st2}"
            if live is True and not (st["phone"] and st["card_unpowered"]):
                row["note"] += f"; NOT in phone mode with the card off: {st}"
        row.update(start=start.isoformat(timespec="milliseconds"), end=end.isoformat(timespec="milliseconds"))
        w.writerow(row)
        fh.flush()
    await set_parts(r, (1, 1, 1, 1), 0)  # as released again
    await r.close()


def pct(v, p):
    return v[min(len(v) - 1, round(p / 100 * (len(v) - 1)))]


def stats(states, meter, skip):
    rows = []
    for m in csv.DictReader(open(meter)):
        try:
            rows.append((datetime.fromisoformat(m["time"]), float(m["value"]) if m["value"] else None))
        except ValueError:
            continue
    out = []
    for s in csv.DictReader(open(states)):
        a, b = datetime.fromisoformat(s["start"]) + timedelta(seconds=skip), datetime.fromisoformat(s["end"])
        win = [x for t, x in rows if a <= t <= b]
        v = sorted(x * 1e3 for x in win if x is not None)
        ol = sum(x is None for x in win)
        r = dict(state=s["state"], link=s["link"], asked=s["asked"], got=s["got_interval_ms"], n=len(v), ol=ol)
        if v:
            r.update(mean=statistics.fmean(v), p1=pct(v, 1), p99=pct(v, 99))
        out.append(r)
    return out


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["cmd", "run", "stats"])
    p.add_argument("args", nargs="*")
    p.add_argument("--name")
    p.add_argument("--out")
    p.add_argument("--secs", type=int, default=60)
    p.add_argument("--only", default="")
    p.add_argument("--skip", type=float, default=5)
    a = p.parse_args()
    if a.what == "stats":
        print("state | link | asked | got ms | mean mA | P1 | P99 | n | OL")
        for r in stats(a.args[0], a.args[1], a.skip):
            m = f"{r['mean']:.3f} | {r['p1']:.3f} | {r['p99']:.3f}" if r["n"] else "- | - | -"
            print(f"{r['state']} | {r['link']} | {r['asked']} | {r['got']} | {m} | {r['n']} | {r['ol']}")
    elif a.what == "run":
        asyncio.run(run(a.name, a.out, a.secs, set(filter(None, a.only.split(",")))))
    else:
        async def cmds():
            r = Rec(a.name)
            await r.connect()
            nums = [int(x, 0) for x in a.args] or [0]
            print(await r.send(nums[0], *nums[1:5]))
            await r.close()
        asyncio.run(cmds())


def selftest():
    d = bytes([0xB0, 6, 0xF4, 0b110101]) + struct.pack("<HHHH", NO_ADV, 24, 0, 400)
    assert parse_status(d + struct.pack("<HB", 15, CLOCKS))["write_s"] == 15 and parse_status(d)["after"] == 0
    st = parse_status(d)
    assert (st["sub"], st["err"], st["mic"], st["codec"], st["card"], st["led"], st["phone"]) == (6, -12, 1, 0, 1, 0, 1)
    assert (st["interval_ms"], st["timeout_ms"], st["adv_ms"], st["card_unpowered"]) == (30, 4000, NO_ADV, 1)
    assert cmd_bytes(HOLD, 60) == b"\x30\x07\x3c\x00" + bytes(6)
    import os
    import tempfile
    tmp = tempfile.mkdtemp()
    sp, mp = os.path.join(tmp, "s.csv"), os.path.join(tmp, "m.csv")
    t0 = datetime(2026, 1, 1, 12)
    with open(sp, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerow(dict(state="x", start=t0.isoformat(), end=(t0 + timedelta(seconds=20)).isoformat(), link="none",
                        asked="", got_interval_ms=""))
    with open(mp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "value", "mode", "range", "display", "unit"])
        for i in range(250):  # 10 Hz from t0 - 2 s: 9 mA in the skipped 5 s, then 5 mA, one OL
            t = t0 + timedelta(seconds=i / 10 - 2)
            w.writerow([t.isoformat(), "" if i == 150 else (0.009 if i < 70 else 0.005), "ADC", "50mA", "", "mA"])
    r = stats(sp, mp, 5)[0]
    assert r["ol"] == 1 and r["n"] == 150 and abs(r["mean"] - 5) < 1e-9, r
    print("selftest ok")


if __name__ == "__main__":
    main()
