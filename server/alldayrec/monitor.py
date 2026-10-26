"""What the pages and the metrics read about the pipelines: each device's status, the status line pushed to the pages,
the Prometheus numbers pushed every minute, and the StepFun balance; the process each minute and the event loop's
stalls, kept (perf.py)."""
import base64
import ctypes
import datetime as dt
import hashlib
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

import anyio
import anyio.to_thread

from alldayrec import asr
from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import device_pipeline
from alldayrec import perf
from alldayrec import stepfun_asr
from alldayrec import tenant
from alldayrec import threads
from alldayrec import ws
from alldayrec.core import state
from alldayrec.pipeline_state import PipelineState
from alldayrec.tenant import T

log = logging.getLogger("live")
HERE = Path(__file__).resolve().parent


def device_status(device: str, st: PipelineState) -> dict:
    end, done, pr = st.audio_end, st.done_to, st.progress
    # what the page shows first: drafts while it watches (the last 10); copies, as status() runs in a thread while the
    # loop appends to these and clears them (progress it replaces whole)
    lat, fin = list(st.latency if st.drafting else st.final_latency), list(st.final_latency)
    left, received_to = audio.backlog(device, done)
    hold, seen, until, arrived = audio.held(device), audio.hold_seen(device), audio.hold_until(device), audio.arrival(device)
    # seconds of audio finalized per second, over the commits of the last 10 min (silence goes fast, speech ~8x)
    rate = (pr[-1][1] - pr[0][1]).total_seconds() / (pr[-1][0] - pr[0][0]) if len(pr) > 1 and pr[-1][0] > pr[0][0] else None
    import_progress = None
    if imported(device):
        total, _ = audio.backlog(device, None)
        import_progress = {"done_s": round(total - left, 1), "total_s": total}
        # Recording dates can have gaps: measure finalized audio, not distance along the calendar.
        rate = None
        if len(pr) > 1 and pr[-1][0] > pr[0][0] and time.time() - pr[-1][0] < 600:
            before, _ = audio.backlog(device, pr[0][1])
            after, _ = audio.backlog(device, pr[-1][1])
            rate = (before - after) / (pr[-1][0] - pr[0][0])
    return {"device": device, "audio_end": end.isoformat(timespec="seconds") if end else None,
            "import_progress": import_progress,
            "behind_s": round((dt.datetime.now() - end).total_seconds(), 1) if end else None,
            "queued": audio.queued(device), "done_to": done.isoformat(timespec="seconds") if done else None,
            "received_to": received_to.isoformat(timespec="seconds") if received_to else None, "left_s": left,
            # finalization waits here for older audio the phone is still sending (its hw: backlog_kb, backlog_eta_s)
            "hold": dt.datetime.fromtimestamp(hold / 1000).isoformat(timespec="seconds") if hold else None,
            # last contact, and the total-life deadline from the hold's first set (audio.HOLD_MAX_S)
            "hold_seen": dt.datetime.fromtimestamp(seen).isoformat(timespec="seconds") if hold and seen else None,
            "hold_until": dt.datetime.fromtimestamp(until).isoformat(timespec="seconds") if hold and until is not None else None,
            "hold_expired": audio.hold_expired(device),
            # its newest audio came in device_pipeline.LIVE_AUDIO_S ago at most: behind_s and the latencies are about now (otherwise
            # behind_s is how long ago it last recorded); an import batch never records, its audio only just came in
            "recording": not imported(device) and bool(arrived) and time.time() * 1000 - arrived["at"] < device_pipeline.LIVE_AUDIO_S * 1000,
            "rate": round(rate, 1) if rate else None, "eta_s": round(left / rate) if rate and rate > 0.1 and left > 30 else None,
            "last_speech": st.last_speech.isoformat(timespec="seconds") if st.last_speech else None,
            "refine": st.refine, "conversation": st.conversation, "drafting": st.drafting, "draft": st.draft,
            "analyzing": st.analyzing,  # the live analysis is running
            "draft_to": st.draft_to.isoformat(timespec="seconds") if st.draft_to else None,
            # how far behind what one reads now is: its newest sentence's end to its showing; the median a diagnosis
            "latency_s": lat[-1] if lat else None, "latency_med_s": sorted(lat)[len(lat) // 2] if lat else None,
            "final_latency_s": fin[-1] if fin else None,
            "arrival": arrived, "sound": sound(device, received_to)}


def sound(device: str, end: dt.datetime | None) -> dict | None:
    """How the device's last 10 min of audio (before `end`, its newest) sounded: s of it, s in zero runs (the mic sent
    nothing), full-scale samples (audio.health)."""
    ms = int(end.timestamp() * 1000) if end else 0
    m = audio.minutes(device, ms - 600_000, ms) if end else []
    return {"s": round(sum(r["s"] for r in m), 1), "zero_s": round(sum(r["zero_s"] for r in m), 2), "full": sum(r["full"] for r in m)} if m else None


def imported(device: str) -> bool:
    """A batch the 导入录音 page sent (import-<when>), or the docs' scripts' `import`: not a recorder."""
    return device == "import" or device.startswith("import-")


def recorders(ds: list[dict]) -> list[dict]:
    """Every device with audio, its newest recording first: where its stored audio starts and ends and its recorder's
    newest status. devices (ds) has only those whose pipeline runs: after a restart, one whose audio is all final has
    none until it records again. Three index lookups a device, none started for it."""
    hw = {d["device"]: d["hw"] for d in ds}
    ts = lambda ms: dt.datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")
    out = []
    for d in audio.devices():
        end = db.conn().execute("SELECT max(start_ms + seconds * 1000) FROM pieces WHERE device = ?", (d,)).fetchone()[0]
        start = db.conn().execute("SELECT min(start_ms) FROM pieces WHERE device = ?", (d,)).fetchone()[0]
        out.append({"device": d, "audio_start": ts(start), "audio_end": ts(end), "hw": hw[d] if d in hw else db.device_status(d)})
    return sorted(out, key=lambda r: r["audio_end"], reverse=True)


def status() -> dict:
    """What the page's status line shows (the device whose audio came in last), every device's pipeline, every device
    with audio (recorders; the import batches apart), and the pieces still queued over all devices. Its callers run it
    in a thread (threads.DB): its reads of the pieces held the event loop up to 0.8 s under load (10-24)."""
    ds = [{**device_status(d, st), "hw": db.device_status(d)} for d, st in list(T().devices.items())]  # a pipeline may start meanwhile
    top = max(ds, key=lambda d: d["audio_end"] or "", default={"device": None, "audio_end": None, "behind_s": None, "done_to": None,
                                                                 "last_speech": None, "refine": None, "conversation": None, "latency_s": None,
                                                                 "latency_med_s": None, "final_latency_s": None})
    summaries = db.conn().execute("SELECT count(*) FROM summaries WHERE status = 'pending'").fetchone()[0]
    rs = recorders(ds)  # 我的 lists the recorders, 导入录音 the batches it sent
    return {**top, "queued": sum(d["queued"] for d in ds), "devices": ds, "recorders": [r for r in rs if not imported(r["device"])],
            "imports": [r for r in rs if imported(r["device"])], "summaries_pending": summaries, "summary_queue": T().summaries[1].statistics().current_buffer_used,
            "recording": any(d["recording"] for d in ds), "last_audio": max((d["received_to"] for d in ds if d["received_to"]), default=None),
            "balance": db.balance("stepfun"),
            "commit": state["commit"], "chunks": T().chunks, "lang": T().lang,
            "engine": asr.ENGINE, "draft_engine": asr.DRAFT_ENGINE, "fallback_engine": asr.ENGINE_FALLBACK or None}  # the pages word them


async def status_loop():
    while True:
        if T().clients:  # a tenant with no page open has nobody to tell
            await ws.broadcast({"type": "status", **await anyio.to_thread.run_sync(status, limiter=threads.DB)})
        await anyio.sleep(2)


async def balance_loop():
    """The StepFun balance every hour: what transcription costs a day, and when the money runs out. One account
    for every tenant: kept in the first one's store, the others' pages show none."""
    while True:
        try:
            tenant.call(core.TENANTS[0], db.add_balance, "stepfun", await stepfun_asr.balance())
        except Exception as e:
            log.warning("stepfun balance: %r", e)
        await anyio.sleep(3600)


VM_INGEST_URL = os.environ.get("VM_INGEST_URL", "")  # .../api/v1/import/prometheus: metrics_loop pushes there; empty: nothing
STARTED = dt.datetime.now().isoformat()  # lr_calls_total counts from here: a restart resets it, as a counter may
CHARGE = {"battery": 0, "charging": 1, "full": 2}  # a recorder's hw charge (firmware lr_power.h)


def prom_line(name: str, labels: dict, v) -> str:
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return name + "{" + ",".join(f'{k}="{esc(x)}"' for k, x in labels.items()) + "} " + repr(float(v))


def metrics() -> str:
    """The current tenant's numbers in Prometheus text, its id the instance label: status(), db.calls and
    db.balance. Devices by their LR-… name, a phone (named by its owner) by a hash of it; never what was said, nor a name
    of anyone."""
    s, now, out = status(), dt.datetime.now(), []

    def m(name, v, **labels):
        if v is not None:
            out.append(prom_line(name, {"instance": T().id, **labels}, v))
    m("lr_up", 1, commit=s["commit"])
    m("lr_summaries_pending", s["summaries_pending"])
    for d in s["devices"]:
        dev = d["device"] if d["device"].startswith("LR-") else "phone-" + hashlib.sha256(d["device"].encode()).hexdigest()[:8]
        hw, snd, arr = d["hw"] or {}, d["sound"] or {}, d["arrival"] or {}
        # the pipeline, its last 10 min of audio (sound), the recorder as its phone last read it (hw)
        for name, v in (("behind_seconds", d["behind_s"]), ("latency_seconds", d["latency_s"]), ("queued_pieces", d["queued"]),
                        ("backlog_seconds", d["left_s"]), ("hold_active", d["hold"] is not None), ("recording", d["recording"]),
                        ("arrival_lag_seconds", arr.get("lag_s")), ("sound_seconds", snd.get("s")),
                        ("sound_zero_seconds", snd.get("zero_s")), ("sound_full_samples", snd.get("full")),
                        ("device_battery_percent", hw.get("battery")), ("device_charge", CHARGE.get(hw.get("charge"))),
                        ("device_last_seen_seconds", hw.get("at") and (now - dt.datetime.fromisoformat(hw["at"])).total_seconds())):
            m(f"lr_{name}", v, device=dev)
        if hw.get("fw"):
            m("lr_device_info", 1, device=dev, firmware=hw["fw"])
    for c in db.calls(STARTED):
        for result, n in {"ok": c["n"] - sum(c["failed"].values()), **c["failed"]}.items():  # failed: by HTTP code or class
            m("lr_calls_total", n, kind=c["kind"], engine=c["engine"], result=result)
        m("lr_call_audio_seconds_total", round(c["sent_h"] * 3600) or None, kind=c["kind"], engine=c["engine"])
    for c in db.calls((now - dt.timedelta(hours=1)).isoformat()):
        for q in ("50", "90"):
            m("lr_call_latency_seconds", c[f"ms{q}"] and c[f"ms{q}"] / 1000, kind=c["kind"], engine=c["engine"], quantile=f"0.{q[0]}")
    b = s["balance"] or {}
    m("lr_stepfun_balance_cny", b.get("cny"))
    m("lr_stepfun_days_left", b.get("days_left"))
    return "\n".join(out) + "\n"


def process_metrics(s: dict) -> str:
    """The process's minute (perf.sample) in Prometheus text, as the first tenant's instance."""
    xs = [("process_cpu_cores", {}, s["cpu_s"] / s["s"] if s["s"] else None), ("process_rss_mb", {}, s["rss_mb"]),
          ("process_hwm_mb", {}, s["hwm_mb"]), ("loop_lag_ms", {"quantile": "0.5"}, s["lag_p50_ms"]),
          ("loop_lag_ms", {"quantile": "0.99"}, s["lag_p99_ms"])]
    xs += [(f"threads_{k}", {"pool": pool}, v) for pool, t in (s["threads"] or {}).items() for k, v in t.items()]
    return "".join(prom_line(f"lr_{n}", {"instance": core.TENANTS[0].id, **labels}, v) + "\n" for n, labels, v in xs if v is not None)


def queued() -> int:
    return sum(audio.queued(d) for d in T().devices)


# glibc keeps what the process frees and gives back only the top of a heap: a voiceprint's activations (~0.6 GB for
# 15 s, onnxruntime without its arena) and the segmenters' 900 s buffers, copied on every piece, left RSS mostly 25-60 MB
# higher after a final with speech (10-25, MALLOC_ARENA_MAX=2: 294 MB at start, 647 MB 8 h later with no page open).
# Given back once a minute, a replay of such a night stayed at 285 MB instead of 494 MB. Linux only (glibc).
TRIM = getattr(ctypes.CDLL(None), "malloc_trim", None) if sys.platform == "linux" else None


async def metrics_loop():
    """Every minute: the heaps' free pages given back (TRIM), the process's sample and every tenant's requests kept
    (perf.sample), and with VM_INGEST_URL every tenant's metrics() and the sample to VictoriaMetrics (basic auth
    VM_INGEST_USER / VM_INGEST_PASSWORD); a failure is a warning, the next minute tries again."""
    while True:
        if TRIM:
            await anyio.to_thread.run_sync(TRIM, 0)
        try:
            s = perf.sample(sum(tenant.call(t, queued) for t in core.TENANTS), sum(len(t.clients) for t in core.TENANTS))
        except Exception as e:
            s = None
            log.warning("perf sample: %r", e)
        if VM_INGEST_URL:
            try:
                auth = base64.b64encode(f"{os.environ.get('VM_INGEST_USER', '')}:{os.environ.get('VM_INGEST_PASSWORD', '')}".encode()).decode()
                tenants = await anyio.to_thread.run_sync(lambda: "".join(tenant.call(t, metrics) for t in core.TENANTS), limiter=threads.DB)
                body = (tenants + (process_metrics(s) if s and core.TENANTS else "")).encode()
                await asr._http(VM_INGEST_URL, body, {"Authorization": f"Basic {auth}", "Content-Type": "text/plain"})
            except Exception as e:
                log.warning("metrics push: %s", asr.failure(e))  # the class and code, never the URL or credentials
        await anyio.sleep(60)


BEAT_S = 0.02  # how often loop_stalls' task beats, and its thread looks
LOOP_STALL_MS = float(os.environ.get("LOOP_STALL_MS", "100"))  # 0: no loop_stalls


async def loop_stalls():
    """The event loop held up longer than LOOP_STALL_MS (a blocking call in a route or a task, a long computation on
    it) is one log line and a perf_stalls row with how long and where the loop was when the thread watching its beat
    caught it (the frames of this directory's files, and the innermost one); at most a line a second, the stalls in
    between counted. Each beat's lag and the thread pools' use go to the minute's sample (perf.beat). ~0.6 % of a
    core, unlike asyncio's debug mode."""
    beat = {"at": time.monotonic(), "loop": threading.get_ident(), "stop": False, "stalls": []}
    threading.Thread(target=watch, args=(beat, LOOP_STALL_MS / 1000), daemon=True, name="loop-stalls").start()
    try:
        while True:
            beat["at"] = time.monotonic()
            await anyio.sleep(BEAT_S)
            perf.beat(time.monotonic() - beat["at"] - BEAT_S)
            while beat["stalls"]:  # the thread's lines (a list append is atomic): their rows written here, on the loop
                perf.stall(*beat["stalls"].pop(0))
    finally:
        beat["stop"] = True  # its thread with it


def watch(beat: dict, limit: float):
    # ponytail: a C call that keeps the GIL (one huge json.loads) is seen only once it returns: the length is right, the
    # stack is where the loop went on to; py-spy dump --native when that matters
    shown, quiet = 0.0, 0
    while not beat["stop"]:
        time.sleep(BEAT_S)
        at = beat["at"]
        if time.monotonic() - at - BEAT_S < limit:
            continue
        frame = sys._current_frames().get(beat["loop"])
        fs = traceback.extract_stack(frame) if frame else []
        while beat["at"] == at and not beat["stop"]:
            time.sleep(BEAT_S)
        if time.monotonic() - shown < 1:
            quiet += 1
            continue
        where = [f for f in fs if Path(f.filename).resolve().parent == HERE] + [f for f in fs[-1:] if Path(f.filename).resolve().parent != HERE]
        ms, frames = int((beat["at"] - at - BEAT_S) * 1000), " > ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in where)
        log.warning("event loop held up %d ms%s: %s", ms, f" (+{quiet} more since the last line)" if quiet else "", frames)
        beat["stalls"].append((perf.now(), ms, frames, quiet))
        shown, quiet = time.monotonic(), 0
