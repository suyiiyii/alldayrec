"""Level-cal E1: is the nRF PDM's GAIN applied before or after its 16-bit rounding? Reads the mic's raw PCM that an
LR_RAW_PCM=1 build (src/raw_pcm.c) sends on the USB serial port, and tells from the sample values. Gain applied after
the rounding: at +20 dB (0x50, x10) the samples sit on a grid ~10 apart, no two used values next to each other;
before: the values are dense, as at 0 dB. Holes that are not on the gain's grid (a narrowband sound), or a level that
did not change by the gains' difference, are no answer. Standard library only.
    python3 lr_pcm.py capture OUT.raw [--seconds 12] [--port /dev/cu.usbmodemXXXX]   the frames as they come
    python3 lr_pcm.py stats A.raw [B.raw]   each capture's numbers; two at different gains: the verdict
    python3 lr_pcm.py --selftest
A frame: "LRPC", seq u32, gain u8, 0, byte sum of the samples u16, 160 samples s16 (little-endian).
"""
import glob
import math
import os
import random
import struct
import sys
import termios
import time
import tty
from collections import Counter

MAGIC = b"LRPC"
HEAD = struct.Struct("<4sIBBH")
N = 160  # samples a frame: 10 ms at 16 kHz
FRAME = HEAD.size + 2 * N
SKIP = 200  # frames (2 s) dropped after the port opens and after a gain change: what was buffered, and up to 1 s of
#             mic ring the codec thread may still hold under the old gain (the gain byte is read as it takes a frame)
FULL = 32767


def frames(b: bytes):
    """[(seq, gain, samples)] and the count of frames with a bad sum; garbage between frames is skipped."""
    out, bad, i = [], 0, b.find(MAGIC)
    while i >= 0 and i + FRAME <= len(b):
        _, seq, gain, _, s = HEAD.unpack_from(b, i)
        pcm = b[i + HEAD.size:i + FRAME]
        if sum(pcm) & 0xFFFF == s:
            out.append((seq, gain, struct.unpack(f"<{N}h", pcm)))
            i += FRAME
        else:  # a cut frame, or "LRPC" inside samples: look again just past it
            bad += 1
            i += 1
        i = b.find(MAGIC, i)
    return out, bad


def pick(fr):
    """The frames of one gain to judge: after the last gain change and SKIP more, from the port's opening too."""
    if not fr:
        return None, []
    gain, start = fr[-1][1], 0
    for i, f in enumerate(fr):
        if f[1] != gain:
            start = i + 1
    return gain, fr[start + SKIP:]


def gain_db(code: int) -> float:
    return (code - 0x28) / 2


