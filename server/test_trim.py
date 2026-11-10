"""trim.py on local hour files: an old, transcribed hour keeps only its pieces near speech (the VAD's, or a
sentence's), byte for byte; playback of what is kept does not change and what went is silence; crash-safe, never
twice, one apply at a time, the original deleted only by purge, past its gates. Real store and VAD stream;
test_backlog's energy VAD."""
import datetime as dt
import io
import json
import subprocess
import sys
import threading
import time
import zlib

import numpy as np
import pytest

from alldayrec import audio
from alldayrec import db
from alldayrec import infer
from alldayrec import store
from alldayrec import tenant
from alldayrec import trim
from alldayrec.tenant import T
from test_backlog import DEVICE, SR, EnergyVad, flac, save, tone

N, PIECE_S, TALK = 30, 10, (3, 20)  # 30 pieces of 10 s, a tone in pieces 3 and 20, silence elsewhere


def hour(tmp_path, monkeypatch, age_days=3):
    """The hour, uploaded age_days ago, finalized; its VAD (vad's output, parsed); the audio."""
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    db.open_db(tmp_path / "live.db", tmp_path / "rec")
    t0 = dt.datetime.fromtimestamp(time.time() - age_days * 86400).replace(minute=0, second=0, microsecond=0)
    x = np.zeros(N * PIECE_S * SR, np.int16)
    for i in TALK:
        x[(i * PIECE_S + 2) * SR:(i * PIECE_S + 6) * SR] = tone(i, 4)
    for i in range(N):
        save(t0 + dt.timedelta(seconds=i * PIECE_S), flac(x[i * PIECE_S * SR:(i + 1) * PIECE_S * SR]))
    db.conn().execute("UPDATE pieces SET received_ms = start_ms")  # as uploaded then
    db.put_kv(f"finalized_to:{DEVICE}", (t0 + dt.timedelta(hours=1)).isoformat())
    return t0, x


def vad() -> dict:
    out = io.StringIO()
    trim.vad((0, 1), int(time.time() * 1000), out)
    return {v["path"]: v for v in map(json.loads, out.getvalue().splitlines())}


def at(t0, s):
    return t0 + dt.timedelta(seconds=s)


def purge(yes=True):
    """purge a day and an hour later, told of a backup begun then."""
    t = int(time.time() * 1000) + 25 * 3600_000
    return trim.purge(t, t, yes)


def kept_pieces(t0):
    return sorted(round((r[0] / 1000 - t0.timestamp()) / PIECE_S) for r in db.conn().execute("SELECT start_ms FROM pieces"))


def test_trim_keeps_speech_and_its_pad(tmp_path, monkeypatch):
    t0, x = hour(tmp_path, monkeypatch)
    path = str(audio.hour_file(DEVICE, int(t0.timestamp() * 1000), "bin"))
    before = {s: audio.cut(at(t0, s), at(t0, s + 30), "s16le", DEVICE) for s in (15, 195)}
    original = (T().recordings / path).read_bytes()
    v = vad()
    assert list(v) == [path] and len(v[path]["speech"]) == 2 and v[path]["fed"] == [[int(t0.timestamp() * 1000), int(t0.timestamp() * 1000) + N * PIECE_S * 1000]]
    hours = trim.plan(v, int(time.time() * 1000))
    assert (T().recordings / path).read_bytes() == original and len(kept_pieces(t0)) == N, "plan changes nothing"
    assert trim.report(hours)["reasons"] == {"trim": [1, len(original)]}
    assert trim.apply_one(hours[0])
    k = kept_pieces(t0)
    assert {2, 3, 4, 19, 20, 21} <= set(k) and not set(k) & {0, *range(8, 16), *range(25, 30)}, k  # tone ± 10 s, in whole pieces
    rows = db.conn().execute("SELECT * FROM pieces ORDER BY start_ms").fetchall()
    assert {r["path"] for r in rows} == {trim.copy_of(path)} and (T().recordings / path).read_bytes() == original, "original kept till purge"
    for s, cut in before.items():
        assert audio.cut(at(t0, s), at(t0, s + 30), "s16le", DEVICE) == cut, "what is kept plays as before"
    gap = np.frombuffer(audio.cut(at(t0, 100), at(t0, 120), "s16le", DEVICE) or b"", np.int16)
    assert not len(gap) or not gap.any(), "what went is silence"
    t = db.conn().execute("SELECT * FROM trims").fetchone()
    assert (t["state"], t["pieces"], t["kept"], t["size"], t["kept_size"]) == ("trimmed", N, len(k), len(original), (T().recordings / trim.copy_of(path)).stat().st_size)
    old = json.loads(zlib.decompress(t["rows"]))
    assert len(old) == N and {r["path"] for r in old} == {path} and all(original[r["pos"]:r["pos"] + r["size"]] for r in old), "the rows as they were"
    # never twice: the next plan leaves it, and the copy is no hour file
    assert {h["path"]: h["reason"] for h in trim.plan(vad() | v, int(time.time() * 1000))} == {trim.copy_of(path): "not an hour file"}
    assert purge() == {"purged": 1, "kept": 0, "waiting": 0} and not (T().recordings / path).exists()
    assert db.conn().execute("SELECT state FROM trims").fetchone()[0] == "purged"
    for s, cut in before.items():
        assert audio.cut(at(t0, s), at(t0, s + 30), "s16le", DEVICE) == cut, "and after the purge"


