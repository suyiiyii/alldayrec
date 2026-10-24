"""Reference client for the live-record record stream (src/lr_store.h): sync the clock, take the files (the open one
too), optionally follow the stream (LIVE) for a while and check what came that way against the card, put every frame
back on the wall clock, write one WAV per continuous run; with --server, send the dated runs as Ogg pieces to
/api/chunks under the recorder's name, as the phone app does. --delete removes what was kept (sent, or written here
when undated) from the card; the open file stays. `log` prints the recorder's event log (src/lr_log.h) as a timeline.
    uv run --no-project --with bleak python lr_sync.py OUT_DIR [--follow SECONDS] [--delete] [--name LR-P3-XXXX]
        [--server URL --token T]
    uv run --no-project --with bleak python lr_sync.py log [--name LR-P3-XXXX] [--from SEQ]
"""
import asyncio
import ctypes
import errno
import math
import json
import random
import urllib.request
import datetime as dt
import struct
import sys
import time
import wave
from pathlib import Path

from bleak import BleakClient, BleakScanner

U = lambda n: f"7c3e{n:04x}-1a2b-4c5d-8e9f-6c6976657265"
TIME, CTRL, DATA, LIVE = U(1), U(2), U(3), U(4)
FRAME_MS = 10


def records(b: bytes):
    """('A', first_frame, [opus...]) and ('T', frame, epoch_ms) in file order; a cut-short tail is dropped."""
    i = 0
    while i < len(b):
        if b[i] == 0x41 and i + 6 <= len(b):
            n, first = b[i + 1], struct.unpack_from("<I", b, i + 2)[0]
            j, frames = i + 6, []
            for _ in range(n):
                if j >= len(b) or j + 1 + b[j] > len(b):
                    return
                frames.append(b[j + 1:j + 1 + b[j]])
                j += 1 + b[j]
            yield "A", first, frames
            i = j
        elif b[i] == 0x54 and i + 13 <= len(b):
            yield "T", *struct.unpack_from("<IQ", b, i + 1)
            i += 13
        else:
            return


def runs(files: dict[int, bytes]):
    """files: file id -> bytes. Yields (epoch_ms of the first frame or None, [opus...]) per run of consecutive frames;
    a run is dated by its boot's 'T' records (in any of its files, wherever in the stream they are)."""
    sync, audio = {}, {}
    for f in sorted(files):
        for r in records(files[f]):
            (sync if r[0] == "T" else audio).setdefault(f >> 16, []).append(r[1:])
    for boot, recs in sorted(audio.items()):
        ts = sync.get(boot)
        cur = None
        for first, frames in recs:
            if cur and first == cur[0] + len(cur[1]):
                cur[1].extend(frames)
                continue
            if cur:
                yield cur_time(ts, cur[0]), cur[1]
            cur = [first, list(frames)]
        if cur:
            yield cur_time(ts, cur[0]), cur[1]


def cur_time(ts, frame):
    """The boot's sync nearest the frame (a later one is as good: the mic's clock runs on in between)."""
    if not ts:
        return None
    f, e = min(ts, key=lambda t: abs(t[0] - frame))
    return e + (frame - f) * FRAME_MS


def assemble(parts: dict[int, bytes], start: int = 0) -> tuple[bytes, int | None]:
    """Pieces of a file by offset (READ's DATA, LIVE) -> its bytes from `start` up to the first hole, and where that
    hole is (None: no hole). Pieces may overlap: the stream and the card hold the same bytes at the same offset."""
    out = bytearray()
    for o in sorted(parts):
        end = start + len(out)
        if o > end:
            return bytes(out), end
        out += parts[o][end - o:]
    return bytes(out), None


_CRC = []
for _i in range(256):
    _r = _i << 24
    for _ in range(8):
        _r = ((_r << 1) ^ 0x04C11DB7 if _r & 0x80000000 else _r << 1) & 0xFFFFFFFF
    _CRC.append(_r)


