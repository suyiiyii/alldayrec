"""Live transcript of the phone's recording.

    alldayrec [--port 8770]            installed (pip install alldayrec, uvx alldayrec)
    set -a; . ../.env; set +a; uv run alldayrec [--port 8770]       in server/ of a checkout
(variables: .env.example; tenants: DATA_DIR/tenants.json, tenant.py, one made at the first start)

Takes the audio pieces the app and the /record page upload, keeps them on disk as a queue and feeds them in time
order (only as fast as finalization keeps up), finds speech with FireRed Stream-VAD and transcribes in two layers:
final pieces of up to CHUNK_S cut in pauses (sentences, conversations, summaries), and while a page watches, drafts at
each pause for the minutes not final yet; all state is in data/live.db.
"""
import argparse
import contextlib
import logging
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

# numpy's and scipy's OpenBLAS start a thread per CPU when they load (31 on the test box): audio.health() on every upload
# woke them to spin in sched_yield, 63 % of the server's CPU over 74.7 h; with 1 its 60 pieces took 0.15 s sys, not
# ~150 s, and less wall time. Set before the first import that loads numpy (all below); .env still overrides it.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import anyio
import anyio.to_thread
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from alldayrec import addresses
from alldayrec import asr
from alldayrec import audio
from alldayrec import auth
from alldayrec import core
from alldayrec import db
from alldayrec import firmware
from alldayrec import infer
from alldayrec import local_asr
from alldayrec import monitor
from alldayrec import music
from alldayrec import perf
from alldayrec import routes_api
from alldayrec import routes_audio
from alldayrec import routes_pages
from alldayrec import routes_sentences
from alldayrec import routes_speakers
from alldayrec import routes_upload
from alldayrec import tenant
from alldayrec import threads
from alldayrec import ws
from alldayrec.core import state
from alldayrec.device_pipeline import pipeline
from alldayrec.monitor import balance_loop, loop_stalls, metrics_loop, status_loop
from alldayrec.routes_speakers import voices_loop
from alldayrec.summaries import RECAP_AT, recap_loop, summary_worker
from alldayrec.late import processor as late_processor
from alldayrec.tenant import T

HERE = Path(__file__).resolve().parent

log = logging.getLogger("live")


@contextlib.asynccontextmanager
async def lifespan(app):
    """The process's tasks (the metrics, the balance) and each tenant's (run_tenant), until the server stops: then all
    of them are cancelled."""
    async with anyio.create_task_group() as tg:
        tg.start_soon(supervised, "metrics", metrics_loop, name="metrics")
        if os.environ.get("STEPFUN_API_KEY"):
            tg.start_soon(supervised, "balance", balance_loop, name="balance")
        if monitor.LOOP_STALL_MS > 0:
            tg.start_soon(supervised, "stalls", loop_stalls, name="stalls")
        for t in core.TENANTS:
            tg.start_soon(run_tenant, t, name=f"tenant:{t.id}")
        yield
        tg.cancel_scope.cancel()


app = FastAPI(lifespan=lifespan)
app.include_router(firmware.router)  # the recorders' firmware packages (firmware.DIR: the first tenant's, set by main)
app.include_router(addresses.router)  # GET /api/addresses: where phones reach this server (the 连接手机 page)


async def run_tenant(t: tenant.Tenant):
    """A tenant's loops, each started again after a failure; the tenant's pipelines and live analyses run in its task
    group too, so cancelling t.scope stops all of it."""
    tenant.enter(t)
    t.recordings.mkdir(parents=True, exist_ok=True)
    with anyio.CancelScope() as t.scope:
        async with anyio.create_task_group() as t.tg:
            for name, fn in (("pipeline", pipelines), ("status", status_loop), ("summary", summary_worker), ("voices", voices_loop), ("late", late_processor),
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
    while True:
        for d in await anyio.to_thread.run_sync(audio.pending, set(T().pipelines), limiter=threads.DB):
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


@app.exception_handler(Exception)
async def server_error(request, exc):
    # ServerErrorMiddleware is outside auth.Tenants, including its send wrapper.
    return PlainTextResponse("Internal Server Error", status_code=500, headers={"Cache-Control": "no-store"})


app.add_middleware(perf.Requests)  # inside Tenants (added after it): each request's tenant is known
app.add_middleware(auth.Tenants)
# the routers in this order: the routes match as they did when they were all in this file (test_routes_snapshot.py)
app.include_router(routes_pages.router)
app.include_router(routes_upload.router)
app.include_router(routes_api.router)
app.include_router(routes_speakers.router)
app.include_router(routes_audio.router)
app.include_router(routes_sentences.router)
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
    if "local" in engines and not importlib.util.find_spec("sherpa_onnx"):
        raise SystemExit(local_asr.NEEDS)
    if not shutil.which("ffmpeg"):  # every upload is decoded with it (audio.py)
        raise SystemExit("ffmpeg is not on PATH: brew install ffmpeg (macOS), sudo apt install ffmpeg (Debian, Ubuntu)")
    h = logging.StreamHandler()
    h.addFilter(TenantTag())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(tenant)s%(message)s", handlers=[h])
    infer.fetch()  # a first start: the missing model files, downloaded
    infer.check()  # every model file there and the pinned one, before anything starts
    log.info("models ok: %d files in %s", len(infer.MODELS), infer.DIR)
    from alldayrec.diarist import load_embedder
    # before the event loop: building the session keeps the GIL ~0.3 s, which in a thread held up the loop at each start
    state["embed"] = load_embedder()
    state["music"] = music.heard if music.ON else None
    if not tenant.FILE.exists():
        token = tenant.create()
        log.info("first start: %s made, one tenant; on this machine open http://127.0.0.1:%d/connect?token=%s",
                 tenant.FILE, args.port, token)
    core.TENANTS[:] = tenant.load()
    for t in core.TENANTS:
        tenant.call(t, db.init)
    firmware.DIR = core.TENANTS[0].data / "firmware"  # every tenant's: the first one's
    commit = HERE / "COMMIT"  # written by deploy.sh, the Docker build and the PyPI release: no git checkout there
    state["commit"] = commit.read_text().strip() if commit.exists() else \
        subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    log.info("tenants: %s", ", ".join(t.id for t in core.TENANTS))
    log.info("ready on %s:%d", os.environ.get("HOST", "127.0.0.1"), args.port)

    import uvicorn
    # a page's WebSocket would hold a stop for good: 5 s, then the lifespan cancels every task (pipelines resume from
    # their finalized time on the next start)
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=args.port, log_level="warning", access_log=False,
                timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
