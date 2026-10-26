"""What the apps send and ask: the audio pieces, a recording's end, a hold let go, what they read off the recorders
(status, event logs), the calls and firmware updates, and the settings they start with."""
import datetime as dt
import json
import logging
import os
import time

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from alldayrec import audio
from alldayrec import db
from alldayrec import perf
from alldayrec import threads
from alldayrec import ws
from alldayrec.core import iso
from alldayrec.tenant import T

log = logging.getLogger("live")
router = APIRouter()


SLICE_FOREGROUND_S = int(os.environ.get("SLICE_FOREGROUND_S", "1"))   # the apps' piece seconds with the app in front: 1 s
SLICE_BACKGROUND_S = int(os.environ.get("SLICE_BACKGROUND_S", "20"))  # ... in the background             (drafts ~8 s vs ~13 s at 5 s)
SLICE_SCREEN_OFF_S = int(os.environ.get("SLICE_SCREEN_OFF_S", "60"))  # ... screen off (Android)
SLICE_WATCHED_S = int(os.environ.get("SLICE_WATCHED_S", "2"))  # ... at most this in both while /live is open


def slices() -> dict:
    """The piece lengths the apps cut now, per app state. While the tenant's live page is open (ws: its watch says
    live; 今天's live card does not), the background and screen-off ones are at most SLICE_WATCHED_S, so its captions
    come within seconds also from a phone in a pocket; they are back to theirs within a minute of the page leaving."""
    cap = (lambda s: min(s, SLICE_WATCHED_S)) if time.time() < T().live_until else (lambda s: s)
    return {"foreground": SLICE_FOREGROUND_S, "background": cap(SLICE_BACKGROUND_S), "screenOff": cap(SLICE_SCREEN_OFF_S)}


@router.post("/api/chunks")
async def chunk(request: Request, device: str, start: int, n: int | None = None, hold: int | None = None):
    """One self-contained audio file (the apps' Opus or AAC pieces), `start` its device time in unix ms, `n` the 16 kHz
    samples the client wrote into it (the iPhone's AAC: the decoder adds some at both ends). Kept on disk (also
    for playback) and queued; the device's pipeline takes its pieces in time order at the pace finalization keeps up
    with, so a client back from an outage sends its whole backlog at once, whatever other devices are doing. `hold`
    (unix ms): the client still has older audio of this device than that to send (it sends what is said now while it
    reads a backlog off the recorder's card): finalization waits there for it, drafts go on (audio.hold); an upload
    without it lets go. The answer has the piece's seconds and the piece lengths now (`slice`, slices()): the apps'
    native uploaders take them from here, as Dart does not run in the background to ask /api/client-config."""
    audio.hold(device, hold)  # before the piece is in: the queue must not hand it out ahead of the older audio
    body, kind = await request.body(), request.headers.get("content-type", "")
    seconds = await anyio.to_thread.run_sync(audio.save, device, start, body, kind, n, limiter=threads.MEDIA)
    if seconds is None:  # the app drops it on a 400 (audio.save keeps its bytes in rejected/): say so, it is lost audio
        log.warning("%s: piece at %s (%s, %d bytes) could not be decoded", device, dt.datetime.fromtimestamp(start / 1000), kind, len(body))
        ws.note(f"{device} 的一块音频（起于 {dt.datetime.fromtimestamp(start / 1000).strftime('%H:%M:%S')}）无法解码，客户端收到 400 会丢弃它；原始字节留在 recordings/rejected/", "error")
        raise HTTPException(400, "could not decode")
    T().chunks = {"device": device, "at": time.time()}
    late = "（早于这台设备已处理的位置，只存不转写）" if start <= T().read_to.get(device, -1) else ""
    ws.note(f"收到 {device} 的块 {seconds:.1f} s，起于 {dt.datetime.fromtimestamp(start / 1000).strftime('%H:%M:%S')}{late}", "warn" if late else "info")
    return {"seconds": round(seconds, 2), "slice": slices()}


@router.post("/api/chunks/end")
async def chunk_end(device: str, t: int):
    """The recorder stopped at device time t (unix ms): the open conversation closes once its audio is processed."""
    end = dt.datetime.fromtimestamp(t / 1000)
    db.put_kv(f"end_at:{device}", iso(end))  # kept across a restart: the queue may still hold the audio before it
    log.info("%s stopped recording at %s", device, end.time())
    ws.note(f"{device} 停止录音（{end.strftime('%H:%M:%S')}），处理完剩余音频后关闭对话")
    return {"ok": True}