def ogg_opus(frames, frame_ms=FRAME_MS, rate=16000) -> bytes:
    """Opus packets into one Ogg Opus file (RFC 7845), as web/src/lib/ogg.ts and the app's Store.oggOpus make them."""
    serial = random.getrandbits(32)

    def page(packets, flags, granule, seq):
        lacing = []
        for p in packets:
            n = len(p)
            while True:
                lacing.append(min(n, 255))
                if n < 255:
                    break
                n -= 255
        out = bytearray(b"OggS\0" + bytes([flags]) + struct.pack("<qIII", granule, serial, seq, 0) + bytes([len(lacing)] + lacing))
        out += b"".join(packets)
        crc = 0
        for b in out:
            crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[((crc >> 24) ^ b) & 0xFF]
        struct.pack_into("<I", out, 22, crc)
        return bytes(out)

    head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", 0, rate, 0, 0)
    pages = [page([head], 2, 0, 0), page([b"OpusTags" + bytes(8)], 0, 0, 1)]
    for i in range(0, len(frames), 50):
        last = i + 50 >= len(frames)
        pages.append(page(frames[i:i + 50], 4 if last else 0, min(i + 50, len(frames)) * frame_ms * 48, len(pages)))
    return b"".join(pages)


PIECE_S = 60  # a run goes up in pieces of this length


def upload(server: str, token: str, device: str, start: int, frames) -> None:
    """One run, cut into pieces; raises if the server does not take one."""
    n = PIECE_S * 1000 // FRAME_MS
    for i in range(0, len(frames), n):
        req = urllib.request.Request(f"{server}/api/chunks?device={urllib.request.quote(device)}&start={start + i * FRAME_MS}",
                                     ogg_opus(frames[i:i + n]), {"Content-Type": "audio/ogg", "Authorization": f"Bearer {token}"})
        urllib.request.urlopen(req, timeout=60).read()


def decoder():
    """One Opus decoder: frame -> 16 kHz 16-bit PCM; a run's frames go through the same one, in order."""
    lib = ctypes.CDLL("/opt/homebrew/lib/libopus.dylib")
    lib.opus_decoder_create.restype = ctypes.c_void_p
    err = ctypes.c_int()
    dec = ctypes.c_void_p(lib.opus_decoder_create(16000, 1, ctypes.byref(err)))
    buf = (ctypes.c_int16 * 960)()

    def frame(f: bytes) -> bytes:
        n = lib.opus_decode(dec, f, len(f), buf, 960, 0)
        return bytes(buf)[: 2 * max(n, 0)]
    return frame


def decode(frames) -> bytes:
    return b"".join(map(decoder(), frames))


