"""Firmware packages for the BLE recorders, one shelf per model (LR-X1 = XIAO, LR-P1, LR-P2 and LR-P3 = ProMicro):
lr_release.py uploads a build, the app asks for the newest one of its recorder's model and sends it over Bluetooth. Kept as
DATA/firmware/<model>/<version>.zip, the .json beside it published last. A version is lr.sh's firmware revision,
YYYYMMDD.HHMMSS-<commit> (the commit's time): a later one sorts later as text. Each carries notes for the people who
own the recorder, listed with every version of the model on the app's firmware page; they can be replaced later."""
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter()
DIR = Path()  # DATA/firmware, set by server.py
TOKEN = os.environ.get("FIRMWARE_TOKEN", "")  # uploads need it on top of a tenant's token, which every phone holds; empty: no uploads
MODEL = re.compile(r"LR-[A-Z0-9]{1,8}")
VERSION = re.compile(r"\d{8}\.\d{6}-[0-9a-f]{7,40}")  # no "+": a dirty tree's build is not one commit


def check(model: str, version: str | None = None):
    if not MODEL.fullmatch(model) or version is not None and not VERSION.fullmatch(version):
        raise HTTPException(400, "model LR-<letters/digits>, version YYYYMMDD.HHMMSS-<commit> from a clean tree")


def allowed(request: Request, notes: str):
    """Writes need the firmware token, and notes the people with the recorder can read: no issue numbers
    (lr_release.py's docstring says how to write them)."""
    if not TOKEN or not hmac.compare_digest(request.headers.get("x-firmware-token", ""), TOKEN):
        raise HTTPException(403, "X-Firmware-Token required")
    if not notes.strip() or re.search(r"#\d", notes):
        raise HTTPException(400, "notes: what the people with the recorder notice, without issue numbers")


@router.post("/api/firmware")
async def upload(request: Request, model: str, version: str, commit: str, built: str, notes: str):
    """One DFU package (`lr.sh pkg`) as the body; the model is the build's own (CONFIG_BT_DIS_MODEL), read by
    lr_release.py from the build directory the package came from. A version is published once."""
    allowed(request, notes)
    check(model, version)
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not commit.startswith(version.split("-")[1]):
        raise HTTPException(400, "commit: the full hash of the version's commit")
    body = await request.body()
    try:
        names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    except zipfile.BadZipFile:
        names = []
    if "manifest.json" not in names:
        raise HTTPException(400, "not a DFU package (lr.sh pkg)")
    d = DIR / model
    if (d / f"{version}.json").exists():
        raise HTTPException(409, f"{model} {version} is uploaded already")
    meta = {"model": model, "version": version, "commit": commit, "built": built, "notes": notes, "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(), "uploaded": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{version}.zip").write_bytes(body)
    (d / f"{version}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


@router.get("/api/firmware")
async def versions(model: str):
    """A model's packages, newest first: the app's firmware page lists them with their notes."""
    check(model)
    return [json.loads(p.read_text()) for p in sorted((DIR / model).glob("*.json"), reverse=True)]


@router.patch("/api/firmware/{model}/{version}")
async def edit(request: Request, model: str, version: str, notes: str):
    """New notes for a published version (lr_release.py --version); the package stays as it is."""
    allowed(request, notes)
    check(model, version)
    p = DIR / model / f"{version}.json"
    if not p.exists():
        raise HTTPException(404, "no such firmware")
    meta = json.loads(p.read_text()) | {"notes": notes}
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


@router.get("/api/firmware/latest")
async def latest(model: str):
    """The newest package of a model (what the app compares with its recorder's revision), or 404."""
    check(model)
    js = sorted((DIR / model).glob("*.json"))
    if not js:
        raise HTTPException(404, f"no firmware for {model}")
    return json.loads(js[-1].read_text())


@router.get("/api/firmware/{model}/{version}.zip")
async def download(model: str, version: str):
    check(model, version)
    p = DIR / model / f"{version}.zip"
    if not (DIR / model / f"{version}.json").exists():
        raise HTTPException(404, "no such firmware")
    return FileResponse(p, media_type="application/zip", filename=f"{model}-{version}.zip")
