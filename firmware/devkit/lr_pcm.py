"""Level-cal E1: is the nRF PDM's GAIN applied before or after its 16-bit rounding? Reads the mic's raw PCM that an
LR_RAW_PCM=1 build (src/raw_pcm.c) sends on the USB serial port, and tells from the sample values. Gain applied after
the rounding: at +20 dB (0x50, x10) the samples sit on multiples of ~10, the steps between used values ~10 LSB wide;
before: every value in the dense middle is used, as at 0 dB. Standard library only.
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
    r = {"gain": gain, "frames": len(fr), "used": len(use), "bad": bad, "gaps": gaps, "clipped": clipped}
    if len(x) < 100 * N:
        return {**r, "error": f"{len(x)} samples left after skipping {SKIP} frames: capture longer"}
    mean = sum(x) / len(x)
    r["mean"] = round(mean, 2)
    r["rms"] = math.sqrt(sum((v - mean) ** 2 for v in x) / len(x))
    # GCD of each frame's steps between samples: 1 when values are continuous, ~10 for x10 after rounding
    g = Counter()
    for f in use:
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
    # occupancy: used values / all values between the 2nd and 98th percentile, where each value is expected often
    s = sorted(x)
    lo, hi = s[len(s) // 50], s[-len(s) // 50]
    band = [v for v in s if lo <= v <= hi]
    r["band"] = (lo, hi)
    r["width"] = hi - lo + 1
    r["occupancy"] = len(set(band)) / (hi - lo + 1)
    r["per_value"] = len(band) / (hi - lo + 1)
    return r


def verdict(a: dict, b: dict) -> tuple[str, str]:
    """('after' | 'before' | 'inconclusive', why) for two captures at different gains."""
    if "error" in a or "error" in b:
        return "inconclusive", "a capture is too short"
    a, b = sorted([a, b], key=lambda r: r["gain"])
    step = 10 ** ((gain_db(b["gain"]) - gain_db(a["gain"])) / 20)
    if step < 2:
        return "inconclusive", f"gains {a['gain']:#04x} and {b['gain']:#04x} only x{step:.2f} apart: use 0x28 and 0x50"
    if a["occupancy"] < 0.9:
        return "inconclusive", f"{a['gain']:#04x} already leaves values unused (occupancy {a['occupancy']:.2f})"
    if b["width"] < 3 * step or a["per_value"] < 10 or b["per_value"] < 10:
        return "inconclusive", f"too little spread at {b['gain']:#04x} (band {b['band']}) or too few samples a value: a louder room or a steady soft sound"
    if b["occupancy"] < 0.75:
        used = round(b["occupancy"] * b["width"])
        return "after", f"{b['gain']:#04x}'s used values {(b['width'] - 1) / max(used - 1, 1):.1f} apart (expected {step:.1f} after rounding)"
    if b["occupancy"] >= 0.9:
        return "before", f"{b['gain']:#04x} uses {b['occupancy']:.0%} of values, as {a['gain']:#04x} ({a['occupancy']:.0%})"
    return "inconclusive", f"{b['gain']:#04x} occupancy {b['occupancy']:.2f}: between the two answers"


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
    assert judge(stream(0x28, 2), stream(0x50, 2, 10.07))[0] == "after"  # a gain not exactly x10
    assert judge(stream(0x28, 2), stream(0x3c, 2, 3.16))[0] == "after"  # +10 dB
    assert judge(stream(0x28, 0.3), stream(0x50, 0.3, 10))[0] == "inconclusive"  # a dead quiet mic
    assert judge(stream(0x28, 3), stream(0x2a, 3, 1.12))[0] == "inconclusive"  # gains too close
    v, rs = judge(stream(0x28, 3), stream(0x50, 3, 10))
    assert rs[1]["gcd"] == 10 and rs[1]["low_bits"][0] == 0 and 10 in rs[1]["frame_gcd"], rs[1]
    assert rs[0]["frame_gcd"].get(1, 0) > 350 and 0.4 < rs[0]["low_bits"][0] < 0.6, rs[0]

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
            a, b = sorted(rs, key=lambda r: r["gain"])
            if "rms" in a and "rms" in b:
                print(f"rms {b['gain']:#04x} / {a['gain']:#04x}: {20 * math.log10(b['rms'] / a['rms']):+.1f} dB "
                      f"(the gains say {gain_db(b['gain']) - gain_db(a['gain']):+.1f})")
            print(f"verdict: {v}: {why}")
    else:
        print(__doc__)
        sys.exit(2)