async def main(out: Path, delete: bool, name: str, follow: float, server: str | None, token: str):
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: U(0) in a.service_uuids and (a.local_name or "").startswith(name), 20)
    if not dev:
        sys.exit("no device")
    async with BleakClient(dev) as c:
        replies, data, live = asyncio.Queue(), {}, {}
        last = [time.time()]  # the last DATA notify: a file takes as long as it takes while bytes keep coming

        def on_data(_, d):
            data[struct.unpack_from("<I", d)[0]] = bytes(d[4:])
            last[0] = time.time()

        await c.start_notify(CTRL, lambda _, d: replies.put_nowait(bytes(d)))
        await c.start_notify(DATA, on_data)
        if follow:  # before LIST: nothing the stream adds after it is missed
            await c.start_notify(LIVE, lambda _, d: live.setdefault(struct.unpack_from("<I", d)[0], {}).__setitem__(
                struct.unpack_from("<I", d, 4)[0], bytes(d[8:])))
        now = int(time.time() * 1000)
        await c.write_gatt_char(TIME, struct.pack("<Q", now), response=True)  # a 'T' record, into the stream
        boot, frame, store, free_kb, total_kb = struct.unpack("<HIhII", (await c.read_gatt_char(TIME))[:16])
        print(f"boot {boot}, frame {frame} = {dt.datetime.fromtimestamp(now / 1000)}, store {store}, "
              f"card {free_kb / 1024:.0f} of {total_kb / 1024:.0f} MB free")

        async def listing():
            await c.write_gatt_char(CTRL, b"\x01", response=True)
            files = []
            while (r := await asyncio.wait_for(replies.get(), 10))[0] != 0x80:
                files.append(struct.unpack_from("<II", r, 1))
            return files, struct.unpack_from("<I", r, 2)[0]

        async def read(f: int, off: int) -> dict[int, bytes]:
            """READ from off to where the card's file ends (the open one: what the stream has finished)."""
            data.clear()
            last[0] = time.time()
            await c.write_gatt_char(CTRL, struct.pack("<BII", 2, f, off), response=True)
            while True:
                try:
                    if (await asyncio.wait_for(replies.get(), 5))[0] == 0x82:
                        return dict(data)
                except TimeoutError:
                    if time.time() - last[0] > 15:
                        sys.exit(f"{f:08X}: stalled")

        files, open_file = await listing() if not store else ([], None)
        print("files:", [(f"{f:08X}", s) for f, s in files], f"open {open_file:08X}" if open_file is not None else "")
        got = {}
        for f, size in files:
            t = time.time()
            b, hole = assemble(await read(f, 0))
            ok = hole is None and len(b) >= size  # the open file grows while it is read
            t = time.time() - t
            print(f"{f:08X}: {len(b)} B of {size} in {t:.1f} s, {len(b) / 1024 / t:.1f} KB/s{'' if ok else '  INCOMPLETE'}")
            if ok:
                got[f] = b
        if follow:
            print(f"following the stream for {follow:.0f} s")
            await asyncio.sleep(follow)
            await c.stop_notify(LIVE)
            for f, parts in sorted(live.items()):
                # the mirror: what READ took, then the stream; a hole (a lost notify) is READ again from there
                b, hole = assemble({0: got.get(f, b""), **parts})
                if hole is not None:
                    print(f"{f:08X}: hole at {hole}, READ from there")
                    b, hole = assemble({0: b, **(await read(f, len(b))), **parts})
                card, _ = assemble(await read(f, 0))  # the card's copy, now
                n = min(len(b), len(card))
                print(f"{f:08X}: stream {len(b)} B, card {len(card)} B, first {n} B "
                      f"{'the same' if b[:n] == card[:n] else 'DIFFER'}{'' if hole is None else f', hole at {hole}'}")
                got[f] = b
        for f, b in got.items():
            (out / f"{f:08X}.BIN").write_bytes(b)  # as on the card, for a second look
        for i, (start, frames) in enumerate(runs(got)):
            when = dt.datetime.fromtimestamp(start / 1000).strftime("%m%d-%H%M%S.%f")[:-3] if start else "undated"
            p = out / f"{i:03d}-{when}.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(decode(frames))
            sent = ""
            if server and start:
                upload(server, token, dev.name, start, frames)  # a failure stops here: nothing is deleted
                sent = f", sent as {dev.name}"
            print(f"run {i}: {len(frames) * FRAME_MS / 1000:.2f} s from {when} -> {p.name}{sent}")
        if delete:
            for f in got:
                if f == open_file:
                    continue  # the stream goes on in it
                await c.write_gatt_char(CTRL, struct.pack("<BI", 4, f), response=True)
                r = await asyncio.wait_for(replies.get(), 10)
                print(f"delete {f:08X}: {struct.unpack_from('<b', r, 5)[0]}")


#
# The event log (src/lr_log.h): lr_sync.py log [--name NAME] [--from SEQ]
#
REC = struct.Struct("<HIBBII")  # boot t code x a b
UPTIME = 0x80
RESETREAS = {0: "RESETPIN", 1: "DOG", 2: "SREQ", 3: "LOCKUP", 16: "OFF", 17: "LPCOMP", 18: "DIF", 19: "NFC", 20: "VBUS"}
HCI = {0x05: "auth failure", 0x06: "PIN or key missing", 0x08: "supervision timeout", 0x13: "remote user terminated",
       0x14: "remote low resources", 0x15: "remote power off", 0x16: "local host terminated",
       0x1A: "unsupported remote feature", 0x1F: "unspecified", 0x22: "LL response timeout", 0x28: "instant passed",
       0x2A: "transaction collision", 0x3B: "unacceptable parameters", 0x3D: "MIC failure",
       0x3E: "failed to establish"}
SECURITY = ["success", "auth fail", "PIN or key missing", "OOB not available", "auth requirement",
            "pair not supported", "pair not allowed", "invalid param", "key rejected", "unspecified"]
