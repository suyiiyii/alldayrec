"""Live transcript of the phone's recording.

    set -a; . .env; set +a; uv run python server.py [--port 8770]   (variables: .env.example; tenants: ../tenants.json, tenant.py)

Takes the audio pieces the app and the /record page upload, keeps them on disk as a queue and feeds them in time
order (only as fast as finalization keeps up), finds speech with FireRed Stream-VAD and transcribes in two layers:
final pieces of up to CHUNK_S cut in pauses (sentences, conversations, summaries), and while a page watches, drafts at
each pause for the minutes not final yet; all state is in data/live.db.
"""
import argparse
import contextlib
import datetime as dt
import logging
import os
import subprocess
from pathlib import Path

# numpy's and scipy's OpenBLAS start a thread per CPU when they load (31 on the test box): audio.health() on every upload
# woke them to spin in sched_yield, 63 % of the server's CPU over 74.7 h; with 1 its 60 pieces took 0.15 s sys, not
# ~150 s, and less wall time. Set before the first import that loads numpy (all below); .env still overrides it.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import anyio
import anyio.to_thread
from fastapi import FastAPI

import addresses
import asr
import audio
import auth
import core
import db
import firmware
import infer
import routes_api
import routes_audio
import routes_pages
import routes_speakers
import routes_upload
import tenant
import threads
import ws
from core import state
from device_pipeline import pipeline
from monitor import balance_loop, loop_stalls, metrics_loop, status_loop
from routes_speakers import voices_loop
from summaries import RECAP_AT, recap_loop, summary_worker
from tenant import T

HERE = Path(__file__).resolve().parent

log = logging.getLogger("live")


@contextlib.asynccontextmanager
async def lifespan(app):
    """The process's tasks (the voiceprint model, the metrics, the balance) and each tenant's (run_tenant), until the
    server stops: then all of them are cancelled."""
    async with anyio.create_task_group() as tg:
        tg.start_soon(load_model, name="model")
        tg.start_soon(supervised, "metrics", metrics_loop, name="metrics")
        if os.environ.get("STEPFUN_API_KEY"):
            tg.start_soon(supervised, "balance", balance_loop, name="balance")
        if os.environ.get("LOOP_STALL_MS"):
            tg.start_soon(supervised, "stalls", loop_stalls, name="stalls")
        for t in core.TENANTS:
            tg.start_soon(run_tenant, t, name=f"tenant:{t.id}")
        yield
        tg.cancel_scope.cancel()


app = FastAPI(lifespan=lifespan)
app.include_router(firmware.router)  # the recorders' firmware packages (firmware.DIR: the first tenant's, set by main)
app.include_router(addresses.router)  # GET /api/addresses: where phones reach this server (the 连接手机 page)
MODEL = anyio.Event()  # the model is loaded


async def load_model():
    from diarist import load_embedder
    state["embed"] = await anyio.to_thread.run_sync(load_embedder, limiter=threads.INFER)
    MODEL.set()


async def run_tenant(t: tenant.Tenant):
    """A tenant's loops, each started again after a failure; the tenant's pipelines and live analyses run in its task
    group too, so cancelling t.scope stops all of it."""
    tenant.enter(t)
    t.recordings.mkdir(parents=True, exist_ok=True)
    with anyio.CancelScope() as t.scope:
        async with anyio.create_task_group() as t.tg:
            for name, fn in (("pipeline", pipelines), ("status", status_loop), ("summary", summary_worker), ("voices", voices_loop),
                             *((("recap", recap_loop),) if RECAP_AT else ())):
                t.tg.start_soon(supervised, name, fn, name=f"{name}:{t.id}")


async def supervised(name: str, fn):
    """fn for good: one that ends (a bug, a returned loop) is logged and started again RESTART_S later, without the
    tasks beside it (a TaskGroup would otherwise cancel them all)."""
    while True:
        try:
            await fn()
            log.error("task %s ended: returned", name)
        except Exception as e:
            log.exception("task %s ended: %r", name, threads.first(e))
        await anyio.sleep(RESTART_S)


async def pipelines():
    """One pipeline per device with audio after its finalized time: a phone sending last night's backlog while an
    iPhone records today is transcribed on its own timeline. A device's pipeline lives on once started, in the
    tenant's task group."""
    await MODEL.wait()
    while True:
        devices = [r[0] for r in db.conn().execute("SELECT DISTINCT device FROM pieces")]
        after = {d: dt.datetime.fromisoformat(v) for d in devices if (v := db.kv(f"finalized_to:{d}"))}
        for d in audio.pending(after):
            if d not in T().pipelines:
                T().pipelines.add(d)
                T().tg.start_soon(lasting, d, name=f"pipeline:{d}")
        await anyio.sleep(5)


