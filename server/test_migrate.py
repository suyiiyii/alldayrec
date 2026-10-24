"""Run the legacy import entry against real media and today's SQLite schema."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import wave


HERE = Path(__file__).resolve().parent


def run_migration(data, recordings):
    return subprocess.run(
        [sys.executable, "-m", "alldayrec.migrate"], cwd=HERE, capture_output=True, text=True,
        env={**os.environ, "LIVE_DATA": str(data), "RECORDINGS_DIR": str(recordings)},
    )


def test_import_preserves_legacy_records_and_audio(tmp_path):
    data, recordings = tmp_path / "data", tmp_path / "recordings"
    data.mkdir()
    chunks = recordings / "chunks" / "phone"
    chunks.mkdir(parents=True)
    piece = chunks / "1000.wav"
    with wave.open(str(piece), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\0\0" * 16000)
    row = {"id": "f1", "start": "2026-01-01T10:00:00", "end": "2026-01-01T10:00:02",
           "speaker": "我", "text": "保留原文", "source": "phone"}
    conv = {"id": "c1", "start": row["start"], "end": row["end"], "participants": ["我"],
            "sentences": [row], "speech_s": 2, "device": "phone"}
    summary = {"conversation": "c1", "status": "done", "title": "旧摘要", "summary": "保留摘要"}
    files = {
        "2026-01-01.final.jsonl": json.dumps({"from": row["start"], "to": row["end"], "rows": [row]}) + "\n",
        "2026-01-01.conversations.jsonl": json.dumps(conv) + "\n",
        "summaries/c1.json": json.dumps(summary),
        "voices/live.jsonl": json.dumps({"conv": "c1", "label": "我", "n": 1, "e": [1.0, 0.0]}) + "\n",
        "voices/apart.jsonl": json.dumps(["c1|我", "someone"]) + "\n",
        "people.json": json.dumps({"我": {"n": 1, "e": [1.0, 0.0]}}),
        "grouper.json": json.dumps({"open": {"start": row["start"], "end": row["end"], "sentences": [row], "speech_s": 2}}),
    }
    for name, content in files.items():
        path = data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    original = {p: p.read_bytes() for p in [piece, *(data / name for name in files)]}
    result = run_migration(data, recordings)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(data / "live.db") as c:
        assert c.execute("SELECT device, start_ms, seconds, path, pos, size FROM pieces").fetchall() == [
            ("phone", 1000, 1.0, "chunks/phone/1000.wav", None, None)]
        assert c.execute("SELECT id, text, source FROM sentences").fetchall() == [("f1", "保留原文", "phone")]
        assert c.execute("SELECT id, sentences, device FROM conversations").fetchall() == [("c1", 1, "phone")]
        assert json.loads(c.execute("SELECT body FROM summaries").fetchone()[0]) == summary
        assert c.execute("SELECT conv, label, n, length(e), level FROM voices").fetchall() == [("c1", "我", 1, 8, None)]
        assert c.execute("SELECT voice, grp FROM apart").fetchall() == [("c1|我", "someone")]
        assert c.execute("SELECT name, n, length(e) FROM people").fetchall() == [("我", 1, 8)]
    assert all(p.read_bytes() == b for p, b in original.items())
    before = (data / "live.db").read_bytes()
    again = run_migration(data, recordings)
    assert again.returncode != 0
    assert (data / "live.db").read_bytes() == before


def test_pending_transcript_refuses_before_creating_database(tmp_path):
    pending = tmp_path / "2026-01-01.jsonl"
    pending.write_text('{"text":"pending"}\n')
    result = run_migration(tmp_path, tmp_path / "recordings")
    assert result.returncode != 0
    assert not (tmp_path / "live.db").exists()
    assert pending.read_text() == '{"text":"pending"}\n'