def stats(fr, bad=0) -> dict:
    gain, use = pick(fr)
    seqs = [f[0] for f in fr]
    gaps = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]) if b > a)
    x = [v for f in use for v in f[2]]
    clipped = sum(1 for v in x if v >= FULL or v <= -FULL)
    x = [v for v in x if -FULL < v < FULL]
    r = {"gain": gain, "frames": len(fr), "used": len(use), "bad": bad, "gaps": gaps, "clipped": clipped,
         "clipped_share": clipped / max(len(use) * N, 1)}
    if len(x) < 100 * N:
        return {**r, "error": f"{len(x)} samples left after skipping {SKIP} frames: capture longer"}
    mean = sum(x) / len(x)
    r["mean"] = round(mean, 2)
    r["rms"] = math.sqrt(sum((v - mean) ** 2 for v in x) / len(x))
    if r["rms"] == 0:
        return {**r, "error": f"every sample is {x[0]}: a dead mic or no PCM, check the board and capture again"}
    # GCD of each frame's steps between samples: 1 when values are continuous, ~10 for x10 after rounding
    g = Counter()
    for f in use:
        if any(v >= FULL or v <= -FULL for v in f[2]):
            continue  # clipped frames left out, as from everything else
        d = 0
        for a, b in zip(f[2], f[2][1:]):
            d = math.gcd(d, b - a)
        g[d] += 1
    r["frame_gcd"] = dict(g.most_common(5))
    d = 0
    for v in x:
        d = math.gcd(d, v - x[0])
    r["gcd"] = d
    r["low_bits"] = [round(sum(v >> k & 1 for v in x) / len(x), 3) for k in range(4)]  # ~0.5 each when all used
    # the distinct values used, and the steps between neighbours among them: gain g >= 2 after the rounding never uses
    # two adjacent values (round(k*g) and round((k+1)*g) are >= g-1 apart), and its median step is ~g
    u = sorted(set(x))
    d = sorted(b - a for a, b in zip(u, u[1:]))
    r["adjacent"] = d.count(1) / len(d)
    r["spacing"] = d[len(d) // 2]
    # occupancy: of the steps between adjacent samples (integers on the same grid as the samples, without the DC and
    # the slow 2-20 Hz wander the raw path carries), the values used / all values between their 2nd and 98th
    # percentile, where each value is expected often
    s = sorted(b - a for f in use for a, b in zip(f[2], f[2][1:]) if -FULL < a < FULL and -FULL < b < FULL)
    lo, hi = s[len(s) // 50], s[-len(s) // 50]
    band = [v for v in s if lo <= v <= hi]
    r["band"] = (lo, hi)
    r["width"] = hi - lo + 1
    r["occupancy"] = len(set(band)) / (hi - lo + 1)
    r["per_value"] = len(band) / (hi - lo + 1)
    return r


def verdict(a: dict, b: dict) -> tuple[str, str]:
    """('after' | 'before' | 'inconclusive', why) for two captures at different gains."""
    for r in (a, b):
        if "error" in r:
            return "inconclusive", r["error"]
    a, b = sorted([a, b], key=lambda r: r["gain"])
    step = 10 ** ((gain_db(b["gain"]) - gain_db(a["gain"])) / 20)
    if step < 2:
        return "inconclusive", f"gains {a['gain']:#04x} and {b['gain']:#04x} only x{step:.2f} apart: use 0x28 and 0x50"
    if a["occupancy"] < 0.9:
        return "inconclusive", f"{a['gain']:#04x} already leaves values unused (occupancy {a['occupancy']:.2f})"
    if b["clipped_share"] > 0.01:
        return "inconclusive", f"{b['clipped_share']:.1%} of {b['gain']:#04x}'s samples clipped: a quieter room, or 0x3c (S gain 10) as the higher gain"
    if b["width"] < 3 * step:
        return "inconclusive", f"too little spread at {b['gain']:#04x} (steps {b['band']}): the room too quiet, play a soft steady sound"
    if a["per_value"] < 10 or b["per_value"] < 10:
        return "inconclusive", f"too few samples a value ({min(a['per_value'], b['per_value']):.1f}): capture longer"
    want, got = gain_db(b["gain"]) - gain_db(a["gain"]), 20 * math.log10(b["rms"] / a["rms"])
    if abs(got - want) > 3:
        return "inconclusive", f"rms {got:+.1f} dB apart, the gains say {want:+.1f}: the gain did not take, or the sound changed"
    seen = f"occupancy {b['occupancy']:.2f}, adjacent {b['adjacent']:.2f}, spacing {b['spacing']}"
    grid = b["adjacent"] < 0.02 and 0.7 * step <= b["spacing"] <= 1.5 * step
    if grid and b["occupancy"] < 0.9:
        return "after", f"{b['gain']:#04x}'s values {b['spacing']} apart, none adjacent (expected {step:.1f} after rounding; {seen})"
    if not grid and b["occupancy"] >= 0.9 and b["adjacent"] > 0.5:
        return "before", f"{b['gain']:#04x} uses {b['occupancy']:.0%} of values, as {a['gain']:#04x} ({a['occupancy']:.0%}; {seen})"
    return "inconclusive", f"{b['gain']:#04x} {seen}: neither the x{step:.1f} grid nor dense values"


def report(path: str) -> dict:
    fr, bad = frames(open(path, "rb").read())
    r = stats(fr, bad)
    print(f"{path}: gain {r['gain']:#04x} ({gain_db(r['gain']):+.1f} dB)" if r["gain"] is not None else f"{path}: no frames")
    for k, v in r.items():
        if k != "gain":
            print(f"  {k}: {round(v, 3) if isinstance(v, float) else v}")
    return r


def capture(out: str, seconds: float, port: str | None):
    ports = [port] if port else glob.glob("/dev/cu.usbmodem*")
    if len(ports) != 1:
        raise SystemExit(f"one board on USB, or --port: found {ports}")
    fd = os.open(ports[0], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)  # no CR/LF or XON/XOFF rewriting: that would fake or hide a grid
    a = termios.tcgetattr(fd)
    a[4] = a[5] = termios.B115200  # never 1200: that reboots the board into its bootloader (usb.c)
    termios.tcsetattr(fd, termios.TCSANOW, a)
    buf, end = bytearray(), time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            buf += os.read(fd, 65536)
        except BlockingIOError:
            time.sleep(0.01)
    os.close(fd)
    open(out, "wb").write(buf)
    fr, bad = frames(bytes(buf))
    gains = sorted({f[1] for f in fr})
    seqs = [f[0] for f in fr]
    gaps = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]) if b > a)
    print(f"{out}: {len(buf)} B, {len(fr)} frames ({len(fr) / 100:.1f} s), {bad} bad, {gaps} frames lost, gains {[hex(g) for g in gains]}")