@pytest.mark.parametrize("why", ["recent", "not transcribed", "late span open", "no VAD", "being sealed"])
def test_left_whole(tmp_path, monkeypatch, why):
    t0, _ = hour(tmp_path, monkeypatch, age_days=1 if why == "recent" else 3)
    v = vad() if why not in ("recent", "no VAD") else {}
    if why == "not transcribed":
        db.put_kv(f"finalized_to:{DEVICE}", at(t0, 200).isoformat())
    if why == "late span open":
        db.conn().execute("INSERT INTO late_spans (device, start_ms, end_ms, first_ms, last_ms, ranges) VALUES (?, ?, ?, 0, 0, '[]')",
                          (DEVICE, int(at(t0, 100).timestamp() * 1000), int(at(t0, 110).timestamp() * 1000)))
    if why == "being sealed":  # STORAGE=s3 and a late piece came: the hour file is on its way into an object
        path = db.conn().execute("SELECT path FROM pieces").fetchone()[0]
        db.conn().execute("INSERT INTO store_objects (path, lo_ms, hi_ms, state) VALUES (?, 0, 0, 'open')", (path,))
    assert [h["reason"] for h in trim.plan(v, int(time.time() * 1000))] == [why]


def test_two_days_by_default(tmp_path, monkeypatch):
    t0, _ = hour(tmp_path, monkeypatch, age_days=2.2)
    assert trim.TRIM_AFTER_DAYS == 2 and [h["reason"] for h in trim.plan(vad(), int(time.time() * 1000))] == [None]


def test_speech_throughout_is_left_alone(tmp_path, monkeypatch):
    t0, _ = hour(tmp_path, monkeypatch)
    monkeypatch.setattr(trim, "TRIM_PAD_S", 200)
    h = trim.plan(vad(), int(time.time() * 1000))[0]
    assert len(h["kept"]) == N and trim.apply_one(h)
    assert db.conn().execute("SELECT state FROM trims").fetchone()[0] == "whole" and len(kept_pieces(t0)) == N
    assert not (T().recordings / trim.copy_of(h["path"])).exists() and purge() == {"purged": 0, "kept": 0, "waiting": 0}


def test_sentence_keeps_what_the_vad_missed(tmp_path, monkeypatch):
    t0, _ = hour(tmp_path, monkeypatch)
    db.conn().execute("INSERT INTO sentences (id, start, \"end\", text, source) VALUES ('s', ?, ?, '嗯', ?)",
                      (at(t0, 121).isoformat(timespec="milliseconds"), at(t0, 122).isoformat(timespec="milliseconds"), DEVICE))
    assert trim.apply_one(trim.plan(vad(), int(time.time() * 1000))[0])
    assert {11, 12, 13} <= set(kept_pieces(t0)), "a sentence ± 10 s"


