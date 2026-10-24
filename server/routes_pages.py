"""The web app's pages and its build output, and the Android app's APK."""
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import apk
import core
import threads
from core import NO_CACHE

router = APIRouter()


DIST = Path(__file__).resolve().parent / "static/dist"  # the web app (web/, built by deploy.sh)


@router.get("/")
@router.get("/live")
@router.get("/me")
@router.get("/todos")
@router.get("/conversations")
@router.get("/c/{conv_id}")
@router.get("/record")
@router.get("/speakers")
@router.get("/calendar")
@router.get("/progress")
@router.get("/connect")
@router.get("/p/{name}")
@router.get("/device/{name}")
async def page(conv_id: str = "", name: str = ""):
    return FileResponse(DIST / "index.html", headers=NO_CACHE)


@router.get("/assets/{name}")
async def asset(name: str):
    """Build output with a content hash in the name: never changes, so cached for good."""
    p = DIST / "assets" / name
    if not p.is_file():
        raise HTTPException(404, "no such asset")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/app.apk")
async def app_apk():
    """The Android client, for installing from the phone's browser."""
    p = core.TENANTS[0].data / "live-record.apk"  # every tenant's: the first one's
    if not p.exists():
        raise HTTPException(404, "no apk uploaded")
    return FileResponse(p, media_type="application/vnd.android.package-archive", filename="alldayrec.apk")


@router.get("/api/app/latest")
async def apk_latest():
    """What /app.apk is: the Android app compares versionCode with its own build number and offers the update."""
    p = core.TENANTS[0].data / "live-record.apk"  # every tenant's: the first one's
    if not p.exists():
        raise HTTPException(404, "no apk uploaded")
    return {**await anyio.to_thread.run_sync(apk.version, p, limiter=threads.DB), "size": p.stat().st_size}
