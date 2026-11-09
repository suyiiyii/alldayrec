"""alldayrec-infer: the server's heavy pure computations (compute.Local) as a small HTTP service, for INFER_URL.

    INFER_TOKEN=... alldayrec-infer [--port 8771]          (or python -m alldayrec.infer_server)

POST /embed and /music: the body is int16 little-endian 16 kHz samples (application/octet-stream, at most MAX_S s),
with "Authorization: Bearer $INFER_TOKEN"; the answer {"embedding": [192 floats]} or {"music": bool}.
GET /health: {"ok": true}, with the token also its model files' sha256 and compute.DIM (the server checks them at
start; a wrong token: 401); GET /metrics (token): Prometheus text. It keeps nothing and knows no tenant: no database,
no audio files, no tenants.json; only MODELS_DIR, which it fills as the server does (infer.fetch). infer.py imports
alldayrec.tenant for MODELS_DIR's default (DATA_DIR/models), only that path. INFER_HOST (127.0.0.1) and INFER_PORT
(8771): its own, so it never takes the server's HOST and PORT from a shared .env. INFER_CALLS and INFER_THREADS as for
the server: calls at once, onnxruntime threads a call.
"""
import argparse
import hmac
import logging
import os
import time
from collections import Counter

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # as server.py, before numpy loads

import anyio.to_thread
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from alldayrec import compute
from alldayrec import infer
from alldayrec import threads

MAX_S = 600  # a voiceprint takes at most 15 s of a sentence and a few enrollment samples; this bounds a request's memory
SR = 16000
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
state = {"compute": None, "token": ""}  # main() sets them; tests put their own
CALLS, SECONDS, AUDIO_S = Counter(), Counter(), Counter()  # (op, result) -> calls; op -> compute seconds, audio seconds


def authorized(request: Request) -> bool:
    return bool(state["token"]) and hmac.compare_digest(request.headers.get("authorization", "").encode(),
                                                        f"Bearer {state['token']}".encode())


async def run(op: str, request: Request, f):
    if not authorized(request):
        CALLS[op, "401"] += 1
        return JSONResponse({"error": "unauthorized"}, 401)
    over = JSONResponse({"error": f"over {MAX_S} s"}, 413)
    if int(request.headers.get("content-length") or 0) > MAX_S * SR * 2:
        CALLS[op, "413"] += 1
        return over
    body = bytearray()
    async for chunk in request.stream():  # counted as it comes: a chunked body without a length stops at the limit too
        body += chunk
        if len(body) > MAX_S * SR * 2:
            CALLS[op, "413"] += 1
            return over
    if not body or len(body) % 2:
        CALLS[op, "400"] += 1
        return JSONResponse({"error": "the body is int16 samples"}, 400)
    x = np.frombuffer(body, "<i2")
    t = time.perf_counter()
    try:
        out = await anyio.to_thread.run_sync(f, x, limiter=threads.INFER)
    except Exception:
        CALLS[op, "500"] += 1
        raise
    SECONDS[op] += time.perf_counter() - t
    AUDIO_S[op] += len(x) / SR
    CALLS[op, "ok"] += 1
    return out


@app.post("/embed")
async def embed(request: Request):
    return await run("embed", request, lambda x: {"embedding": state["compute"].embed(x).tolist()})


@app.post("/music")
async def music_(request: Request):
    return await run("music", request, lambda x: {"music": bool(state["compute"].music(x))})


@app.get("/health")
async def health(request: Request):
    if "authorization" not in request.headers:
        return {"ok": True}
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    return {"ok": True, "models": compute.models(), "dim": compute.DIM}


@app.get("/metrics")
async def metrics(request: Request):
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    out = [f'lr_infer_server_calls_total{{op="{op}",result="{r}"}} {n}' for (op, r), n in sorted(CALLS.items())]
    out += [f'lr_infer_server_seconds_total{{op="{op}"}} {v:.3f}' for op, v in sorted(SECONDS.items())]
    out += [f'lr_infer_server_audio_seconds_total{{op="{op}"}} {v:.3f}' for op, v in sorted(AUDIO_S.items())]
    return PlainTextResponse("\n".join(out) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=int(os.environ.get("INFER_PORT", "8771")))
    args = p.parse_args()
    if not os.environ.get("INFER_TOKEN"):
        raise SystemExit("not set: INFER_TOKEN (the server sends the same one)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    infer.fetch()
    infer.check()
    state["compute"], state["token"] = compute.Local(), os.environ["INFER_TOKEN"]
    host = os.environ.get("INFER_HOST", "127.0.0.1")
    logging.getLogger(__name__).info("ready on %s:%d", host, args.port)
    import uvicorn
    uvicorn.run(app, host=host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