def test_unheard_pieces_kept(tmp_path, monkeypatch):
    """A piece the VAD did not hear (it could not decode it, or it came after the VAD ran) is kept."""
    t0, _ = hour(tmp_path, monkeypatch)
    v = vad()
    for x in v.values():
        x["fed"] = [[a, a + 100_000] for a, _ in x["fed"]]  # heard only the first 100 s
    assert trim.apply_one(trim.plan(v, int(time.time() * 1000))[0])
    assert set(range(10, N)) <= set(kept_pieces(t0)) and 0 not in kept_pieces(t0)


def test_piece_since_the_vad_kept(tmp_path, monkeypatch):
    """A longer resend of a piece after the VAD ran, within time it heard: its audio may differ (a live piece with
    dropouts, then the card's), so it is kept; a VAD file that does not say which pieces it was given is not used."""
    t0, x = hour(tmp_path, monkeypatch)
    v = vad()
    save(at(t0, 120), flac(x[120 * SR:130 * SR + SR // 2]))  # 10.5 s, more bytes: replaces piece 12's row
    db.conn().execute("UPDATE pieces SET received_ms = start_ms")  # as if days ago, transcribed
    db.conn().execute("UPDATE late_spans SET status = 'done'")
    (h,) = trim.plan(v, int(time.time() * 1000))
    assert 12 in {round((r["start_ms"] / 1000 - t0.timestamp()) / PIECE_S) for r in h["kept"]} and len(h["kept"]) < N
    assert [h["reason"] for h in trim.plan({p: {k: y for k, y in w.items() if k != "pieces"} for p, w in v.items()}, int(time.time() * 1000))] == ["no VAD"]


def test_changed_meanwhile_is_left(tmp_path, monkeypatch):
    """A piece into the hour between the plan and its transaction (a longer resend): nothing changes, the copy goes
    at the next apply, and that one trims it."""
    t0, x = hour(tmp_path, monkeypatch)
    v = vad()
    h = trim.plan(v, int(time.time() * 1000))[0]
    write = trim.write_copy

    def resend(*a):
        write(*a)
        save(at(t0, 100), flac(x[100 * SR:111 * SR]))  # 11 s, longer than the 10 s kept: replaces the row
    monkeypatch.setattr(trim, "write_copy", resend)
    assert not trim.apply_one(h)
    monkeypatch.setattr(trim, "write_copy", write)
    assert {r[0] for r in db.conn().execute("SELECT path FROM pieces")} == {h["path"]} and db.conn().execute("SELECT count(*) FROM pieces").fetchone()[0] == N
    assert (T().recordings / trim.copy_of(h["path"])).exists() and db.conn().execute("SELECT state FROM trims").fetchone()[0] == "writing"
    trim.leftovers()
    assert not (T().recordings / trim.copy_of(h["path"])).exists() and not db.conn().execute("SELECT 1 FROM trims").fetchone()


@pytest.mark.parametrize("where", ["copy", "transaction"])
def test_crash(tmp_path, monkeypatch, where):
    """Killed while writing the copy or in the transaction: the hour reads as before; the next apply trims it."""
    t0, _ = hour(tmp_path, monkeypatch)
    before = audio.cut(at(t0, 15), at(t0, 45), "s16le", DEVICE)
    v = vad()

    def die(*a):
        if where == "copy":  # half of it on disk
            open(T().recordings / a[1], "ab").write(b"OggS half")
        raise KeyboardInterrupt
    # the copy's write, or the transaction after its rows were moved (its last statement's arguments)
    monkeypatch.setattr(*(store.Local, "append", die) if where == "copy" else (trim, "snapshot", die))
    with pytest.raises(KeyboardInterrupt):
        trim.apply_one(trim.plan(v, int(time.time() * 1000))[0])
    monkeypatch.undo()
    assert len(kept_pieces(t0)) == N and audio.cut(at(t0, 15), at(t0, 45), "s16le", DEVICE) == before
    trim.leftovers()
    h = trim.plan(v, int(time.time() * 1000))[0]
    assert h["reason"] is None and trim.apply_one(h) and len(kept_pieces(t0)) < N
    assert audio.cut(at(t0, 15), at(t0, 45), "s16le", DEVICE) == before


def test_purge_keeps_what_changed(tmp_path, monkeypatch):
    """An original written again since (a late piece appended to it, its row there) is not deleted."""
    t0, x = hour(tmp_path, monkeypatch)
    h = trim.plan(vad(), int(time.time() * 1000))[0]
    assert trim.apply_one(h)
    save(at(t0, 305), flac(x[:SR]))  # a late piece: into the original hour file again
    assert purge() == {"purged": 0, "kept": 1, "waiting": 0} and (T().recordings / h["path"]).exists()


def test_one_apply_at_a_time(tmp_path, monkeypatch):
    """Another apply's leftovers() while this one writes its copy: this one does not commit (its rows never point at
    the copy deleted), and the lock keeps a second apply or purge of the tenant from starting at all."""
    t0, _ = hour(tmp_path, monkeypatch)
    before = audio.cut(at(t0, 25), at(t0, 45), "s16le", DEVICE)
    h = trim.plan(vad(), int(time.time() * 1000))[0]
    write = trim.write_copy

    def other(*a):
        write(*a)
        trim.leftovers()
    monkeypatch.setattr(trim, "write_copy", other)
    assert not trim.apply_one(h)
    monkeypatch.setattr(trim, "write_copy", write)
    assert {r[0] for r in db.conn().execute("SELECT path FROM pieces")} == {h["path"]} and not (T().recordings / trim.copy_of(h["path"])).exists()
    assert not db.conn().execute("SELECT 1 FROM trims").fetchone() and purge() == {"purged": 0, "kept": 0, "waiting": 0}
    assert audio.cut(at(t0, 25), at(t0, 45), "s16le", DEVICE) == before
    with trim.lock():
        with pytest.raises(SystemExit):
            with trim.lock():
                pass
    with trim.lock():  # let go
        pass


def test_older_plan_leaves_a_trim(tmp_path, monkeypatch):
    """A second apply with the plan made before the first one's trim: the trim, its rows as they were and its copy stay."""
    t0, _ = hour(tmp_path, monkeypatch)
    h = trim.plan(vad(), int(time.time() * 1000))[0]
    assert trim.apply_one(dict(h))
    copy = T().recordings / trim.copy_of(h["path"])
    st = copy.stat()
    assert not trim.apply_one(dict(h))
    assert tuple(db.conn().execute("SELECT state, rows IS NOT NULL FROM trims").fetchone()) == ("trimmed", 1)
    assert (copy.stat().st_ino, copy.stat().st_mtime_ns) == (st.st_ino, st.st_mtime_ns)


def test_purge_gates(tmp_path, monkeypatch):
    """No original goes without a backup begun after its trim, sooner than TRIM_PURGE_AFTER_H, without --yes, or with
    its copy gone or changed."""
    t0, _ = hour(tmp_path, monkeypatch)
    h = trim.plan(vad(), int(time.time() * 1000))[0]
    assert trim.apply_one(h)
    original, copy = T().recordings / h["path"], T().recordings / trim.copy_of(h["path"])
    now, at_ = int(time.time() * 1000), db.conn().execute("SELECT at FROM trims").fetchone()[0]
    day = now + 25 * 3600_000
    waiting = {"purged": 0, "kept": 0, "waiting": 1}
    assert trim.purge(day, None, True) == waiting, "no backup"
    assert trim.purge(day, at_ - 1, True) == waiting, "a backup begun before the trim"
    assert trim.purge(now, now, True) == waiting and trim.TRIM_PURGE_AFTER_H == 24, "a day after the trim"
    assert purge(yes=False) == {"to purge": 1, "kept": 0, "waiting": 0} and original.exists(), "only says what would go"
    good = copy.read_bytes()
    copy.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
    assert purge() == {"purged": 0, "kept": 1, "waiting": 0} and original.exists(), "the copy is not the bytes written"
    copy.unlink()
    assert purge() == {"purged": 0, "kept": 1, "waiting": 0} and original.exists(), "the copy is gone"
    copy.write_bytes(good)
    assert purge() == {"purged": 1, "kept": 0, "waiting": 0} and not original.exists()
    assert abs(db.conn().execute("SELECT backup FROM trims").fetchone()[0] - day) < 1000


def test_purge_holds_off_a_late_piece(tmp_path, monkeypatch):
    """A late piece coming into the original while purge checks it: its append waits for the purge (the lock every
    append takes), then goes into a new hour file of that name, and plays."""
    t0, x = hour(tmp_path, monkeypatch)
    assert trim.apply_one(trim.plan(vad(), int(time.time() * 1000))[0])
    path = str(audio.hour_file(DEVICE, int(t0.timestamp() * 1000), "bin"))
    me, sha, appending, late, waits = T(), store.file_sha, store.appending, [], threading.Event()

    def save_late():
        tenant.enter(me)
        late.append(save(at(t0, 305), flac(x[20 * PIECE_S * SR:(20 * PIECE_S + 4) * SR])))  # the tone again, 4 s

    def checking(h, size):
        if h.name == str(T().recordings / path) and not th.is_alive() and not late:
            th.start()
            assert waits.wait(10)
            th.join(0.3)
            assert th.is_alive(), "the append waits for the purge"
        return sha(h, size)

    def append_to(f):
        waits.set()
        return appending(f)
    th = threading.Thread(target=save_late)
    monkeypatch.setattr(store, "file_sha", checking)
    monkeypatch.setattr(store, "appending", append_to)
    assert purge() == {"purged": 1, "kept": 0, "waiting": 0}
    th.join(10)
    assert late == [4.0]
    assert [tuple(r) for r in db.conn().execute("SELECT pos FROM pieces WHERE path = ?", (path,))] == [(0,)], "a new file"
    assert np.frombuffer(audio.cut(at(t0, 305), at(t0, 309), "s16le", DEVICE), np.int16).any()


def test_vad_of_another_threshold_and_null_finalized(tmp_path, monkeypatch):
    t0, _ = hour(tmp_path, monkeypatch)
    v = vad()
    assert all(x["thr"] == trim.TRIM_VAD for x in v.values())
    assert [h["reason"] for h in trim.plan({p: x | {"thr": 0.5} for p, x in v.items()}, int(time.time() * 1000))] == ["VAD of another TRIM_VAD"]
    db.put_kv(f"finalized_to:{DEVICE}", None)
    assert [h["reason"] for h in trim.plan(v, int(time.time() * 1000))] == ["not transcribed"]


REC = "LR-P3-trim"


def recorder_hour(tmp_path, monkeypatch, loud, skip=()):
    """60 1 s pieces of one recorder encoder (its codec.c: 10 ms CELT frames, pre-skip 0 as the app cuts them), quiet
    room noise, a tone over each (a, b) of loud, the pieces in skip never uploaded (a reconnect); days old, finalized."""
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    db.open_db(tmp_path / "live.db", tmp_path / "rec")
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(minute=5, second=0, microsecond=0)
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=60:c=pink:r=16000:a=0.003:seed=1",
                                      "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"], capture_output=True, check=True).stdout, np.int16).copy()
    for a, b in loud:
        x[a * SR:b * SR] = tone(a, b - a)
    ogg = subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0", "-c:a", "libopus", "-application",
                          "lowdelay", "-frame_duration", "10", "-b:a", "32k", "-f", "ogg", "pipe:1"], input=x.tobytes(), capture_output=True, check=True).stdout
    ps = audio.packets(ogg)[2:]
    for i in set(range(60)) - set(skip):
        assert audio.save(REC, int(t0.timestamp() * 1000) + i * 1000, audio.ogg(ps[i * 100:(i + 1) * 100]), "audio/ogg")
    db.conn().execute("UPDATE pieces SET received_ms = start_ms")
    db.conn().execute("UPDATE late_spans SET status = 'done'")
    db.put_kv(f"finalized_to:{REC}", (t0 + dt.timedelta(hours=1)).isoformat())
    return t0