def selftest():
    rnd = random.Random(1)

    def frame(seq, gain, pcm):
        b = struct.pack(f"<{N}h", *pcm)
        return HEAD.pack(MAGIC, seq, gain, 0, sum(b) & 0xFFFF) + b

    def stream(gain, sigma, scale=1.0, dc=0.0, n=600, before=False):
        out = []
        for i in range(n):
            if before:  # the gain on the fine value, then rounded
                pcm = [round(rnd.gauss(dc, sigma) * scale) for _ in range(N)]
            else:  # rounded to 16 bits, then the gain
                pcm = [round(round(rnd.gauss(dc, sigma)) * scale) for _ in range(N)]
            out.append(frame(i, gain, [max(-32768, min(32767, v)) for v in pcm]))
        return b"".join(out)

    def judge(*streams):
        rs = [stats(*frames(s)) for s in streams]
        return verdict(*rs)[0], rs

    for sigma in (1.5, 4, 30):
        assert judge(stream(0x28, sigma, dc=3.3), stream(0x50, sigma, 10, 3.3))[0] == "after", sigma
        assert judge(stream(0x28, sigma, dc=3.3), stream(0x50, sigma, 10, 3.3, before=True))[0] == "before", sigma
    for before in (False, True):  # a slow 2 Hz wander of 1000 LSB (x10: 10000) under the noise: the steps ignore it
        def wander(gain, scale):
            out = []
            for i in range(600):
                pcm = []
                for j in range(N):
                    v = 1000 * math.sin(2 * math.pi * 2 * (i * N + j) / 16000) + rnd.gauss(0, 3)
                    pcm.append(round(v * scale) if before else round(round(v) * scale))
                out.append(frame(i, gain, pcm))
            return b"".join(out)
        assert judge(wander(0x28, 1), wander(0x50, 10))[0] == ("before" if before else "after"), before
    v, rs = judge(stream(0x28, 3), stream(0x50, 20000, 1))  # a loud room at 0x50: ~10% of it at full scale
    assert v == "inconclusive" and rs[1]["clipped"] > 0, rs[1]
    assert judge(stream(0x28, 2), stream(0x50, 2, 10.07))[0] == "after"  # a gain not exactly x10
    assert judge(stream(0x28, 2), stream(0x3c, 2, 3.16))[0] == "after"  # +10 dB
    assert judge(stream(0x28, 0.3), stream(0x50, 0.3, 10))[0] == "inconclusive"  # a dead quiet mic
    assert judge(stream(0x28, 3), stream(0x2a, 3, 1.12))[0] == "inconclusive"  # gains too close
    v, rs = judge(stream(0x28, 3), stream(0x50, 3, 10))
    assert rs[1]["gcd"] == 10 and rs[1]["low_bits"][0] == 0 and 10 in rs[1]["frame_gcd"], rs[1]
    assert rs[0]["frame_gcd"].get(1, 0) > 350 and 0.4 < rs[0]["low_bits"][0] < 0.6, rs[0]

    # review of #363: a 1 kHz tone with weak noise, the gain before the rounding, leaves holes in the steps that are
    # not the x10 grid: never 'after'; the same tone after the rounding is
    def tone(gain, scale, sigma, before):
        r = random.Random(42)
        x = [30 * math.sin(2 * math.pi * 1000 * i / 16000) + r.gauss(0, sigma) for i in range(600 * N)]
        x = [round(v * scale) if before else round(round(v) * scale) for v in x]
        return b"".join(frame(i, gain, x[i * N:(i + 1) * N]) for i in range(600))
    for sigma in (0, 0.01, 0.1, 0.2, 0.3):
        v, rs = judge(tone(0x28, 1, sigma, True), tone(0x50, 10, sigma, True))
        assert v != "after", (sigma, verdict(*rs))
    assert judge(tone(0x28, 1, 0.2, False), tone(0x50, 10, 0.2, False))[0] == "after"
    same = stream(0x28, 10)  # the gain byte changed, the level did not: no answer
    v, rs = judge(same, b"".join(frame(i, 0x50, pcm) for i, _, pcm in frames(same)[0]))
    assert v == "inconclusive" and rs[1]["gain"] == 0x50, verdict(*rs)

    # the CLI on captures with nothing to judge: no frames, every sample 0 (a dead mic): an error, not a crash
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        files = {"empty": b"", "normal": stream(0x28, 3), "zeros28": stream(0x28, 0), "zeros50": stream(0x50, 0)}
        for k, b in files.items():
            open(os.path.join(d, k), "wb").write(b)
        for pair in (("empty", "normal"), ("zeros28", "zeros50")):
            p = subprocess.run([sys.executable, __file__, "stats", *(os.path.join(d, k) for k in pair)],
                               capture_output=True, text=True)
            assert p.returncode == 1 and not p.stderr and "verdict: inconclusive: " in p.stdout, (pair, p)
        assert "dead mic" in p.stdout, p.stdout

    # framing: garbage first, a frame cut short, a bad sum, lost frames, and the gain changed mid-capture
    a, b = stream(0x28, 3, n=300), stream(0x50, 3, 10, n=500)
    bf = bytearray(b)
    bf[5 * FRAME + 20] ^= 1  # a bad sum
    s = os.urandom(100) + a[:FRAME * 100 + 50] + a[FRAME * 101:] + bytes(bf[:FRAME * 400]) + bytes(bf[FRAME * 410:])
    fr, bad = frames(s)
    assert len(fr) == 299 + 489 and bad >= 2, (len(fr), bad)
    r = stats(fr, bad)
    assert r["gain"] == 0x50 and r["used"] == 489 - SKIP and r["gaps"] == 1 + 1 + 10, r
    assert stats([])["gain"] is None

    # capture through a pty: bytes a cooked tty rewrites or acts on (CR, LF, ^C, ^D, XON, XOFF, DEL) come through as sent
    import threading
    special = [0x0A0D, 0x0D0A, 0x0304, 0x1311, 0x7F7F, -0x0A0D]
    sent = b"".join(frame(i, 0x28, special * (N // len(special)) + special[:N % len(special)]) for i in range(50))
    master, slave = os.openpty()
    out = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"lr_pcm_selftest_{os.getpid()}.raw")
    t = threading.Thread(target=capture, args=(out, 1.0, os.ttyname(slave)))
    t.start()
    time.sleep(0.3)  # capture has the port open and raw
    os.write(master, sent)
    t.join()
    got = open(out, "rb").read()
    os.remove(out)
    os.close(master)
    os.close(slave)
    assert got == sent, (len(got), len(sent))
    print("selftest ok")


if __name__ == "__main__":
    av = sys.argv[1:]
    arg = lambda k, d=None: av[av.index(k) + 1] if k in av else d
    if av == ["--selftest"]:
        selftest()
    elif av[:1] == ["capture"] and len(av) > 1:
        capture(av[1], float(arg("--seconds", 12)), arg("--port"))
    elif av[:1] == ["stats"] and 2 <= len(av) <= 3:
        rs = [report(p) for p in av[1:]]
        if len(rs) == 2:
            v, why = verdict(*rs)
            if not any("error" in r for r in rs):
                a, b = sorted(rs, key=lambda r: r["gain"])
                print(f"rms {b['gain']:#04x} / {a['gain']:#04x}: {20 * math.log10(b['rms'] / a['rms']):+.1f} dB "
                      f"(the gains say {gain_db(b['gain']) - gain_db(a['gain']):+.1f})")
            print(f"verdict: {v}: {why}")
        sys.exit(1 if any("error" in r for r in rs) else 0)  # a capture with nothing to judge: an error, not a verdict
    else:
        print(__doc__)
        sys.exit(2)
