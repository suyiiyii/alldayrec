"""What the pages and the metrics read about the pipelines: each device's status, the status line pushed to the pages,
the Prometheus numbers pushed every minute, and the StepFun balance; and, when asked for, the event loop's stalls."""
import base64
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

from alldayrec import asr
from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import device_pipeline
from alldayrec import stepfun_asr
from alldayrec import tenant
from alldayrec import ws
from alldayrec.core import state
from alldayrec.pipeline_state import PipelineState
from alldayrec.tenant import T

log = logging.getLogger("live")
HERE = Path(__file__).resolve().parent


def device_status(device: str, st: PipelineState) -> dict:
    end, done, pr = st.audio_end, st.done_to, st.progress
    lat = st.latency if st.drafting else st.final_latency  # what the page shows first: drafts while it watches (the last 10)
    left, received_to = audio.backlog(device, done)
    hold, seen, arrived = audio.held(device), audio.hold_seen(device), audio.arrival(device)
    # seconds of audio finalized per second, over the commits of the last 10 min (silence goes fast, speech ~8x)
    rate = (pr[-1][1] - pr[0][1]).total_seconds() / (pr[-1][0] - pr[0][0]) if len(pr) > 1 and pr[-1][0] > pr[0][0] else None
    return {"device": device, "audio_end": end.isoformat(timespec="seconds") if end else None,
            "behind_s": round((dt.datetime.now() - end).total_seconds(), 1) if end else None,
            "queued": audio.queued(device), "done_to": done.isoformat(timespec="seconds") if done else None,
            "received_to": received_to.isoformat(timespec="seconds") if received_to else None, "left_s": left,
            # finalization waits here for older audio the phone is still sending (its hw: backlog_kb, backlog_eta_s)
            "hold": dt.datetime.fromtimestamp(hold / 1000).isoformat(timespec="seconds") if hold else None,
            # when its phone last said it, and when it is let go without the phone (audio.HOLD_MAX_S)
            "hold_seen": dt.datetime.fromtimestamp(seen).isoformat(timespec="seconds") if hold and seen else None,
            "hold_until": dt.datetime.fromtimestamp(seen + audio.HOLD_MAX_S).isoformat(timespec="seconds") if hold and seen else None,
            # its newest audio came in device_pipeline.LIVE_AUDIO_S ago at most: behind_s and the latencies are about now (otherwise
            # behind_s is how long ago it last recorded)
            "recording": bool(arrived) and time.time() * 1000 - arrived["at"] < device_pipeline.LIVE_AUDIO_S * 1000,
            "rate": round(rate, 1) if rate else None, "eta_s": round(left / rate) if rate and rate > 0.1 and left > 30 else None,
            "last_speech": st.last_speech.isoformat(timespec="seconds") if st.last_speech else None,
            "refine": st.refine, "conversation": st.conversation, "drafting": st.drafting, "draft": st.draft,
            "analyzing": st.analyzing,  # the live analysis is running
            "draft_to": st.draft_to.isoformat(timespec="seconds") if st.draft_to else None,
            # how far behind what one reads now is: its newest sentence's end to its showing; the median a diagnosis
            "latency_s": lat[-1] if lat else None, "latency_med_s": sorted(lat)[len(lat) // 2] if lat else None,
            "final_latency_s": st.final_latency[-1] if st.final_latency else None,
            "arrival": arrived, "sound": sound(device, received_to)}


def sound(device: str, end: dt.datetime | None) -> dict | None:
    """How the device's last 10 min of audio (before `end`, its newest) sounded: s of it, s in zero runs (the mic sent
    nothing), full-scale samples (audio.health)."""
    ms = int(end.timestamp() * 1000) if end else 0
    m = audio.minutes(device, ms - 600_000, ms) if end else []
    return {"s": round(sum(r["s"] for r in m), 1), "zero_s": round(sum(r["zero_s"] for r in m), 2), "full": sum(r["full"] for r in m)} if m else None


def status() -> dict:
    """What the page's status line shows (the device whose audio came in last), every device's pipeline, and the
    pieces still queued over all devices."""
    ds = [{**device_status(d, st), "hw": db.device_status(d)} for d, st in T().devices.items()]
    top = max(ds, key=lambda d: d["audio_end"] or "", default={"device": None, "audio_end": None, "behind_s": None, "done_to": None,
                                                                 "last_speech": None, "refine": None, "conversation": None, "latency_s": None,
                                                                 "latency_med_s": None, "final_latency_s": None})
    summaries = db.conn().execute("SELECT count(*) FROM summaries WHERE status = 'pending'").fetchone()[0]
    return {**top, "queued": sum(d["queued"] for d in ds), "devices": ds, "summaries_pending": summaries, "summary_queue": T().summaries[1].statistics().current_buffer_used,
            "recording": any(d["recording"] for d in ds), "last_audio": max((d["received_to"] for d in ds if d["received_to"]), default=None),
            "balance": db.balance("stepfun"),
            "commit": state["commit"], "chunks": T().chunks, "lang": T().lang,
            "engine": asr.ENGINE, "draft_engine": asr.DRAFT_ENGINE, "fallback_engine": asr.ENGINE_FALLBACK or None}  # the pages word them


async def status_loop():
    while True:
        if T().clients:  # a tenant with no page open has nobody to tell
            await ws.broadcast({"type": "status", **status()})
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


async def metrics_loop():
    """Every tenant's metrics() to VictoriaMetrics every minute (basic auth VM_INGEST_USER / VM_INGEST_PASSWORD); a failed push is a
    warning, the next minute tries again."""
    while True:
        if VM_INGEST_URL:
            try:
                auth = base64.b64encode(f"{os.environ.get('VM_INGEST_USER', '')}:{os.environ.get('VM_INGEST_PASSWORD', '')}".encode()).decode()
                body = "".join(tenant.call(t, metrics) for t in core.TENANTS).encode()
                await asr._http(VM_INGEST_URL, body, {"Authorization": f"Basic {auth}", "Content-Type": "text/plain"})
            except Exception as e:
                log.warning("metrics push: %s", asr.failure(e))  # the class and code, never the URL or credentials
        await anyio.sleep(60)


BEAT_S = 0.02  # how often loop_stalls' task beats, and its thread looks


async def loop_stalls():
    """LOOP_STALL_MS (unset: off): the event loop held up longer than that (a blocking call in a route or a task, a
    long computation on it) is one log line with how long and where the loop was when the thread watching its beat
    caught it (the frames of this directory's files, and the innermost one); at most a line a second, the stalls in
    between counted. Cheap enough to leave on, unlike asyncio's debug mode."""
    beat = {"at": time.monotonic(), "loop": threading.get_ident(), "stop": False}
    threading.Thread(target=watch, args=(beat, float(os.environ["LOOP_STALL_MS"]) / 1000), daemon=True, name="loop-stalls").start()
    try:
        while True:
            beat["at"] = time.monotonic()
            await anyio.sleep(BEAT_S)
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
        log.warning("event loop held up %d ms%s: %s", (beat["at"] - at - BEAT_S) * 1000, f" (+{quiet} more since the last line)" if quiet else "",
                    " > ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in where))
        shown, quiet = time.monotonic(), 0