def test_recorder_kept_stretch_starts_quiet(tmp_path, monkeypatch):
    """A tone at 30-32 s: the first kept piece, decoded without the dropped one before it, starts faded in (no click),
    and from the second on it plays as before, sample for sample."""
    t0 = recorder_hour(tmp_path, monkeypatch, [(30, 32)])
    before = np.frombuffer(audio.cut(at(t0, 15), at(t0, 50), "s16le", REC), np.int16)
    (h,) = trim.plan(vad(), int(time.time() * 1000))
    assert trim.apply_one(h)
    k = sorted(round(r[0] / 1000 - t0.timestamp()) for r in db.conn().execute("SELECT start_ms FROM pieces"))
    first, last = k[0], k[-1]
    assert k == list(range(first, last + 1)), "one stretch kept"
    after = np.frombuffer(audio.cut(at(t0, 15), at(t0, 50), "s16le", REC), np.int16)
    i, j = round((first - 15 + 0.2) * SR), round((last + 1 - 15 + 0.2) * SR)
    assert 15 < first < 30 and not after[i:i + audio.COLD_MS * SR // 1000].any(), "muted where the cold decoder clicks"
    lvl = lambda v: 10 * np.log10(np.mean(v.astype(np.float64) ** 2) + 1e-9)
    assert lvl(after[i:i + SR // 10]) <= lvl(before[i:i + SR // 10]) + 1, "no louder than before"
    assert np.array_equal(after[i + SR:j], before[i + SR:j]), "the second kept piece on: as before"


def test_recorder_real_gap_in_speech_plays_as_before(tmp_path, monkeypatch):
    """A tone over 28-40 s and piece 33 never came (a reconnect): piece 34 starts cold before the trim and after it,
    and it is not muted (its speech plays as it did); only the start of the kept stretch, after a dropped piece, is."""
    t0 = recorder_hour(tmp_path, monkeypatch, [(28, 40)], skip={33})
    before = np.frombuffer(audio.cut(at(t0, 30), at(t0, 38), "s16le", REC), np.int16)
    (h,) = trim.plan(vad(), int(time.time() * 1000))
    assert trim.apply_one(h)
    k = sorted(round(r[0] / 1000 - t0.timestamp()) for r in db.conn().execute("SELECT start_ms FROM pieces"))
    cold = [round(r[0] / 1000 - t0.timestamp()) for r in db.conn().execute("SELECT start_ms FROM cold_starts")]
    assert 34 in k and cold == [k[0]], (k, cold)
    i = round((34 - 30 + 0.2) * SR)
    assert np.abs(before[i:i + SR // 25]).max() > 1000, "speech in the first 40 ms after the gap"
    assert np.array_equal(np.frombuffer(audio.cut(at(t0, 30), at(t0, 38), "s16le", REC), np.int16), before)


def test_coverage_slack(tmp_path, monkeypatch):
    """fed is in whole ms and a piece's seconds rounded to 1 ms: a VAD that heard a piece to within 1 ms heard it."""
    hour(tmp_path, monkeypatch)
    v = vad()
    kept = lambda v: {r["start_ms"] for r in trim.plan(v, int(time.time() * 1000))[0]["kept"]}
    assert kept({p: x | {"fed": [[a, b - 1] for a, b in x["fed"]]} for p, x in v.items()}) == kept(v)


@pytest.mark.parametrize("argv", [["apply", "--db", "x.db", "--recordings", "r", "--vad", "v"], ["apply", "--tenant", "main", "--db", "x.db", "--vad", "v"],
                                  ["purge", "--tenant", "main", "--recordings", "r", "--confirm-backup", "2026-01-01T00:00"],
                                  ["purge", "--tenant", "main"], ["apply", "--tenant", "main", "--vad", "v", "--now", "2030-01-01T00:00"]])
def test_cli_refuses(monkeypatch, argv):
    """apply and purge: a tenant only (no --db, --recordings), purge a backup, apply no --now (the 2-day gate)."""
    monkeypatch.setattr(sys, "argv", ["trim", *argv])
    with pytest.raises(SystemExit) as e:
        trim.main()
    assert e.value.code == 2


def test_purge_after_at_least_a_day(tmp_path, monkeypatch):
    hour(tmp_path, monkeypatch)
    assert trim.apply_one(trim.plan(vad(), int(time.time() * 1000))[0])
    monkeypatch.setattr(trim, "TRIM_PURGE_AFTER_H", 0)  # TRIM_PURGE_AFTER_H=0 in the environment
    with pytest.raises(SystemExit):
        purge()
    assert db.conn().execute("SELECT state FROM trims").fetchone()[0] == "trimmed"