FAULT = ["CPU exception", "spurious IRQ", "stack check", "kernel oops", "kernel panic"]
ERRNO = {**{n: errno.errorcode[n] for n in range(1, 35) if n in errno.errorcode},  # the same everywhere up to 34
         77: "EBADMSG", 105: "ENOBUFS", 116: "ETIMEDOUT", 120: "EALREADY", 128: "ENOTCONN", 134: "ENOTSUP"}  # Zephyr's


# USB_DISK x 0's b (lr_log.h): why the card came back
BACK = ("", "the phone", "unplugged", "the computer left it 3 min")  # 0: firmware older than this field
# APPEND_ERR's x (lr_log.h): which part of an append failed
APPEND = ("", "writer behind, rest of the segment LIVE only: ", "restart with the card stuck: ", "file length not on the card: ")


def err(v):
    v = struct.unpack("<i", struct.pack("<I", v))[0]
    return f"{v} {'-' + ERRNO[-v] if -v in ERRNO else ''}".strip() if v else "ok"


def reas(v):
    return "+".join(n for b, n in RESETREAS.items() if v >> b & 1) or "power-on"


def param(a, b):
    return f"interval {(a & 0xFFFF) * 1.25:g} ms, latency {a >> 16}, timeout {b * 10} ms"


def stack(v):
    return "-" if v == 0xFFFF else str(v)


def sd_writes(x, a, b):
    n = [b & 0x3FF, b >> 10 & 0x3FF, b >> 20 & 0x3FF, x]
    return (f"{sum(n)} appends, {a & 0xFFFFF} ms in all, slowest {a >> 20} ms; "
            f"under 20 ms {n[0]}, 20-49 ms {n[1]}, 50-200 ms {n[2]}, over 200 ms {n[3]}")


def i8(v):
    return v - 256 if v > 127 else v


def peak(p):
    return f"{20 * math.log10(p / 256):.0f} dBFS" if p else "under -48 dBFS"


def health(x, a, b):
    return (f"{i8(x)} °C, mic all zero {a & 0xFFFFF} ms, {a >> 20} PDM errors, {b & 0xFFFFFF} samples at full scale, "
            f"loudest {peak(b >> 24)}")


def card_id(x, a, b):
    name = (struct.pack("<I", a) + bytes([b & 0xFF])).decode("ascii", "replace")
    oem = bytes([b >> 8 & 0xFF, b >> 16 & 0xFF]).decode("ascii", "replace")
    return f"manufacturer 0x{x:02x}, OEM {oem!r}, product {name!r} rev {b >> 28}.{b >> 24 & 0xF}"


def card_size(x, a, b):
    return (f"{'SDHC/SDXC' if x else 'SDSC'}, {a * 512 / 1e9:.2f} GB ({a} sectors), "
            f"made {2000 + (b >> 4)}-{b & 0xF:02d}")


def sd_errors(x, a, b):
    return (f"{a & 0xFFFF} CRC errors, {a >> 16} timeouts ({b & 0xFFFF} busy), {b >> 16} other errors, "
            f"{x} transfers tried again")


def read_done(x, a, b):
    ms = b & 0x3FFFFF
    return (f"{a / 1024:.1f} KB in {ms / 1000:.1f} s ({a / 1024 / max(ms, 1) * 1000:.1f} KB/s), "
            f"{b >> 22} B a packet, interval {x * 1.25:g} ms")


