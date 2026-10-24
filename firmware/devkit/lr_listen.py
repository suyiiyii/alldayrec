"""Listen to one recorder live on the Mac: follow its record stream (LIVE, src/lr_store.h), decode each Opus frame as
it comes and play it, about 0.4 s behind the mic. A line a second for the second of sound just played, on the wall
clock (the stream's 'T' record): RMS, 50 Hz hum (the 50..250 Hz harmonics together), ms in runs of exact zeros of
10 ms or more (no data from the mic), full-scale samples, ms lost on the link, the delay from the mic to the speakers
(and how much of it is the queue here).
Connects only to the recorder named exactly --name, and only once it advertises connectable: one a phone holds is
reported, not fought for. Read-only: it writes TIME, as every phone connection does (and pairs, the first time).
Ends on Ctrl-C or after --seconds; a link that goes is found again.
    uv run --no-project --with bleak --with sounddevice python lr_listen.py --name LR-P3-XXXX [--seconds N]
    uv run --no-project --with bleak python lr_listen.py --selftest      (no recorder, no speakers)
"""
import array
import asyncio
import ctypes
import math
import re
import struct
import sys
import threading
import time

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from lr_sync import FRAME_MS, LIVE, TIME, decoder, records

RATE = 16000
ZERO = RATE * 10 // 1000  # a run of exact zeros this long (10 ms) is the mic sending nothing
# s queued for the speakers: playing (re)starts at PRIME; past MAX the oldest goes (a burst after a stall; the MacBook's
# output ran 0.3% slower than the recorder, 10-07)
PRIME, MAX = 0.15, 0.25


def stats(pcm, z=0):
    """Samples (a second) -> (RMS dBFS, hum dBFS, ms in zero runs >= 10 ms, full-scale samples, the zero run going on
    at the end: the next second's z)."""
    db = lambda ms: 10 * math.log10(max(ms, 1e-3) / 32768 ** 2)  # mean square -> dBFS
    n, hum, zero = max(len(pcm), 1), 0.0, 0
    for f in range(50, 251, 50):  # Goertzel: each harmonic's mean square
        w, s1, s2 = 2 * math.cos(2 * math.pi * f / RATE), 0.0, 0.0
        for x in pcm:
            s1, s2 = x + w * s1 - s2, s1
        hum += 2 * (s1 * s1 + s2 * s2 - w * s1 * s2) / n / n
    for x in pcm:
        z = 0 if x else z + 1
        zero += ZERO if z == ZERO else z > ZERO
    return db(sum(x * x for x in pcm) / n), db(hum), zero * 1000 // RATE, sum(abs(x) >= 32767 for x in pcm), z


class Listener:
    """LIVE notifications (file:u32 offset:u32 bytes) -> PCM to play(pcm), which says in how many s it is heard, and a
    stats line a second to say(line). A record comes in MTU pieces, and LIVE never resumes mid-record: a piece that does
    not go on from the last (records left out of LIVE, a new file) starts one; the rest of an unfinished one is lost."""

    def __init__(self, play, say):
        self.play, self.say, self.dec = play, say, decoder()
        self.at, self.buf, self.lost_sync = None, b"", False  # where the last piece ended; an unfinished record
        self.sync = self.next = self.sec = None  # the latest 'T' (frame, epoch_ms); the frame expected next; the second
        self.pcm, self.delay, self.queue, self.lost, self.z = array.array("h"), [], [], 0, 0

    def __call__(self, d: bytes, now: float | None = None):
        now = time.time() if now is None else now
        f, off = struct.unpack_from("<II", d)
        if self.at != (f, off):
            self.buf, self.lost_sync = b"", False
        self.at = f, off + len(d) - 8
        if self.lost_sync:
            return
        self.buf += d[8:]
        used = 0
        for r in records(self.buf):
            if r[0] == "T":
                used += 13
                self.sync = r[1:]
                continue
            used += 6 + sum(1 + len(x) for x in r[2])
            for i, opus in enumerate(r[2]):
                self.frame(r[1] + i, opus, now)
        self.buf = self.buf[used:]
        if self.buf[:1] not in (b"", b"A", b"T"):  # began mid-record (pieces sent before we listened): wait for a start
            self.buf, self.lost_sync = b"", True

    def frame(self, n: int, opus: bytes, now: float):
        pcm = self.dec(opus)
        ahead = self.play(pcm)
        lost = max(n - self.next, 0) * FRAME_MS if self.next is not None else 0
        self.next = n + 1
        if not self.sync:  # not on the wall clock yet: heard, not counted
            return
        t = self.sync[1] + (n - self.sync[0]) * FRAME_MS  # when the mic took it, epoch ms
        if t // 1000 != self.sec:
            self.flush()
            self.sec = t // 1000
        self.pcm.frombytes(pcm)
        self.lost += lost
        self.delay.append(now * 1000 - t + ahead * 1000)
        self.queue.append(ahead * 1000)

    def flush(self):
        if self.sec is None:
            return
        rms, hum, zero, full, self.z = stats(self.pcm, self.z)
        self.say(f"{time.strftime('%H:%M:%S', time.localtime(self.sec))}  rms {rms:6.1f} dBFS  hum {hum:6.1f}  "
                 f"zero {zero:4d} ms  full {full:4d}  lost {self.lost:4d} ms  delay {sum(self.delay) / len(self.delay):4.0f} ms "
                 f"(queued {sum(self.queue) / len(self.queue):3.0f})")
        self.pcm, self.delay, self.queue, self.lost = array.array("h"), [], [], 0


