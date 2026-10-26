"""The audio store: what clients upload goes in, one endless time-stamped stream of 16 kHz mono samples comes out.

Only this module knows how audio is kept (today: each uploaded piece appended unchanged to its device's file for the
hour it starts in, chunks/<device>/<date>/<HH>.<ext>, and indexed in the pieces table by path, pos and size; one file a
piece made 3600 tiny files an hour while the app was in front). Each device is its own stream: the pipeline of a device reads `stream(device, after)`, the playback reads `cut()`;
neither cares about formats, files or piece lengths. A stream pauses when nothing new has arrived and when its reader
stops asking (it pulls at its own pace), and a gap between two pieces is only a jump in their start times. A device
that sends a backlog of older audio while another one is recording is streamed on its own, not dropped. A device's
pieces may overlap (a recorder's card read again over what its phone sent live: both kept); each reader takes every
moment once (fresh).
"""
import contextlib
import datetime as dt
import io
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import anyio
import anyio.to_thread
import numpy as np

from alldayrec import db
from alldayrec import perf
from alldayrec import threads
from alldayrec import ws
from alldayrec.tenant import T

log = logging.getLogger("live")
SR = 16000
# the current tenant's recordings dir: T().recordings; per device, the start of the last piece its reader has finished
# with (later ones are queued): T().read_to
HOLD_MAX_S = 12 * 3600  # total life of one hold boundary, including silence, reconnects and progress
HOLD_STALE_S = int(os.environ.get("HOLD_STALE_S", "1200"))  # no older audio arriving while the phone keeps saying hold
_holds = threading.RLock()  # a hold refresh, older-piece arrival and expiry must preserve each other's persisted state
_append = threading.Lock()  # uploads are saved in threads (threads.MEDIA): one append (and its index row) at a time


AAC_DELAY = 2112  # samples an AAC encoder puts before the audio (at the iPhone's 16 kHz: 132 ms)
AAC_FRAME = 1024  # it pads the end to whole frames: 0 to 1023 samples, unknown, so half is cut