EVENTS = {
    1: ("BOOT", lambda x, a, b: f"reset {reas(a)} (0x{a:x}), battery {x * 20} mV, fw {b & 0xFFFFFFF:07x}{'+' * (b >> 28 & 1)}"),
    2: ("FAULT", lambda x, a, b: f"{FAULT[a] if a < len(FAULT) else a}, PC 0x{b:08x} (previous boot)"),
    3: ("MOUNT", lambda x, a, b: f"{f'try {x}' if x else 'again'}: {err(a)}"),  # x 0: a failed card tried again
    4: ("STORE", lambda x, a, b: f"audio boot {a} (files {a:04X}xxxx), {b // 1024} MB free" + ", BOOT unread" * (x & 1) + ", BOOT unwritten" * (x >> 1 & 1)),
    5: ("SLOW", lambda x, a, b: f"SD append {a} ms ({b} B)"),
    6: ("APPEND_ERR", lambda x, a, b: f"{APPEND[x] if x < len(APPEND) else ''}{err(a)} ({b} B)"),
    7: ("CARD_FULL", lambda x, a, b: f"{'recording stopped' if x else 'resumed'}, {a} KB free"),
    8: ("USB_DISK", lambda x, a, b: f"card lent to USB" if x else f"card back{f' ({BACK[b] if b < len(BACK) else b})' if b else ''}: {err(a)}"),
    9: ("CONNECT", lambda x, a, b: param(a, b)),
    10: ("DISCONNECT", lambda x, a, b: f"0x{x:02x} {HCI.get(x, '')}".strip()),
    11: ("PARAM", lambda x, a, b: param(a, b)),
    12: ("MTU", lambda x, a, b: f"{'link data length' if x else 'ATT MTU'} tx {a} rx {b}"),
    13: ("PHY", lambda x, a, b: f"tx {a} rx {b} (1: 1M, 2: 2M, 4: coded)"),
    14: ("TIME", lambda x, a, b: dt.datetime.fromtimestamp((b << 32 | a) / 1000).isoformat(" ", "milliseconds")),
    15: ("SECURITY", lambda x, a, b: f"level {x}" + (f", {SECURITY[a] if a < len(SECURITY) else a}" if a else "")),
    16: ("PAIRED", lambda x, a, b: "bonded" if x else "not bonded"),
    17: ("PAIR_FAIL", lambda x, a, b: SECURITY[x] if x < len(SECURITY) else str(x)),
    18: ("FORGET", lambda x, a, b: f"{a} bond(s): {'stale bond' if x else 'a phone asked'}"),
    19: ("DROPPED", lambda x, a, b: f"{a} frames, {b} this boot"),
    20: ("SEGMENT", lambda x, a, b: f"{a:08X}.BIN"),
    21: ("DFU", lambda x, a, b: {0x57: "into the UF2 bootloader", 0xA8: "into the OTA bootloader"}.get(x, f"0x{x:02x}")),
    22: ("HEARTBEAT", lambda x, a, b: f"battery {x * 20} mV, {a // 1024} MB free, {b} frames dropped"),
    23: ("STACKS", lambda x, a, b: f"unused: codec {stack(a >> 16)}, pusher {stack(a & 0xFFFF)}, "
                                   f"store {stack(b >> 16)}, BT RX {stack(b & 0xFFFF)} B"),
    24: ("SD_RESET", lambda x, a, b: f"CMD12 r1 0x{a:02x}, CMD0 r1 0x{b:02x}" + (" (idle)" if b == 1 else " (no idle: card not answering)")),
    25: ("SD_WRITES", sd_writes),
    26: ("READ", read_done),
    27: ("READ_TIME", lambda x, a, b: f"card {a} ms, notify {b} ms"),
    28: ("SWITCH", lambda x, a, b: f"{'on' if x else 'off'}: " + (
        ["recording", "System OFF", "up on USB, not recording", "restart"][a] if a < 4 else str(a))),
    29: ("POWER", lambda x, a, b: {0: f"on battery, VDDH {x * 20} mV",
                                   1: "power-fail warning: VDDH under 3.3 V or VDD under 2.8 V",
                                   2: f"low battery ({x * 20} mV): recording ended, System OFF until USB or the switch"}.get(a, str(a))),
    30: ("LIVE_LOST", lambda x, a, b: f"{a} records ({b} B) left out of LIVE ({'link lost' if x else 'link stalled'}),"
                                      " on the card all the same"),
    31: ("HEALTH", health),
    32: ("CARD_ID", card_id),
    33: ("CARD_SIZE", card_size),
    34: ("SD_ERRORS", sd_errors),
}