def speakers():
    """The default output, fed by a queue -> (the stream, play(pcm) -> s until pcm is heard)."""
    import sounddevice
    q, lock, on = bytearray(), threading.Lock(), [False]

    def take(out, frames, t, status):
        with lock:
            on[0] = on[0] or len(q) >= PRIME * RATE * 2
            k = min(len(out), len(q)) if on[0] else 0
            out[:] = bytes(q[:k]) + bytes(len(out) - k)
            del q[:k]
            on[0] = on[0] and k == len(out)  # ran dry (the link held back): wait for PRIME again

    s = sounddevice.RawOutputStream(RATE, channels=1, dtype="int16", latency="low", callback=take)

    def play(pcm: bytes) -> float:
        with lock:
            q.extend(pcm)
            if len(q) > MAX * RATE * 2:
                del q[:len(q) - int(MAX * RATE * 2)]
            return (len(q) - len(pcm)) / RATE / 2 + s.latency
    return s, play


async def main(name: str, seconds: float):
    busy = []

    def match(d, a):
        if a.local_name != name:
            return False
        if not a.platform_data[1].get("kCBAdvDataIsConnectable", True):  # macOS; a phone is on it
            busy.append(d)
            return False
        return True

    out, play = speakers()
    listen, end = Listener(play, print), time.time() + (seconds or 1e9)
    with out:
        while (left := end - time.time()) > 0:  # a link that goes (the recorder reset, out of range): find it again
            busy.clear()
            dev = await BleakScanner.find_device_by_filter(match, min(left, 20))
            if not dev:
                print(f"{name}: {'advertising, not connectable: a phone is connected' if busy else 'not found'}")
                continue
            gone = asyncio.Event()
            listen.sync = listen.next = None  # a new 'T' comes; the recorder may have rebooted
            try:
                async with BleakClient(dev, disconnected_callback=lambda _: gone.set()) as c:
                    # a bonded Mac is subscribed again as the link is encrypted, before we listen: off and on again,
                    # so the recorder starts a new file we take from its start
                    for on in (True, False, True):
                        await (c.start_notify(LIVE, lambda _, d: listen(bytes(d))) if on else c.stop_notify(LIVE))
                    await c.write_gatt_char(TIME, struct.pack("<Q", int(time.time() * 1000)), response=True)  # the stream on the wall clock
                    st = await c.read_gatt_char(TIME)
                    boot, store = struct.unpack_from("<Hxxxxh", st)
                    print(f"{time.strftime('%H:%M:%S')} {name}: boot {boot}, store {store}, {struct.unpack_from('<I', st, 28)[0]} "
                          f"frames dropped this boot, {st[42]} bonds; output latency {out.latency * 1000:.0f} ms")
                    await asyncio.wait_for(gone.wait(), end - time.time())
                    print(f"{time.strftime('%H:%M:%S')} the link went")
            except TimeoutError:
                pass
            except BleakError as e:
                print(f"{time.strftime('%H:%M:%S')} {name}: {e}")
    listen.flush()


