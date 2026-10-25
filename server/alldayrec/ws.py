"""The pages' WebSocket: its tenant's pushes (broadcast), the system log lines the page shows (note), a page's watch."""
import datetime as dt
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alldayrec.tenant import T

router = APIRouter()


async def broadcast(msg: dict):
    for ws in list(T().clients):
        try:
            await ws.send_json(msg)
        except Exception:
            T().clients.discard(ws)


def note(text: str, kind: str = "info"):
    """One line of the system log the page shows: what arrived, what each pass did, state changes."""
    e = {"type": "event", "at": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind, "text": text}
    T().events.append(e)
    del T().events[:-300]
    if T().tg:  # none: a test without the tenant's tasks, nobody to send it to
        T().tg.start_soon(broadcast, e)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Its tenant's pushes (Tenants let it in by its token)."""
    await ws.accept()
    T().clients.add(ws)
    try:
        while True:
            try:
                m = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("type") == "watch":  # a page in front: every 20 s; wake: it just came to the front
                T().watch_until = time.time() + 60
                if m.get("live"):  # the live page, not 今天's live card: short pieces from the phones (routes_upload.slices)
                    T().live_until = T().watch_until
                if m.get("wake"):
                    T().wake_at = time.time()
    except WebSocketDisconnect:
        T().clients.discard(ws)
