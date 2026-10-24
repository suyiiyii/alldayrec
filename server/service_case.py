"""Whole-file transcription + diarization of a case through the file-mode ASR service, as a sentence hypothesis.

    set -a; . .env; set +a
    uv run python service_case.py <case-dir> <model> [--job <id>]

model: stepaudio-2.5-asr-8006 | doubao-seedasr-2.0 | moss-transcribe-diarize-0.9b. Uploads the case
audio (or reuses --job), polls, saves the raw result to work/<model>.json and writes
hyp/<model>-file.jsonl: the service's utterances, speakers relabelled 我 / S<n> by the live rules
(cluster mean voiceprint vs owner, registry over the whole file).
"""
import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asr import _http  # noqa: E402
from replay_case import DEVICE_PARAMS, EPOCH, LINK_MIN, MIN_SPEAKER_S, Case, Registry, Speakers  # noqa: E402
from segmenter import SR  # noqa: E402
from transcript import final_rows  # noqa: E402

BRIDGE, SERVICE = os.environ["ASR_UPLOAD_BRIDGE_URL"], os.environ["ASR_SERVICE_URL"]


def submit(case: Case, model: str) -> str:
    up = json.loads(anyio.run(_http, BRIDGE, json.dumps({"filename": f"{case.dir.name}.ogg", "content_type": "audio/ogg"}).encode(),
                          {"Authorization": f"Bearer {os.environ['ASR_UPLOAD_BRIDGE_TOKEN']}", "Content-Type": "application/json"}))
    anyio.run(_http, up["upload_url"], (case.dir / case.meta["audio"]).read_bytes(), {"Content-Type": "audio/ogg"}, "PUT")
    auth = {"Authorization": "Basic " + base64.b64encode(os.environ["ASR_SERVICE_BASIC_AUTH"].encode()).decode()}
    r = json.loads(anyio.run(_http, f"{SERVICE}/audio/transcriptions", json.dumps({"audio_url": up["download_url"], "model": model}).encode(),
                             {**auth, "Content-Type": "application/json"}))
    print("job", r, flush=True)
    return r["job_id"]


def wait(job: str, every=30) -> dict:
    auth = {"Authorization": "Basic " + base64.b64encode(os.environ["ASR_SERVICE_BASIC_AUTH"].encode()).decode()}
    t = time.time()
    while True:
        r = json.loads(anyio.run(_http, f"{SERVICE}/jobs/{job}", None, auth))
        if r["status"] == "COMPLETED":
            print(f"completed after {time.time() - t:.0f}s", flush=True)
            return r
        if r["status"] == "FAILED":
            raise SystemExit(f"job failed: {r.get('error')}")
        print(f"  {r['status']} {time.time() - t:.0f}s", flush=True)
        time.sleep(every)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=Path)
    ap.add_argument("model")
    ap.add_argument("--job")
    a = ap.parse_args()
    case = Case(a.case)
    raw_path = case.work / f"{a.model}.json"
    if raw_path.exists():
        raw = json.load(open(raw_path))
    else:
        raw = wait(a.job or submit(case, a.model))
        json.dump(raw, open(raw_path, "w"), ensure_ascii=False)
    segs = raw["segments"]
    print(len(segs), "segments,", len({g['speaker'] for g in segs}), "service speakers")
    spk, reg = Speakers(), Registry()
    names, score, means, secs = spk.label(segs, case.x, case.meta["device"])
    link = DEVICE_PARAMS.get(case.meta["device"], {}).get("link", LINK_MIN)
    labels = {}
    for k, who in names.items():
        if who == "我":
            labels[k] = "我"
        elif k in means and secs[k] >= MIN_SPEAKER_S:
            labels[k] = reg.assign(means[k], secs[k], link)[0]
        else:
            labels[k] = "S?"
    print("owner scores", {k: round(v, 3) for k, v in score.items()}, "labels", labels)
    _, _, rows = final_rows(segs, labels, EPOCH, len(case.x) / SR + 10)
    out = case.dir / "hyp" / f"{a.model}-file.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        for r in rows:
            s0 = (dt.datetime.fromisoformat(r["start"]) - EPOCH).total_seconds()
            e0 = (dt.datetime.fromisoformat(r["end"]) - EPOCH).total_seconds()
            f.write(json.dumps({**r, "start": case.wall(s0).isoformat(timespec="milliseconds"),
                                "end": case.wall(e0).isoformat(timespec="milliseconds")}, ensure_ascii=False) + "\n")
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