def selftest():
    # 3 s as the recorder makes it (codec.c: CELT low delay, 32 kb/s VBR): hum (50 Hz at -12 dBFS + 150 Hz at
    # -24 dBFS), then from 1.2 s 50 ms of nothing from the mic, then from 2.3 s 20 ms clipped
    lib = ctypes.CDLL("/opt/homebrew/lib/libopus.dylib")
    lib.opus_encoder_create.restype = ctypes.c_void_p
    enc = ctypes.c_void_p(lib.opus_encoder_create(RATE, 1, 2051, ctypes.byref(ctypes.c_int())))
    hum = lambda i: 0.25 * 32767 * math.sin(2 * math.pi * 50 * i / RATE) + 0.0625 * 32767 * math.sin(2 * math.pi * 150 * i / RATE)
    pcm = [0 if 19200 <= i < 20000 else 32767 if 36800 <= i < 37120 and i % 64 < 32 else -32767 if 36800 <= i < 37120
           else round(hum(i)) for i in range(3 * RATE)]
    frames, out = [], (ctypes.c_ubyte * 256)()
    for i in range(0, len(pcm), 160):
        if i == 19200:  # the recorder's fixed-point encoder sends silence frames for nothing; this float one, after a reset
            lib.opus_encoder_ctl(enc, 4028)
        n = lib.opus_encode(enc, (ctypes.c_int16 * 160)(*pcm[i:i + 160]), 160, out, 40)  # 40 B: 32 kb/s (a variadic ctl misses on arm64)
        frames.append(bytes(out[:n]))
    # into the stream, frames 1000.., as lr_store.c writes it: 'A' records of 10 frames, one 'T' (frame 1000 was
    # 10:00:00.000), sent in 20 B pieces; the pieces of frames 1240..1249 after the first go (a notify that failed:
    # the rest of that record is not sent), then a new file
    a = lambda first, fs: bytes([0x41, len(fs)]) + struct.pack("<I", first) + b"".join(bytes([len(f)]) + f for f in fs)
    epoch = int(time.mktime((2026, 10, 7, 10, 0, 0, 0, 0, -1))) * 1000
    recs = [b"T" + struct.pack("<IQ", 1000, epoch)] + [a(1000 + i, frames[i:i + 10]) for i in range(0, 300, 10)]
    sent, f, off = [], 7, 0
    for r in recs:
        first = struct.unpack_from("<I", r, 1 + (r[0] == 0x41))[0]
        if first == 1200 and r[0] == 0x41:
            f, off = 8, 0
        ps = [struct.pack("<II", f, off + i) + r[i:i + 20] for i in range(0, len(r), 20)]
        # as a record arrives: 20 ms after its last frame
        sent += [(p, epoch / 1000 + (first - 1000 + 10) * 0.01 + 0.02) for p in ps[:1 if first == 1240 else None]]
        off += len(r)
    played, lines = [], []
    listen = Listener(lambda p: played.append(p) or 0.1, lines.append)  # 0.1 s queued ahead of each frame
    for p, now in sent:
        listen(p, now)
    listen.flush()
    print("\n".join(lines))
    got = array.array("h", b"".join(played))
    assert len(got) == 290 * 160, len(got)  # all but the 10 lost frames
    lag = min(range(200), key=lambda k: sum((got[i + k] - pcm[i]) ** 2 for i in range(4000, 4800)))
    assert lag < 160, lag  # the codec's own delay, not a frame late (each frame out of its own decode)
    s = [{k: float(v) for k, v in re.findall(r"(rms|hum|zero|full|lost|delay|queued) +(-?[\d.]+)", l)} for l in lines]
    assert [l[:8] for l in lines] == ["10:00:00", "10:00:01", "10:00:02"], lines
    assert -15.5 < s[0]["hum"] < -14 and abs(s[0]["rms"] - s[0]["hum"]) < 0.5, s[0]  # 50+150 Hz: -14.8 dBFS RMS
    assert s[0]["zero"] == 0 and 30 <= s[1]["zero"] <= 50 and s[2]["zero"] == 0, s  # 50 ms of nothing, codec edges
    assert s[0]["full"] == s[1]["full"] == 0 < s[2]["full"], s
    assert [x["lost"] for x in s] == [0, 0, 100], s
    assert [(x["delay"], x["queued"]) for x in s] == [(175, 100)] * 3, s  # 130..220 ms over a record's frames
    # joined mid-record (a bonded Mac, subscribed again before it listened): nothing kept until a record starts
    listen = Listener(lambda p: 0, lambda line: None)
    listen(struct.pack("<II", 6, 100) + recs[3][7:27])
    listen(struct.pack("<II", 6, 120) + recs[3][27:47])
    assert listen.buf == b"" and listen.lost_sync and listen.next is None
    for p, now in sent:  # a new file
        listen(p, now)
    assert not listen.lost_sync and listen.next == 1300, listen.next
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    arg = lambda k, default=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else default
    if not arg("--name"):
        sys.exit(__doc__)
    try:
        asyncio.run(main(arg("--name"), float(arg("--seconds", 0))))
    except KeyboardInterrupt:
        pass