def log_records(packets):
    """DATA notifications of a LOG (seq:u32 + 16 B records) -> {seq: (boot, t, code, x, a, b)}."""
    out = {}
    for p in packets:
        seq = struct.unpack_from("<I", p)[0]
        for i in range((len(p) - 4) // REC.size):
            out[seq + i] = REC.unpack_from(p, 4 + i * REC.size)
    return out


def timeline(recs):
    """(seq, wall time or a boot-relative uptime, boot, name, args) in seq order: a frame goes on the wall clock by its
    boot's TIME nearest to it; a boot whose TIMEs fell out of the log (the oldest sectors go first) stays on frames."""
    sync = {}
    for boot, t, code, x, a, b in recs.values():
        if code == 14:
            sync.setdefault(boot, []).append((t, b << 32 | a))
    for seq in sorted(recs):
        boot, t, code, x, a, b = recs[seq]
        name, fmt = EVENTS.get(code & ~UPTIME, (f"0x{code & ~UPTIME:02x}", lambda x, a, b: f"x {x} a {a} b {b}"))
        if code & UPTIME:
            when = f"(uptime {t / 1000:.3f} s)"
        elif (e := cur_time(sync.get(boot), t)) is not None:
            when = dt.datetime.fromtimestamp(e / 1000).isoformat(" ", "milliseconds")
        else:
            when = f"(frame {t})"
        yield seq, when, boot, name, fmt(x, a, b)


async def log(name: str, start: int):
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: U(0) in a.service_uuids and (a.local_name or "").startswith(name), 20)
    if not dev:
        sys.exit("no device")
    async with BleakClient(dev) as c:
        # this boot's frames on the wall clock: the TIME write is an event too
        await c.write_gatt_char(TIME, struct.pack("<Q", int(time.time() * 1000)), response=True)
        replies, packets = asyncio.Queue(), []
        await c.start_notify(CTRL, lambda _, d: replies.put_nowait(bytes(d)))
        await c.start_notify(DATA, lambda _, d: packets.append(bytes(d)))
        await c.write_gatt_char(CTRL, struct.pack("<BI", 7, start), response=True)
        while (r := await asyncio.wait_for(replies.get(), 30))[0] != 0x87:
            pass
        nxt = struct.unpack_from("<I", r, 1)[0]
    print(f"{dev.name}:")
    for seq, when, boot, ev, args in timeline(log_records(packets)):
        print(f"{seq:6d}  {when:26s} boot {boot:<4d} {ev:11s} {args}")
    print(f"next: lr_sync.py log --from {nxt}")