@router.post("/api/hold/release")
async def hold_release(device: str):
    """No older audio of the recorder is coming from the phone: it no longer has the recorder (unbound, or another bound
    in its place), or its backlog is all up and no upload follows to let go (a recorder that stopped). Finalization
    stops waiting for it now, not HOLD_MAX_S after the phone last said the hold (10-07: LR-P3-116C's 261 pieces waited
    from 17:47 to 05:47)."""
    if db.kv(f"hold:{device}"):
        log.info("%s hold let go: no older audio is coming from its phone", device)
        ws.note(f"{device} 的旧录音不用再等了，继续定稿")
    audio.hold(device, None)  # clear an expired hold's reason too
    return {"ok": True}


@router.post("/api/device-status")
async def device_status_report(device: str, hw: dict):
    """What a client read from a BLE recorder (battery, card, firmware, model): each one kept, the newest shown with
    the device's pipeline."""
    db.add_device_status(device, iso(dt.datetime.now()), hw)
    return {"ok": True}


class DeviceEvent(BaseModel):
    src: str               # "rec": a record of the recorder's own event log (firmware lr_log.h); else the app that wrote it
    seq: int               # the recorder log's sequence number, or the app's own: unique per device and src
    at: int | None = None  # epoch ms, put on the clock by the phone (a record's frame by the boot's TIME records); None: unknown
    data: dict             # rec: {boot, t, code, x, a, b} as recorded; an app's: {kind, text?, ...}


@router.post("/api/device-log")
async def device_log_upload(device: str, events: list[DeviceEvent]):
    """A batch of a device's events, from the phone that read them; one sent again replaces the one kept."""
    db.put_device_log(device, [{**e.model_dump(), "at": None if e.at is None else iso(dt.datetime.fromtimestamp(e.at / 1000))} for e in events])
    return {"ok": True}


@router.get("/api/device-log")
async def device_log(device: str, since: str, until: str | None = None):
    """A device's events, status reports and audio by the minute (audio.minutes) in [since, until)."""
    ms = lambda t: int(dt.datetime.fromisoformat(t).timestamp() * 1000)
    return {"events": db.device_log(device, since, until), "status": db.device_statuses(device, since, until),
            "audio": audio.minutes(device, ms(since), ms(until) if until else 1 << 62)}


@router.get("/api/calls")
async def calls(since: str):
    """The ASR and LLM calls since `since`, by kind and engine (db.calls), and the StepFun balance (db.balance)."""
    return {"calls": db.calls(since), "balance": db.balance("stepfun")}


@router.get("/api/perf")
async def perf_day(day: str):
    """A day's (YYYY-MM-DD) performance rows in this tenant's store, oldest first (perf.day): {stalls, samples, requests,
    pieces}; the event loop's stalls and the process's minutes are in the first tenant's only."""
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "day is YYYY-MM-DD")
    # thousands of rows: read and encoded off the event loop (FastAPI's own encoding of a list that long takes ~20 ms on it)
    body = await anyio.to_thread.run_sync(lambda: json.dumps(perf.day(db.conn(), day), ensure_ascii=False), limiter=threads.DB)
    return Response(body, media_type="application/json")


@router.get("/api/upgrades")
async def upgrades(device: str, since: str):
    """A device's firmware updates begun since `since`, oldest first: what its app logged joined with the recorder's DFU
    and BOOT records (db.upgrades)."""
    return db.upgrades(device, since)


@router.get("/api/client-config")
async def client_config():
    """What the apps ask on start and while recording: piece lengths per app state (the server changes them without
    a new build; short pieces in front make drafts quick, long ones in the background save requests). The lengths now
    (slices()), shortened while the live page is open, the same as /api/chunks answers: the app's poll never undoes
    them."""
    return {"slice": slices()}


@router.get("/api/app-setup")
async def app_setup():
    """For the web page's QR: the token to hand the app (its tenant's, the one the page sent), so it doesn't need
    typing in by hand. Empty in no-auth mode, same as everywhere else."""
    return {"token": T().token}
