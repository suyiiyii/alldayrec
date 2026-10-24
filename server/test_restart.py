"""A new instance has nobody's voiceprint yet: its first device is transcribed all the same, nobody labelled 我. A
pipeline that fails (here a write, once) starts again whole RESTART_S later, from the finalized time, without leaving
its readers behind: every sentence final once (10-09: a missing voiceprint ended the pipeline until the next restart)."""
import datetime as dt
import time

import anyio
import numpy as np

import core
import db
import server
from tenant import T
from test_backlog import DEVICE, PIECE_S, SR, flac, hear, rig, save, tone


def test_no_voiceprint_and_restart(tmp_path, monkeypatch):
    rig(tmp_path, monkeypatch, hear)
    (tmp_path / "owner-redimnet2.npz").unlink()
    monkeypatch.setattr(server, "RESTART_S", 0.5)
    replace, failed = db.replace_sentences, []

    def once(c, lo, hi, rows, source):  # the write of the piece with tone 2 fails the first time
        if any(r["text"] == "2" for r in rows) and not failed:
            failed.append(lo)
            raise RuntimeError("disk full")
        return replace(c, lo, hi, rows, source)
    monkeypatch.setattr(db, "replace_sentences", once)
    silence = np.zeros(PIECE_S * SR, np.int16)
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 10 * PIECE_S)
    for i in range(3):
        save(t0 + dt.timedelta(seconds=PIECE_S * i), flac(np.concatenate([silence[:10 * SR], tone(i, 3), silence[13 * SR:]])))
    finals = lambda: db.sentences(core.iso(t0), core.iso(t0 + dt.timedelta(seconds=3 * PIECE_S)), source=DEVICE)

    async def run():
        dias = len(T().dias)
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.lasting, DEVICE)
            for _ in range(120):
                if len(finals()) >= 3:
                    break
                await anyio.sleep(0.5)
            rows = finals()
            assert [r["text"] for r in rows] == ["0", "1", "2"], rows
            assert failed, "the write was to fail once"
            assert "我" not in {r["speaker"] for r in rows}, rows
            assert [t.name for t in anyio.get_running_tasks()].count(f"feed:{DEVICE}") == 1, "the failed pipeline's reader is gone"
            assert len(T().dias) == dias + 2, "the failed pipeline's diarists are gone"
            tg.cancel_scope.cancel()

    anyio.run(run)