def selftest():
    fixture = json.loads((Path(__file__).resolve().parents[2] / "fixtures/record-stream.json").read_text())
    files = {f["file"]: bytes.fromhex(f["hex"]) for f in fixture["archive"]["files"]}
    got = list(runs(files))
    assert [[s, len(f)] for s, f in got] == fixture["archive"]["runs"], got
    v = fixture["mirror"]
    stream, parts = bytes.fromhex(v["hex"]), {}
    for d in v["deliveries"]:
        parts[d["start"]] = stream[d["start"]:d["end"]]
        merged, hole = assemble(parts)
        assert hole == d["hole"], (hole, d)
    assert merged == stream
    # The snapshot reader merges consecutive frames; live clients slice at liveFrames.
    # Compare their frame clocks, not the transport-dependent piece boundaries.
    actual = [s + i * FRAME_MS for s, frames in runs({v["file"]: merged}) for i in range(len(frames))]
    expected = [s + i * FRAME_MS for s, count in v["pieces"] for i in range(count)]
    assert actual == expected, (actual, expected)
    o = fixture["ogg"]
    ogg = ogg_opus([bytes.fromhex(o["packetHex"])] * o["count"])
    assert ogg[:4] == b"OggS" and ogg.count(b"OggS") == o["pages"]
    # Snapshot parser: incomplete tails are dropped, not buffered. No persisted boot-reuse API.

    # the event log, wrapped: the oldest sectors went, so boot 7 starts mid-way (its BOOT and TIME gone); LOG packets
    # as the device sends them (seq + records), one sent twice by an overlapping read, out of order
    r = lambda boot, t, code, x=0, a=0, b=0: REC.pack(boot, t, code, x, a, b)
    pkt = lambda seq, *recs: struct.pack("<I", seq) + b"".join(recs)
    when = lambda e: dt.datetime.fromtimestamp(e / 1000).isoformat(" ", "milliseconds")
    packets = [pkt(5003, r(8, 400, 9, 0, 24 | 0 << 16, 400), r(8, 1000, 14, 0, 1_700_000_000_000 & 0xFFFFFFFF, 1_700_000_000_000 >> 32)),
               pkt(5000, r(7, 123456, 22, 200, 2048, 7), r(8, 1500, 1 | UPTIME, 205, 0x4, 0x1abcdef | 1 << 28)),
               pkt(5002, r(8, 3200, 3 | UPTIME, 1, (-5) & 0xFFFFFFFF)),
               pkt(5003, r(8, 400, 9, 0, 24, 400)),
               pkt(5005, r(8, 2000, 10, 0x13), r(8, 2100, 0x63, 1, 2, 3))]
    got = [(seq, w, boot, n, a) for seq, w, boot, n, a in timeline(log_records(packets))]
    assert got == [
        (5000, "(frame 123456)", 7, "HEARTBEAT", "battery 4000 mV, 2 MB free, 7 frames dropped"),
        (5001, "(uptime 1.500 s)", 8, "BOOT", "reset SREQ (0x4), battery 4100 mV, fw 1abcdef+"),
        (5002, "(uptime 3.200 s)", 8, "MOUNT", "try 1: -5 -EIO"),
        (5003, when(1_700_000_000_000 - 6000), 8, "CONNECT", "interval 30 ms, latency 0, timeout 4000 ms"),
        (5004, when(1_700_000_000_000), 8, "TIME", when(1_700_000_000_000)),
        (5005, when(1_700_000_010_000), 8, "DISCONNECT", "0x13 remote user terminated"),
        (5006, when(1_700_000_011_000), 8, "0x63", "x 1 a 2 b 3")], got
    assert EVENTS[25][1](1, 601 | 250 << 20, 4 | 2 << 10 | 2 << 20) == \
        "9 appends, 601 ms in all, slowest 250 ms; under 20 ms 4, 20-49 ms 2, 50-200 ms 2, over 200 ms 1"
    assert EVENTS[3][1](0, (-116) & 0xFFFFFFFF, 0) == "again: -116 -ETIMEDOUT"
    assert EVENTS[28][1](0, 1, 0) == "off: System OFF" and EVENTS[28][1](1, 9, 0) == "on: 9"
    assert EVENTS[29][1](204, 0, 0) == "on battery, VDDH 4080 mV"
    assert EVENTS[29][1](165, 2, 0).startswith("low battery (3300 mV)")
    assert EVENTS[8][1](0, 0, 3) == "card back (the computer left it 3 min): ok" and EVENTS[8][1](0, 0, 0) == "card back: ok"
    assert EVENTS[30][0] == "LIVE_LOST" and EVENTS[30][1](0, 12, 4800).startswith("12 records (4800 B) left out of LIVE (link stalled)")
    assert EVENTS[30][1](1, 3, 1100).startswith("3 records (1100 B) left out of LIVE (link lost)")
    assert EVENTS[26][1](12, 409600, 10000 | 491 << 22) == \
        "400.0 KB in 10.0 s (40.0 KB/s), 491 B a packet, interval 15 ms"
    assert EVENTS[27][1](0, 3000, 6500) == "card 3000 ms, notify 6500 ms"
    assert EVENTS[6][1](3, (-5) & 0xFFFFFFFF, 4096) == "file length not on the card: -5 -EIO (4096 B)"
    assert EVENTS[31][1](0xF6, 230 | 3 << 20, 17 | 128 << 24) == \
        "-10 °C, mic all zero 230 ms, 3 PDM errors, 17 samples at full scale, loudest -6 dBFS"
    assert EVENTS[31][1](31, 0, 0).endswith("loudest under -48 dBFS")
    assert EVENTS[32][1](0x03, int.from_bytes(b"SC32", "little"), ord("G") | ord("S") << 8 | ord("D") << 16 | 0x80 << 24) == \
        "manufacturer 0x03, OEM 'SD', product 'SC32G' rev 8.0"
    assert EVENTS[33][1](1, 62333952, 23 << 4 | 7) == "SDHC/SDXC, 31.91 GB (62333952 sectors), made 2023-07"
    assert EVENTS[34][1](4, 2 | 5 << 16, 3 | 1 << 16) == \
        "2 CRC errors, 5 timeouts (3 busy), 1 other errors, 4 transfers tried again"
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    arg = lambda k, default=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else default
    if sys.argv[1] == "log":
        sys.exit(asyncio.run(log(arg("--name", "LR-"), int(arg("--from", 0)))))
    d = Path(sys.argv[1])
    d.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(d, "--delete" in sys.argv, arg("--name", "LR-"), float(arg("--follow", 0)), arg("--server"),
                     arg("--token", "")))