RESTART_S = 60  # a device's pipeline that failed starts again this much later


async def lasting(device: str):
    """The device's pipeline for good: one that fails (a bug, a missing file) starts again RESTART_S later from the
    finalized time, as after a server restart (10-09: a new instance's first device stayed untranscribed until one)."""
    while True:
        log.info("pipeline for %s from %s", device, db.kv(f"finalized_to:{device}"))
        try:
            await pipeline(device)
        except Exception as e:
            log.exception("pipeline %s ended", device)
            ws.note(f"{device} 的处理任务退出：{threads.first(e)!r}"[:140] + f"，{RESTART_S} s 后重来", "error")
        await anyio.sleep(RESTART_S)


app.add_middleware(auth.Tenants)
# the routers in this order: the routes match as they did when they were all in this file (test_routes_snapshot.py)
app.include_router(routes_pages.router)
app.include_router(routes_upload.router)
app.include_router(routes_api.router)
app.include_router(routes_speakers.router)
app.include_router(routes_audio.router)
app.include_router(ws.router)


class TenantTag(logging.Filter):
    """%(tenant)s: "[<id>] " of the tenant a line is about when the process has several, else nothing (one tenant's
    log reads as before)."""

    def filter(self, record):
        t = tenant.current.get(None)
        record.tenant = f"[{t.id}] " if t and len(core.TENANTS) > 1 else ""
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8770")))
    args = ap.parse_args()
    missing = []
    engines = {e for e in (asr.ENGINE, asr.DRAFT_ENGINE, asr.ENGINE_FALLBACK) if e}
    if engines - asr.PROVIDERS.keys() - {"qwen-stream"} or "qwen-stream" in (asr.ENGINE, asr.ENGINE_FALLBACK):
        raise SystemExit(f"ENGINE, DRAFT_ENGINE, ENGINE_FALLBACK must be one of {', '.join(asr.PROVIDERS)} (DRAFT_ENGINE also qwen-stream), "
                         f"not {', '.join(engines - asr.PROVIDERS.keys())}")
    if "volc" in engines and not os.environ.get("VOLC_ASR_KEY"):
        missing.append("VOLC_ASR_KEY")
    if engines & {"stepfun", "stepfun-sse"} and not os.environ.get("STEPFUN_API_KEY"):
        missing.append("STEPFUN_API_KEY")
    if engines & {"qwen", "qwen-stream"} and not os.environ.get("DASHSCOPE_API_KEY"):
        missing.append("DASHSCOPE_API_KEY")
    if file := engines & {"stepfun", "stepfun-file"}:  # diarize(): your own upload bridge and file-mode ASR service
        missing += [f"{k} (for {', '.join(sorted(file))})" for k in
                    ("ASR_UPLOAD_BRIDGE_URL", "ASR_UPLOAD_BRIDGE_TOKEN", "ASR_SERVICE_URL", "ASR_SERVICE_BASIC_AUTH") if not os.environ.get(k)]
    missing += [k for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"not set: {', '.join(missing)} (see .env.example)")
    h = logging.StreamHandler()
    h.addFilter(TenantTag())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(tenant)s%(message)s", handlers=[h])
    infer.fetch()  # a first start: the missing model files, downloaded
    infer.check()  # every model file there and the pinned one, before anything starts
    log.info("models ok: %d files in %s", len(infer.MODELS), infer.DIR)
    core.TENANTS[:] = tenant.load()
    for t in core.TENANTS:
        tenant.call(t, db.init)
    firmware.DIR = core.TENANTS[0].data / "firmware"  # every tenant's: the first one's
    commit = HERE / "COMMIT"  # written by deploy.sh: the server has no git checkout
    state["commit"] = commit.read_text().strip() if commit.exists() else \
        subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    log.info("tenants: %s", ", ".join(t.id for t in core.TENANTS))
    log.info("ready on %s:%d", os.environ.get("HOST", "127.0.0.1"), args.port)

    import uvicorn
    # a page's WebSocket would hold a stop for good: 5 s, then the lifespan cancels every task (pipelines resume from
    # their finalized time on the next start)
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=args.port, log_level="warning", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