def unpad(x: np.ndarray) -> np.ndarray:
    """An ADTS AAC piece without its encoder's delay and (on average) its last-frame padding. Each iPhone piece is a
    new encoder: decoded, a piece was 0.18 s longer than its audio, overlapped the next piece and spliced 0.1 s of
    silence into speech at every piece; this leaves at most +-32 ms."""
    return x[AAC_DELAY:len(x) - AAC_FRAME // 2] if len(x) > AAC_DELAY + AAC_FRAME else x


def packets(ogg: bytes) -> list[bytes]:
    """The packets of an Ogg stream (RFC 3533) in order, Opus's two header packets included."""
    out, cur, i = [], b"", 0
    while ogg[i:i + 4] == b"OggS" and i + 27 <= len(ogg):
        n = ogg[i + 26]
        j = i + 27 + n
        for size in ogg[i + 27:j]:
            cur += ogg[j:j + size]
            j += size
            if size < 255:
                out.append(cur)
                cur = b""
        i = j
    return out


def _crc_table() -> list[int]:
    out = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = (r << 1) ^ 0x04C11DB7 if r & 0x80000000 else r << 1
        out.append(r & 0xFFFFFFFF)
    return out


_CRC = _crc_table()  # Ogg's CRC-32: polynomial 0x04C11DB7, not reflected, no initial value or final xor


def ogg(audio_packets: list[bytes], frame_ms: int = 10, pre_skip: int = 0, channels: int = 1) -> bytes:
    """Opus packets (each one frame of frame_ms) as one 16 kHz Ogg Opus stream (RFC 7845), a packet a page;
    pre_skip in 48 kHz samples, decoded and dropped by the reader."""
    pages = []

    def page(packet: bytes, flags: int, granule: int):
        lace = bytes([255] * (len(packet) // 255) + [len(packet) % 255])
        p = bytearray(b"OggS\0" + bytes([flags]) + struct.pack("<qIII", granule, 1, len(pages), 0) + bytes([len(lace)]) + lace + packet)
        crc = 0
        for b in p:
            crc = (crc << 8 & 0xFFFFFFFF) ^ _CRC[crc >> 24 ^ b]
        struct.pack_into("<I", p, 22, crc)
        pages.append(bytes(p))
    page(b"OpusHead" + struct.pack("<BBHIhB", 1, channels, pre_skip, SR, 0, 0), 2, 0)
    page(b"OpusTags" + bytes(8), 0, 0)
    for k, p in enumerate(audio_packets):
        page(p, 4 if k == len(audio_packets) - 1 else 0, (k + 1) * frame_ms * 48)
    return b"".join(pages)


PREROLL = 8  # packets (80 ms, libopus's advice) of the piece before that a recorder piece's decoder hears first
DEHUM = os.environ.get("DEHUM", "1") != "0"  # a recorder's 10 ms hum taken out of its pieces as they are read (dehum); 0: as decoded
QUIET = 32768 * 10 ** (-60 / 20)  # a 10 ms frame under this RMS (-60 dBFS) has no one speaking in it
DEHUM_MIN = 50  # quiet frames (0.5 s) a piece needs for its hum to be taken out


def celt(packet: bytes) -> int:
    """The ms of an Opus packet whose TOC says CELT, one frame of 10 ms (a recorder's encoder, firmware codec.c) or
    20 ms (the Feishu bean's, stereo); 0 for anything else."""
    return {2: 10, 3: 20}.get(packet[0] >> 3 & 3, 0) if len(packet) > 0 and packet[0] >> 3 >= 16 and packet[0] & 3 == 0 else 0


def dehum(x: np.ndarray) -> np.ndarray:
    """A recorder piece without its board's hum. Since the 10 ms microphone buffers the encoder's burst of work
    in each 10 ms frame couples into the microphone (on battery: 100 Hz at about -80 dBFS, the level of a quiet room's
    floor, and its harmonics): a waveform that repeats every 160 samples. The mean of the piece's quiet frames is that
    waveform (the room's noise averages out) and is taken from every frame, speech too (the hum is under it as well);
    a piece is whole frames, so whatever its offset the waveform lines up. x unchanged with fewer than DEHUM_MIN quiet
    frames (a loud room; speech throughout). The 10 Hz flutter of the 100 ms buffers before is not a fixed waveform (the
    noise's power rises and falls) and stays."""
    f = x[:len(x) // 160 * 160].reshape(-1, 160).astype(np.float64)
    quiet = np.sqrt((f * f).mean(1)) < QUIET
    if quiet.sum() < DEHUM_MIN:
        return x
    return np.clip(np.round(x - np.resize(f[quiet].mean(0), len(x))), -32768, 32767).astype(np.int16)


def primed(prev: bytes, body: bytes) -> bytes:
    """body, an Ogg Opus piece of a recorder, with the last PREROLL packets of the piece it goes on from (`prev`) put
    ahead of its own and skipped (pre-skip): the decoder then has the state the board's encoder had. A recorder's pieces
    are slices of one running encoder (CELT, 10 ms frames, pre-skip 0: the app's Store.oggOpus, lr_sync; a Feishu
    bean's: 20 ms, stereo); a fresh decoder starting inside it put out the piece's first 10-40 ms up to 26 dB too loud,
    a click at every join, each second while the app is in front (10-08). Anything else (an iPhone's own microphone:
    SILK, 20 ms; a phone that starts each piece anew) is body unchanged."""
    ps, prev = packets(body), packets(prev)[2:][-PREROLL:]
    ms = celt(ps[2]) if len(ps) > 2 else 0
    if (not ms or not ps[0].startswith(b"OpusHead") or struct.unpack_from("<H", ps[0], 10)[0] != 0
            or len(prev) < PREROLL or any(celt(p) != ms for p in prev)):
        return body
    return ogg(prev + ps[2:], ms, PREROLL * ms * 48, ps[0][9])


def before(device: str, start_ms: int, max_bytes: int | None = None) -> bytes | None:
    """The stored Ogg piece of `device` that ends where start_ms begins (within a frame), as uploaded; None after a gap."""
    p = db.conn().execute("SELECT * FROM pieces WHERE device = ? AND start_ms < ? ORDER BY start_ms DESC LIMIT 1", (device, start_ms)).fetchone()
    if p is None or not p["path"].endswith(".ogg") or abs(p["start_ms"] + p["seconds"] * 1000 - start_ms) >= 10:
        return None
    return None if max_bytes is not None and p["size"] > max_bytes else read(p)


def decode(body: bytes, aac: bool = False, n: int | None = None, prev: bytes | None = None) -> np.ndarray | None:
    """One piece's bytes as 16 kHz mono int16; None when ffmpeg cannot read them. aac: an ADTS piece, cut to the n
    samples its client wrote (exact), or unpadded when it did not say (older apps, a piece cut short by a kill).
    prev: the piece this one goes on from (before()), for a recorder's decoder to start where it left off (primed)."""
    if prev is not None:
        body = primed(prev, body)
    run = lambda args, data: subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *args, "-f", "s16le", "-ac", "1", "-ar", str(SR),
                                             "pipe:1"], input=data, capture_output=True)

    def from_file(*args):  # an .m4a keeps its index (moov) after the audio: ffmpeg cannot seek back to it in a pipe
        with tempfile.NamedTemporaryFile() as f:  # (10-26: an imported 60 s one decoded to nothing)
            f.write(body)
            f.flush()
            return run([*args, "-i", f.name], None)

    # MP4/M4A/3GP straight from a file, with the mov demuxer only (no probing of other local-file readers)
    out = from_file("-f", "mov") if body[4:8] == b"ftyp" else run(["-i", "pipe:0"], body)
    if out.returncode or len(out.stdout) < 2:  # an Opus piece can be one 20 ms packet (the last one of a run)
        out = from_file()  # an old .mov, a leading free atom
    if out.returncode or len(out.stdout) < 2:
        return None
    x = np.frombuffer(out.stdout[:len(out.stdout) // 2 * 2], np.int16)
    if aac and n:
        return x[AAC_DELAY:AAC_DELAY + n]
    return unpad(x) if aac else x


HUM = np.exp(-2j * np.pi * np.outer(np.arange(SR), np.arange(50, 251, 50)) / SR)  # a second's DFT at 50..250 Hz
ZERO_RUN = SR // 100  # exact zeros this long (10 ms) in a row: the mic sent nothing (the recorder's high-pass turns a stuck input into 0)


def health(x: np.ndarray) -> tuple[float, float, int, int]:
    """How a piece sounds, measured as lr_listen.py does each second (no content: levels only): RMS and mains hum (the
    50..250 Hz harmonics, each second's, averaged) in dBFS, ms in runs of exact zeros of 10 ms or more (no data from
    the mic: a loose wire), and full-scale samples (clipping)."""
    f = x.astype(np.float64)
    hum = sum((2 * np.abs(f[i:i + SR] @ HUM[:len(f[i:i + SR])]) ** 2).sum() / len(f[i:i + SR]) for i in range(0, len(f), SR))
    edges = np.flatnonzero(np.diff(np.r_[0, x == 0, 0]))  # where each run of zeros starts and ends
    runs = edges[1::2] - edges[::2]
    dbfs = lambda ms: round(10 * np.log10(max(ms, 1e-3) / 32768 ** 2), 1)
    return (dbfs(np.mean(f * f) if len(f) else 0), dbfs(hum / max(len(f), 1)), int(runs[runs >= ZERO_RUN].sum()) * 1000 // SR,
            int(np.count_nonzero((x >= 32767) | (x <= -32767))))


def hour_file(device: str, start_ms: int, ext: str) -> Path:
    """Where a piece starting at start_ms goes, relative to the recordings dir: its device's file for that (local) hour."""
    t = dt.datetime.fromtimestamp(start_ms / 1000)
    return Path("chunks") / re.sub(r"[^\w.-]", "_", device) / f"{t:%Y-%m-%d}" / f"{t:%H}.{ext}"


def append(path: Path, body: bytes) -> int:
    """Appends body to the recordings dir / path and returns where it starts; on disk (fsync) when this returns. Hold _append."""
    f = T().recordings / path
    new = not f.exists()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "ab") as h:
        pos = h.seek(0, os.SEEK_END)  # a failed write before leaves bytes no row points at: harmless
        h.write(body)
        h.flush()
        os.fsync(h.fileno())
    if new:  # the file's name is durable too
        d = os.open(f.parent, os.O_RDONLY)
        os.fsync(d)
        os.close(d)
    return pos


def save(device: str, start_ms: int, body: bytes, content_type: str, n: int | None = None) -> float | None:
    """Keeps one uploaded piece (start_ms: its device time, n: the samples its client wrote, when it says); returns
    its seconds, None when it is not audio. It is on disk and in the index before this returns, so the client may
    delete its copy once told so. Of a piece sent again (same device and start) the longer copy (in bytes) is kept: a
    resend as long or shorter only gets the kept copy's seconds back; a longer one replaces the row, appended to the
    hour file (an Android batch retried with more pieces after the answer to the first try was lost)."""
    kept = lambda: db.conn().execute("SELECT seconds FROM pieces WHERE device = ? AND start_ms = ? AND size >= ?",
                                     (device, start_ms, len(body))).fetchone()
    if r := kept():
        return r[0]
    ext = {"audio/ogg": "ogg", "audio/webm": "webm", "audio/aac": "aac"}.get(content_type.split(";")[0], "bin")
    x = decode(body, aac=ext == "aac", n=n, prev=before(device, start_ms) if ext == "ogg" else None)
    if x is None:  # the client drops it on the 400 that follows: its bytes stay here, for a look later
        f = T().recordings / "rejected" / re.sub(r"[^\w.-]", "_", device) / f"{start_ms}.{ext}"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(body)
        return None
    path = hour_file(device, start_ms, ext)
    h = health(x)
    with _append:
        if r := kept():  # the same piece in two requests at once
            return r[0]
        pos = append(path, body)
        db.conn().execute("INSERT OR REPLACE INTO pieces (device, start_ms, seconds, path, pos, size, n, received_ms, rms, hum, zero_ms, full) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (device, start_ms, round(len(x) / SR, 3), str(path), pos, len(body), n, int(time.time() * 1000), *h))
        with _holds:
            held = db.kv(f"hold:{device}")
            if held and start_ms < held["at"] and not held.get("released"):
                held["progress"] = time.time()
                db.put_kv(f"hold:{device}", held)
    return len(x) / SR


BLOCK_S = 10  # long files: bounded decode/health/VAD working sets
WHOLE_S = 300  # keep ordinary device pieces and <=5-minute imports on their existing read path
COPY_BYTES = 1024 * 1024
DECODE_IDLE_S = 300  # no PCM progress, not a limit on the recording's duration
DECODE_ATTEMPTS = 3


def decode_process(args, output):
    """Own the PCM writes so storage failures have a real OSError errno.
    Supervise progress instead of imposing a total runtime on long recordings.
    """
    async def run():
        async with await anyio.open_process(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            try:
                while True:
                    try:
                        with anyio.fail_after(DECODE_IDLE_S):
                            data = await process.stdout.receive(COPY_BYTES)
                    except anyio.EndOfStream:
                        break
                    output.write(data)
                with anyio.fail_after(DECODE_IDLE_S):
                    code = await process.wait()
                output.flush()
            except BaseException as exc:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                if isinstance(exc, TimeoutError):
                    raise subprocess.TimeoutExpired(args, DECODE_IDLE_S) from None
                raise
        if code < 0:
            raise RuntimeError(f"audio decoder terminated by signal {-code}")
        return code
    # This API is synchronous (also used by offline readers); in the HTTP path
    # its caller already runs in the bounded media pool, away from the event loop.
    return anyio.run(run)


@contextlib.contextmanager
def decoded_file(source, aac=False, n=None, prev=None):
    """Seekable upload -> disk PCM, with the same priming and AAC trimming as decode().
    stdout/stderr never accumulate in RAM, even for a small compressed file lasting hours.
    """
    with tempfile.NamedTemporaryFile() as pcm, tempfile.NamedTemporaryFile() as priming:
        source.seek(0)
        if prev is not None:
            body = source.read(COPY_BYTES + 1)
            source.seek(0)
            if len(body) <= COPY_BYTES:
                priming.write(primed(prev, body))
                priming.flush()
                source = priming
        source.seek(0)
        mov = source.read(8)[4:8] == b"ftyp"
        source.seek(0)
        args = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", *(["-f", "mov"] if mov else []),
                "-i", source.name, "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"]
        code = decode_process(args, pcm)
        count = os.fstat(pcm.fileno()).st_size // 2
        if code or not count:
            yield None
            return
        lo = AAC_DELAY if aac and (n or count > AAC_DELAY + AAC_FRAME) else 0
        hi = min(count, AAC_DELAY + n) if aac and n else count - AAC_FRAME // 2 if aac and lo else count
        pcm.seek(lo * 2)
        yield pcm, max(0, hi - lo)


def pcm_blocks(pcm, count):
    """Owned arrays: no view can retain the rest of a file behind a short tail."""
    while count > 0:
        x = np.frombuffer(pcm.read(min(count, BLOCK_S * SR) * 2), np.int16)
        if not len(x):
            raise OSError("decoded audio ended early")
        count -= len(x)
        yield x


def file_health(pcm, count):
    """health() in bounded blocks, carrying zero runs over block boundaries."""
    energy = hum = full = zeros = run = 0
    for x in pcm_blocks(pcm, count):
        f = x.astype(np.float64)
        energy += np.dot(f, f)
        hum += sum((2 * np.abs(f[i:i + SR] @ HUM[:len(f[i:i + SR])]) ** 2).sum() / len(f[i:i + SR])
                   for i in range(0, len(f), SR))
        full += int(np.count_nonzero((x >= 32767) | (x <= -32767)))
        if x[0] != 0:
            zeros += run if run >= ZERO_RUN else 0
            run = 0
        edges = np.flatnonzero(np.diff(np.r_[0, x == 0, 0]))
        carried, run = run, 0
        for a, b in zip(edges[::2], edges[1::2]):
            length = b - a + (carried if a == 0 else 0)
            if b == len(x):
                run = length
            elif length >= ZERO_RUN:
                zeros += length
    zeros += run if run >= ZERO_RUN else 0
    dbfs = lambda ms: round(10 * np.log10(max(ms, 1e-3) / 32768 ** 2), 1)
    return dbfs(energy / max(count, 1)), dbfs(hum / max(count, 1)), zeros * 1000 // SR, full


def save_file(device, start_ms, source, content_type, n=None):
    """HTTP upload already spooled to disk; retain the original bytes and one pieces row.
    A failed decode publishes no row, so retrying never exposes a partial import to the pipeline.
    """
    size = os.fstat(source.fileno()).st_size
    kept = lambda: db.conn().execute("SELECT seconds FROM pieces WHERE device = ? AND start_ms = ? AND size >= ?",
                                     (device, start_ms, size)).fetchone()
    if r := kept():
        return r[0]
    ext = {"audio/ogg": "ogg", "audio/webm": "webm", "audio/aac": "aac"}.get(content_type.split(";")[0], "bin")
    prev = before(device, start_ms, COPY_BYTES) if ext == "ogg" and size <= COPY_BYTES else None
    with decoded_file(source, ext == "aac", n, prev) as decoded:
        if decoded is None:
            path = T().recordings / "rejected" / re.sub(r"[^\w.-]", "_", device) / f"{start_ms}.{ext}"
            path.parent.mkdir(parents=True, exist_ok=True)
            source.seek(0)
            with path.open("wb") as out:
                shutil.copyfileobj(source, out, COPY_BYTES)
            return None
        pcm, count = decoded
        h = file_health(pcm, count)
    path = hour_file(device, start_ms, ext)
    with _append:
        if r := kept():
            return r[0]
        dest = T().recordings / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        source.seek(0)
        with dest.open("ab") as out:
            pos = out.tell()
            shutil.copyfileobj(source, out, COPY_BYTES)
            out.flush()
            os.fsync(out.fileno())
        directory = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        db.conn().execute("INSERT OR REPLACE INTO pieces (device, start_ms, seconds, path, pos, size, n, received_ms, rms, hum, zero_ms, full) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (device, start_ms, round(count / SR, 3), str(path), pos, size, n, int(time.time() * 1000), *h))
        with _holds:
            held = db.kv(f"hold:{device}")
            if held and start_ms < held["at"] and not held.get("released"):
                held["progress"] = time.time()
                db.put_kv(f"hold:{device}", held)
    return count / SR


@contextlib.contextmanager
def piece_file(p):
    """Isolate one indexed piece from its append-only hour file, in bounded copies."""
    with tempfile.NamedTemporaryFile() as source:
        with open(T().recordings / p["path"], "rb") as stored:
            stored.seek(p["pos"])
            left = p["size"]
            while left:
                data = stored.read(min(left, COPY_BYTES))
                if not data:
                    raise OSError("stored audio ended early")
                source.write(data)
                left -= len(data)
        source.flush()
        yield source


@contextlib.contextmanager
def piece_blocks(p, end):
    """Long stored files decoded on disk, then read at the consumer's pace (including its backpressure)."""
    with piece_file(p) as source:
        with decoded_file(source, p["path"].endswith(".aac"), p["n"]) as decoded:
            if decoded is None:
                raise ValueError("stored audio could not be decoded")
            pcm, count = decoded
            skip = max(0, round((end - p["start_ms"]) * SR / 1000))
            pcm.seek(skip * 2, os.SEEK_CUR)
            yield pcm_blocks(pcm, max(0, count - skip)), skip


def piece_window(p, offset, count):
    """Only decode the requested samples of a long import, with 2 s codec preroll.
    Short device pieces retain samples()'s exact Opus priming/dehum/AAC behavior.
    """
    if p["seconds"] <= WHOLE_S:
        x = samples(p)
        return None if x is None else x[offset:offset + count]
    if p["path"].endswith(".aac"):
        offset += AAC_DELAY
    seek = max(0, offset // SR - 2)
    with piece_file(p) as source, tempfile.TemporaryFile() as pcm:
        source.seek(0)
        header = source.read(512)
        # Ogg's first packet identifies the codec, even for an upload stored as
        # .bin. ffmpeg's FLAC-in-Ogg input seek can misalign data and timestamps.
        if header.startswith(b"OggS") and len(header) >= 27 and header[27 + header[26]:].startswith(b"\x7fFLAC"):
            with decoded_file(source) as decoded:
                if decoded is None:
                    return None
                decoded_pcm, _ = decoded
                decoded_pcm.seek(offset * 2, os.SEEK_CUR)
                return np.frombuffer(decoded_pcm.read(count * 2), np.int16)
        mov = header[4:8] == b"ftyp"
        args = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", *(["-ss", str(seek)] if seek else []),
                *(["-f", "mov"] if mov else []), "-i", source.name,
                "-af", f"aresample={SR}:first_pts=0,atrim=start_sample={offset - seek * SR},asetpts=PTS-STARTPTS",
                "-t", str(count / SR),
                "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"]
        if decode_process(args, pcm):
            return None
        pcm.seek(0)
        return np.frombuffer(pcm.read(count * 2), np.int16)


def read(p) -> bytes:
    """A pieces row's bytes, as uploaded."""
    with open(T().recordings / p["path"], "rb") as f:
        f.seek(p["pos"])
        return f.read(p["size"])


def samples(p) -> np.ndarray | None:
    """A pieces row's audio, decoded, a recorder's going on from the piece before it (decode, primed) and without its
    board's 10 ms hum (dehum). Every reader takes pieces from here (the pipeline, playback, listening); the levels
    stored at upload (save) stay those of the piece as it came."""
    body, ogg = read(p), p["path"].endswith(".ogg")
    x = decode(body, aac=p["path"].endswith(".aac"), n=p["n"], prev=before(p["device"], p["start_ms"]) if ogg else None)
    if x is not None and DEHUM and ogg and celt((packets(body)[2:] or [b""])[0]) == 10:
        return dehum(x)
    return x


def fresh(p, end: float) -> tuple[float, np.ndarray] | None:
    """A pieces row's audio after `end` (unix ms: where its device's audio a reader has taken so far ends) and where
    that starts (unix ms); None when the piece adds nothing (all of it before `end`: not decoded; not audio). Pieces
    of a device may overlap (10-12: 859 of 3184 of LR-P3-BDAE's, by 22 s median, up to 60 s): a reader taking
    them in start order whole went back in time at the second copy and heard what was said there twice."""
    if p["start_ms"] + p["seconds"] * 1000 <= end:
        return None
    x = samples(p)
    k = max(0, round((end - p["start_ms"]) * SR / 1000))
    return None if x is None or k >= len(x) else (p["start_ms"] + k * 1000 / SR, x[k:])


def covered(device: str, ms: int) -> float:
    """Where the device's pieces starting at or before ms end (unix ms; each piece is far shorter than an hour): what a
    reader that has had them has heard."""
    return db.conn().execute("SELECT max(start_ms + seconds * 1000) FROM pieces WHERE device = ? AND start_ms <= ? AND start_ms > ?",
                             (device, ms, ms - 3600_000)).fetchone()[0] or 0.0


def once(rows, end: float) -> tuple[int, float]:
    """Of (start_ms, seconds) rows in start order, how many add audio after `end` (unix ms) and how many seconds they
    add: overlapping pieces counted once, as fresh() hands them out."""
    n, s = 0, 0.0
    for a, sec in rows:
        if (b := a + sec * 1000) > end:
            n, s, end = n + 1, s + (b - max(a, end)) / 1000, b
    return n, s


def queued(device: str) -> int:
    """Pieces of a device its stream has not handed out yet (and will: one another piece covers is not)."""
    at = T().read_to.get(device, -1)
    return once(db.conn().execute("SELECT start_ms, seconds FROM pieces WHERE device = ? AND start_ms > ? ORDER BY start_ms", (device, at)),
                covered(device, at))[0]


def backlog(device: str, after: dt.datetime | None) -> tuple[float, dt.datetime | None]:
    """Seconds of a device's stored audio after `after` (its finalized time), overlaps once, and where its stored
    audio ends."""
    ms = int(after.timestamp() * 1000) if after else 0
    _, s = once(db.conn().execute("SELECT start_ms, seconds FROM pieces WHERE device = ? AND start_ms + seconds * 1000 > ? ORDER BY start_ms",
                                  (device, ms)), ms)
    last = db.conn().execute("SELECT max(start_ms + seconds * 1000) FROM pieces WHERE device = ?", (device,)).fetchone()[0]
    return round(s, 1), dt.datetime.fromtimestamp(last / 1000) if last else None


def minutes(device: str, lo_ms: int, hi_ms: int) -> list[dict]:
    """How a device's audio starting in [lo_ms, hi_ms) sounded and arrived, a row per minute it has audio in (pieces
    from before 10-07 have neither): its seconds; RMS and hum in dBFS (energy-weighted); s in zero runs and full-scale
    samples (audio.health); lag_s: how long after its end the minute's median piece arrived."""
    out = {}
    for p in db.conn().execute("SELECT * FROM pieces WHERE device = ? AND start_ms >= ? AND start_ms < ? AND rms IS NOT NULL ORDER BY start_ms",
                               (device, lo_ms, hi_ms)):
        m = out.setdefault(p["start_ms"] // 60_000, {"s": 0.0, "rms": 0.0, "hum": 0.0, "zero_s": 0.0, "full": 0, "lag": []})
        m["s"] += p["seconds"]
        m["rms"] += 10 ** (p["rms"] / 10) * p["seconds"]
        m["hum"] += 10 ** (p["hum"] / 10) * p["seconds"]
        m["zero_s"] += p["zero_ms"] / 1000
        m["full"] += p["full"]
        if p["received_ms"]:
            m["lag"].append((p["received_ms"] - p["start_ms"]) / 1000 - p["seconds"])
    dbfs = lambda e, s: round(10 * np.log10(max(e / s, 1e-12)), 1) if s else None
    return [{"t": dt.datetime.fromtimestamp(k * 60).isoformat(timespec="seconds"), "s": round(m["s"], 1), "rms": dbfs(m["rms"], m["s"]),
             "hum": dbfs(m["hum"], m["s"]), "zero_s": round(m["zero_s"], 2), "full": m["full"],
             "lag_s": round(sorted(m["lag"])[len(m["lag"]) // 2], 1) if m["lag"] else None} for k, m in out.items()]


def arrival(device: str) -> dict | None:
    """The device's newest audio: when it came (unix ms) and how long after its end (s): what is said now, how late."""
    p = db.conn().execute("SELECT * FROM pieces WHERE device = ? ORDER BY start_ms DESC LIMIT 1", (device,)).fetchone()
    return p and p["received_ms"] and {"at": p["received_ms"], "lag_s": round((p["received_ms"] - p["start_ms"]) / 1000 - p["seconds"], 1)}


def devices() -> list[str]:
    """Every device with pieces: an index lookup each (SELECT DISTINCT read the whole index, 89 ms at 1.3 M pieces)."""
    return [r[0] for r in db.conn().execute(
        "WITH RECURSIVE d(x) AS (SELECT min(device) FROM pieces UNION ALL SELECT (SELECT min(device) FROM pieces WHERE device > x) FROM d "
        "WHERE x IS NOT NULL) SELECT x FROM d WHERE x IS NOT NULL")]


def pending(running: set[str]) -> list[str]:
    """Devices, of those whose pipeline is not `running`, with audio after their finalized time (none yet: any audio).
    Unbounded per device (an imported file is one piece, hours long): its caller runs it in a thread."""
    out = []
    for d in devices():
        if d not in running:
            v = db.kv(f"finalized_to:{d}")
            end = db.conn().execute("SELECT max(start_ms + seconds * 1000) FROM pieces WHERE device = ?", (d,)).fetchone()[0]
            if end > (int(dt.datetime.fromisoformat(v).timestamp() * 1000) if v else 0):
                out.append(d)
    return out


def hold(device: str, at_ms: int | None):
    """A phone still has audio of `device` older than at_ms to send (a backlog it reads off the recorder's card while it
    sends what is said now): the device's queue (stream without edge_s) hands out nothing from at_ms on until
    that audio is in, so none of it arrives behind the reader. None: nothing older is coming. Every upload says it
    again; kept across a restart."""
    with _holds:
        h = db.kv(f"hold:{device}")
        now = time.time()
        if at_ms is None:
            h = None
        elif h and h["at"] == at_ms:
            h.setdefault("since", h["seen"])  # a legacy hold's earliest known contact, before seen is refreshed
            if now - h["seen"] > 5 * 60:
                h["progress"] = now  # reconnect: live pieces upload first, so give the backlog a fresh grace period
            h.setdefault("progress", now)  # a pre-timeout store gets one full grace period after upgrade
            h["seen"] = now  # repeating a hold does not mean older audio made progress, or rearm an expired hold
        else:
            h = {"at": at_ms, "since": now, "seen": now, "progress": now}
        db.put_kv(f"hold:{device}", h)


def held(device: str) -> int | None:
    """Where finals wait (unix ms). The no-progress clock advances only as the phone repeats the hold.
    New older audio or a reconnect renews stale grace, within HOLD_MAX_S from this boundary's first set.
    Expiry is persisted: repeated uploads and restarts cannot rearm the same hold or repeat its warning."""
    with _holds:
        h = db.kv(f"hold:{device}")
        if not h or h.get("released"):
            return None
        if "since" not in h:
            h["since"] = h["seen"]
            db.put_kv(f"hold:{device}", h)
        if time.time() - h["since"] >= HOLD_MAX_S:
            h["released"] = "max"
            db.put_kv(f"hold:{device}", h)
            return None
        done = db.kv(f"finalized_to:{device}")
        if done and int(dt.datetime.fromisoformat(done).timestamp() * 1000) >= h["at"]:
            return None  # already passed: late pieces cannot be put back in front of the reader
        if "progress" not in h:
            h["progress"] = time.time()
            db.put_kv(f"hold:{device}", h)
        if h["seen"] - h["progress"] < HOLD_STALE_S:
            return h["at"]
        h.update(released="stale", stale_s=HOLD_STALE_S)
        db.put_kv(f"hold:{device}", h)
    text = f"{device}：补传等了 {HOLD_STALE_S / 60:g} 分钟没有进展，先定稿；之后到的更早录音只保存不转写"
    log.warning("%s", text)
    # monitor.status runs in an AnyIO worker; ws.note schedules its broadcast on the tenant's event loop.
    if T().tg:
        try:
            anyio.get_current_task()
        except RuntimeError:
            anyio.from_thread.run_sync(ws.note, text, "warn")
        else:
            ws.note(text, "warn")
    else:
        ws.note(text, "warn")
    return None


def hold_expired(device: str) -> dict | None:
    """Why the queue stopped waiting, retained until the phone releases or replaces that hold."""
    h = db.kv(f"hold:{device}")
    return {"reason": "stale", "seconds": h["stale_s"]} if h and h.get("released") == "stale" else None


def hold_seen(device: str) -> float | None:
    """When a phone last said the device's hold (unix s)."""
    h = db.kv(f"hold:{device}")
    return h and h["seen"]


def hold_until(device: str) -> float | None:
    """The hold's total-life deadline (unix s), unchanged by reconnects or older-piece arrivals."""
    h = db.kv(f"hold:{device}")
    return h.get("since", h["seen"]) + HOLD_MAX_S if h else None


async def stream(device: str, after: dt.datetime | None, edge_s: float | None = None, spent: dict | None = None):
    """(start time, samples) of one device in time order, from the piece holding `after` (its first piece when None),
    forever, each moment once (fresh). A piece arriving older than one already handed out is kept for playback but not
    streamed: a phone that sends what is said now ahead of older audio holds the queue (hold()) until the older audio
    is in. edge_s: only the device's newest edge_s seconds (before its newest piece's end or the clock, whichever is
    later), for a reader of what is said now: older pieces, a backlog arriving, are skipped; such a reader is not the
    device's queue (read_to, queued). spent: the ms its pieces took to decode (decode_ms) and the seconds it handed out
    (fed_s) added to it (perf.piece)."""
    ms = int(after.timestamp() * 1000) if after else 0
    p = db.conn().execute("SELECT * FROM pieces WHERE device = ? AND start_ms + seconds * 1000 > ? ORDER BY start_ms LIMIT 1",
                          (device, ms)).fetchone() if after else None  # None: from 0 all the same, and pieces_end would sort every piece
    # from `after` (or the piece holding it), not from the first piece there is now: that one may be held, older
    # audio after `after` still on its way (a pipeline started mid catch-up took 3 such pieces as late)
    at, lo, end = min(p["start_ms"] - 1, ms) if p else ms, 0, 0.0
    decode_failures = 0
    while True:
        if edge_s is None:
            T().read_to[device] = at  # the reader came back for more, so the piece it had is done
        else:
            last = db.conn().execute("SELECT start_ms + seconds * 1000 FROM pieces WHERE device = ? ORDER BY start_ms DESC LIMIT 1", (device,)).fetchone()
            lo = max(last[0] if last else 0, time.time() * 1000) - edge_s * 1000
        # the edge reader by the end (pieces_end: its last edge_s); by start (pieces_device), an idle device's reader
        # (at 0: nothing handed out yet) walked all its pieces each second
        p = db.conn().execute(f"SELECT * FROM pieces{' INDEXED BY pieces_end' if edge_s else ''} WHERE device = ? AND start_ms > ? "
                              "AND start_ms + seconds * 1000 > ? ORDER BY start_ms LIMIT 1", (device, at, lo)).fetchone()
        if p is not None and edge_s is None and (h := held(device)) is not None and p["start_ms"] >= h:
            p = None  # older audio is still on its way: it goes first
        if p is None:
            await anyio.sleep(1)
            continue
        if p["seconds"] > WHOLE_S:
            # Keep both decode and each read off the loop. The context owns temporary files;
            # cancellation/close releases them even while the consumer is waiting on ASR.
            with contextlib.ExitStack() as stack:
                with perf.timed(spent, "decode_ms"):
                    try:
                        blocks, skip = await anyio.to_thread.run_sync(
                            lambda: stack.enter_context(piece_blocks(p, max(end, ms, lo))), limiter=threads.MEDIA)
                    except subprocess.TimeoutExpired:
                        decode_failures += 1
                        if decode_failures >= DECODE_ATTEMPTS:
                            ws.note(f"{device}：音频解码连续 {DECODE_ATTEMPTS} 次无进展，已暂停；请检查文件后重启处理任务", "error")
                            # Keep the piece queued, without skipping audio or letting
                            # lasting() restart the same failing decode forever.
                            await anyio.sleep_forever()
                        ws.note(f"{device}：音频解码无进展，60 s 后重试（{decode_failures}/{DECODE_ATTEMPTS}）", "warn")
                        await anyio.sleep(60)
                        continue
                    decode_failures = 0
                for_block = lambda: next(blocks, None)
                while (x := await anyio.to_thread.run_sync(for_block, limiter=threads.MEDIA)) is not None:
                    s = p["start_ms"] + skip * 1000 / SR
                    skip += len(x)
                    end = p["start_ms"] + skip * 1000 / SR
                    perf.add(spent, fed_s=len(x) / SR)
                    yield dt.datetime.fromtimestamp(s / 1000), x
            at = p["start_ms"]
            continue
        with perf.timed(spent, "decode_ms"):
            got = await anyio.to_thread.run_sync(fresh, p, end, limiter=threads.MEDIA)
        at = p["start_ms"]
        if got is not None:
            s, x = got
            end = s + len(x) * 1000 / SR
            perf.add(spent, fed_s=len(x) / SR)
            yield dt.datetime.fromtimestamp(s / 1000), x


def cut(a: dt.datetime, b: dt.datetime, fmt: str, device: str | None = None) -> bytes | None:
    """[a, b] (+0.2 s each side) of one device (any when None) as `fmt` (wav, s16le), or None when no stored audio
    covers it."""
    a_ms, b_ms = int(a.timestamp() * 1000), int(b.timestamp() * 1000)
    pieces = db.conn().execute(
        "SELECT * FROM pieces WHERE start_ms <= ? AND start_ms + seconds * 1000 >= ? AND (? IS NULL OR device = ?) ORDER BY start_ms",
        (b_ms, a_ms, device, device)).fetchall()
    xs, end, drop = [], {}, None
    need = int(((b - a).total_seconds() + 0.4) * SR)
    for p in pieces:
        previous = end.get(p["device"], 0.0)
        if p["start_ms"] + p["seconds"] * 1000 <= previous:
            continue
        skip = max(0, round((previous - p["start_ms"]) * SR / 1000))
        if p["seconds"] <= WHOLE_S:
            got = fresh(p, previous)
            if got is None:
                continue
            start, x = got
            count = len(x)
        else:
            start = p["start_ms"] + skip * 1000 / SR
            count = max(0, round(p["seconds"] * SR) - skip)
            x = None
        end[p["device"]] = start + count * 1000 / SR
        if drop is None:
            drop = int(max(0.0, (a_ms - start) / 1000 - 0.2) * SR)
        if x is not None and drop >= count:
            drop -= count
            continue
        # pieces.seconds is rounded to milliseconds; let EOF, rather than that
        # metadata, decide the final sample of a long file.
        take = min(need, count - drop) if x is not None else need
        windowed = x is None
        x = x[drop:drop + take] if x is not None else piece_window(p, skip + drop, take)
        if x is not None and len(x):
            if windowed:
                end[p["device"]] = start + (drop + len(x)) * 1000 / SR
            xs.append(x)
            need -= len(x)
        drop = 0
        if need <= 0:
            break
    if not xs:
        return None
    # concatenated back to back (the small gaps between pieces are ignored)
    x = np.concatenate(xs)
    if not len(x):
        return None
    return x.tobytes() if fmt == "s16le" else wav(x)


def wav(x: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(x.tobytes())
    return out.getvalue()


LISTEN_BACK_S = 30  # a listener asking from further back than this before a device's newest audio skips ahead


def newer(device: str, after: int | None) -> tuple[int, int, np.ndarray] | None:
    """For listening in on a device: its pieces starting after `after` (unix ms; None: its newest piece only), within
    LISTEN_BACK_S of its newest audio (a page back from the background, a backlog arriving), back to back; with the
    first one's start and the last one's (the next `after`); each moment once (fresh), also across calls: what the
    pieces up to `after` covered is not sent again. None when there is none."""
    last = db.conn().execute("SELECT start_ms, seconds FROM pieces WHERE device = ? ORDER BY start_ms DESC LIMIT 1", (device,)).fetchone()
    if last is None:
        return None
    lo = last["start_ms"] + last["seconds"] * 1000 - LISTEN_BACK_S * 1000
    ps = db.conn().execute("SELECT * FROM pieces WHERE device = ? AND start_ms > ? AND start_ms + seconds * 1000 > ? ORDER BY start_ms",
                           (device, last["start_ms"] - 1 if after is None else after, lo)).fetchall()
    end, xs = covered(device, after) if after is not None else 0.0, []
    for p in ps:
        if got := fresh(p, end):
            end = got[0] + len(got[1]) * 1000 / SR
            xs.append(got)
    return (round(xs[0][0]), ps[-1]["start_ms"], np.concatenate([x for _, x in xs])) if xs else None


if __name__ == "__main__":  # the stream's order, resume point and late pieces, on synthetic tones
    DIR = Path(tempfile.mkdtemp())
    db.open_db(DIR / "t.db")
    tone = lambda s: subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:d={s}", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                                    capture_output=True, check=True).stdout
    M = lambda m: int(dt.datetime(2026, 10, 2, 10, m).timestamp() * 1000)
    for m in (0, 1, 5):  # 10:00, 10:01, a gap, 10:05
        assert save("phone", M(m), tone(60), "audio/ogg") > 59
    assert save("phone", M(9), b"not audio", "audio/ogg") is None
    assert (DIR / f"rejected/phone/{M(9)}.ogg").read_bytes() == b"not audio", "an undecodable piece's bytes are kept"
    assert 0 < save("phone", M(8), tone(0.02), "audio/ogg") < 0.1, "a 20 ms piece is audio too"
    db.conn().execute("DELETE FROM pieces WHERE start_ms = ?", (M(8),))
    files = [p.relative_to(DIR).as_posix() for p in (DIR / "chunks").rglob("*.ogg")]
    assert files == ["chunks/phone/2026-10-02/10.ogg"], files
    # how pieces sound (no content): ffmpeg's sine is at 1/8 of full scale (-21 dBFS RMS); 50 Hz is hum, 440 Hz is not
    i = np.arange(2 * SR)
    hum = (0.25 * 32767 * np.sin(2 * np.pi * 50 * i / SR)).astype(np.int16)
    assert health(hum)[:2] == (-15.1, -15.1), health(hum)
    assert health((0.25 * 32767 * np.sin(2 * np.pi * 440 * i / SR)).astype(np.int16))[1] < -60, "440 Hz is not hum"
    broken = hum.copy()
    broken[1000:1800], broken[5000:5100], broken[9000:9010], broken[9500] = 0, 0, 32767, -32768
    assert health(broken)[2:] == (50, 11), "50 ms of zero runs >= 10 ms (the 6 ms one does not count), 11 full-scale samples"
    assert health(np.zeros(1, np.int16)) == (-120.3, -120.3, 0, 0)
    p = db.conn().execute("SELECT * FROM pieces WHERE start_ms = ?", (M(0),)).fetchone()
    assert abs(p["rms"] + 21.1) < 0.3 and p["hum"] < -60 and p["zero_ms"] == p["full"] == 0, dict(p)
    assert 0 <= time.time() * 1000 - p["received_ms"] < 60_000, "when it came"
    m = minutes("phone", M(0), M(2))
    assert [r["t"][11:16] for r in m] == ["10:00", "10:01"] and m[0]["s"] > 59 and abs(m[0]["rms"] + 21.1) < 0.3, m
    assert m[0]["lag_s"] > 3600, "arrived long after (this test runs after 2026-10-02 10:01)"
    db.conn().execute("UPDATE pieces SET rms = -40, hum = -40, zero_ms = 1500, full = 3, received_ms = start_ms + 61000 WHERE start_ms = ?", (M(1),))
    assert minutes("phone", M(1), M(2))[0] | {"t": 0, "s": 0} == {"t": 0, "s": 0, "rms": -40.0, "hum": -40.0, "zero_s": 1.5, "full": 3, "lag_s": 1.0}
    assert arrival("phone")["lag_s"] > 3600 and arrival("nobody") is None
    x = np.arange(16000, dtype=np.int16)
    assert len(unpad(x)) == 16000 - AAC_DELAY - AAC_FRAME // 2 and unpad(x)[0] == AAC_DELAY, "AAC: the encoder's delay and half a frame go"
    assert len(unpad(x[:3000])) == 3000, "too short to hold both: left alone"
    # an iPhone piece of n = 16333 samples (not whole frames). Apple's encoder puts 2112 samples before them; ffmpeg's
    # puts 1024, so 1088 of silence in front stand in for the rest (aac_at, Apple's own, exists only on a Mac)
    aac = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:r=16000", "-af", "atrim=end_sample=16333,adelay=1088S",
                          "-c:a", "aac", "-b:a", "24k", "-f", "adts", "pipe:1"], capture_output=True, check=True).stdout
    assert len(decode(aac)) == 19 * AAC_FRAME, len(decode(aac))  # 2112 + 16333, padded to whole frames
    assert save("iphone", M(20), aac, "audio/aac", n=16333) == 16333 / SR
    assert len(samples(db.conn().execute("SELECT * FROM pieces WHERE start_ms = ?", (M(20),)).fetchone())) == 16333
    assert len(decode(aac, aac=True)) == len(decode(aac)) - AAC_DELAY - AAC_FRAME // 2, "no n: unpadded as before"

    async def take(after, n, device="phone"):
        out, s = [], stream(device, after)
        async for t, x in s:
            out.append(t.minute)
            if len(out) == n:
                break
        await s.aclose()
        return out
    assert anyio.run(take, None, 3) == [0, 1, 5]
    assert anyio.run(take, dt.datetime(2026, 10, 2, 10, 1, 30), 2) == [1, 5], "resumes from the piece holding `after`"
    assert queued("phone") == 1, "the last piece handed out counts until the reader comes back"
    assert save("iphone", M(2), tone(30), "audio/aac") > 29  # another device, inside phone's gap
    assert anyio.run(take, None, 1, "iphone") == [2], "each device streams on its own"
    db.put_kv("finalized_to:phone", "2026-10-02T10:06:00")
    assert devices() == ["iphone", "phone"] and pending(set()) == ["iphone"], "phone done, iphone has none finalized"
    assert pending({"iphone"}) == [], "a device whose pipeline runs is not asked about"
    db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path) VALUES ('import', ?, 10800, 'x')", (M(0),))  # one 3 h file
    db.put_kv("finalized_to:import", "2026-10-02T11:30:00")  # a restart 1.5 h into it: the rest is still to do
    assert pending({"iphone"}) == ["import"], "a piece hours long counts to its end"
    db.conn().execute("DELETE FROM pieces WHERE device = 'import'")
    assert cut(dt.datetime(2026, 10, 2, 10, 2, 5), dt.datetime(2026, 10, 2, 10, 2, 6), "s16le", "phone") is None
    left, end = backlog("phone", dt.datetime(2026, 10, 2, 10, 1, 30))
    assert 89 < left < 91 and end == dt.datetime(2026, 10, 2, 10, 6), (left, end)  # 30 s of 10:01 + all of 10:05
    assert len(cut(dt.datetime(2026, 10, 2, 10, 0, 59), dt.datetime(2026, 10, 2, 10, 1, 1), "s16le")) > 2 * SR * 2
    with wave.open(io.BytesIO(cut(dt.datetime(2026, 10, 2, 10, 0, 59), dt.datetime(2026, 10, 2, 10, 1, 1), "wav", "phone"))) as w:
        assert w.getframerate() == SR and abs(w.getnframes() - 2.4 * SR) < 2, w.getnframes()

    async def pieces(after, got):  # the stream's pieces into got, one at a time as it hands them out
        s = stream("phone", after)
        try:
            async for t, x in s:
                await got.send(t)
        finally:
            await s.aclose()

    async def next_in(got, s: float):  # the next piece within s seconds, None when the stream holds it back
        with anyio.move_on_after(s):
            return (await got.receive()).minute

    async def held_back():  # 10:05 came first, the phone still has older audio: 10:03 is to go before it
        send, got = anyio.create_memory_object_stream()
        db.put_kv("finalized_to:phone", None)  # the earlier pending() check's completed recording is not this reader
        hold("phone", M(5))
        with send, got:
            async with anyio.create_task_group() as tg:
                tg.start_soon(pieces, None, send)
                out = [await next_in(got, 5) for _ in range(2)]
                assert out == [0, 1] and await next_in(got, 2.5) is None, "10:05 waits"
                assert save("phone", M(3), tone(60), "audio/ogg") > 59
                out.append(await next_in(got, 5))
                assert out == [0, 1, 3] and await next_in(got, 2.5) is None, "still held"
                hold("phone", None)
                out.append(await next_in(got, 5))
                assert out == [0, 1, 3, 5], out
                tg.cancel_scope.cancel()
    anyio.run(held_back)
    hold("phone", M(5))
    assert held("phone") == M(5) and anyio.run(take, dt.datetime(2026, 10, 2, 10, 4), 1, "iphone") == [20], "a hold is the device's own"

    async def started_held():  # the reader starts while all there is after `after` is held: older audio still goes first
        send, got = anyio.create_memory_object_stream()
        with send, got:
            async with anyio.create_task_group() as tg:
                tg.start_soon(pieces, dt.datetime(2026, 10, 2, 10, 4), send)
                assert await next_in(got, 1.5) is None, "10:05 is held"
                assert save("phone", M(4) + 30_000, tone(20), "audio/ogg") > 19  # 10:04:30, after `after`, before 10:05
                t = await got.receive()
                tg.cancel_scope.cancel()
        return t
    assert anyio.run(started_held) == dt.datetime(2026, 10, 2, 10, 4, 30), "not passed over for the held piece"
    db.put_kv("hold:phone", {"at": M(5), "seen": time.time() - HOLD_MAX_S - 1})
    assert held("phone") is None, "a hold not said again for HOLD_MAX_S is let go"
    assert anyio.run(take, dt.datetime(2026, 10, 2, 10, 4), 2) == [4, 5]
    print("ok")
