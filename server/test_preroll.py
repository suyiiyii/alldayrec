"""A recorder's pieces are slices of one running encoder (CELT, 10 ms frames, pre-skip 0, as the app's Store.oggOpus
cuts them). Decoded on its own, a piece began up to 26 dB too loud for 10-40 ms: a click at every join, each second
while the app is in front (10-08, LR-P3-2420). Two back-to-back pieces of such a stream: the second one starts as it
goes on; a piece after a gap and an iPhone microphone piece (SILK, 20 ms) decode as before."""
import datetime as dt
import subprocess

import numpy as np

from alldayrec import audio
from alldayrec import db

T = int(dt.datetime(2026, 10, 8, 1, 13).timestamp() * 1000)
lvl = lambda v: 10 * np.log10(np.mean(v.astype(np.float64) ** 2))


def encode(*opts: str) -> list[bytes]:
    """4 s of quiet pink noise (a room, about -55 dBFS; a fixed seed: the same noise every run) through one libopus
    encoder: its audio packets."""
    ogg = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=4:c=pink:r=16000:a=0.003:seed=1", "-c:a", "libopus",
                          *opts, "-f", "ogg", "pipe:1"], capture_output=True, check=True).stdout
    return audio.packets(ogg)[2:]  # OpusHead, OpusTags


def test_join(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")
    monkeypatch.setattr(audio, "DEHUM", False)  # the noise is quiet: dehum would take its mean waveform out (test_dehum)
    rec = encode("-application", "lowdelay", "-frame_duration", "10", "-b:a", "32k")  # the recorder's codec.c
    a, b = audio.ogg(rec[:200]), audio.ogg(rec[200:400])  # 2 s each, back to back
    assert audio.save("LR-P3-test", T, a, "audio/ogg") == audio.save("LR-P3-test", T + 2000, b, "audio/ogg") == 2.0
    row = lambda t: db.conn().execute("SELECT * FROM pieces WHERE start_ms = ?", (t,)).fetchone()
    x = audio.samples(row(T + 2000))
    # against the one stream the encoder made (both pieces through one decoder), not against the noise's own level: a
    # 40 ms stretch of noise is itself 2-3 dB louder or quieter than the next, which made a level check flaky (10-08)
    one = audio.decode(audio.ogg(rec[:400]))[200 * 160:]
    assert len(x) == len(one) == 200 * 160, (len(x), len(one))
    assert abs(lvl(x[:640]) - lvl(one[:640])) < 1, f"the join: first 40 ms {lvl(x[:640]) - lvl(one[:640]):+.1f} dB against one stream"
    assert np.abs(x[1280:].astype(np.int32) - one[1280:]).max() <= 2, "past the 80 ms the decoder heard first, the same samples as one stream"
    assert row(T + 2000)["rms"] == audio.health(x)[0], "the stored level is of the piece as it plays"
    cold = audio.decode(b)
    assert lvl(cold[:640]) - lvl(one[:640]) > 6, "on its own the same piece starts loud (what the check above guards against)"
    # after a gap (a lost frame, another recording): nothing to go on from, decoded as before
    assert audio.save("LR-P3-test", T + 4010, b, "audio/ogg") == 2.0
    assert np.array_equal(audio.samples(row(T + 4010)), cold)
    # the iPhone's own microphone: SILK, 20 ms frames, its own pieces untouched
    mic = encode("-application", "voip", "-frame_duration", "20", "-b:a", "16k")
    c, d = audio.ogg(mic[:50], 20), audio.ogg(mic[50:100], 20)
    assert audio.save("iPhone", T, c, "audio/ogg") == audio.save("iPhone", T + 1000, d, "audio/ogg") == 1.0
    assert np.array_equal(audio.samples(db.conn().execute("SELECT * FROM pieces WHERE device = 'iPhone' AND start_ms = ?", (T + 1000,)).fetchone()),
                          audio.decode(d))


def test_bean_join(tmp_path):
    """A Feishu bean's audio as the Android app uploads it (Bean.kt, Store.oggOpus): its units' 160-byte packets, 20 ms
    CELT, stereo, 16 kHz, one running encoder cut into pieces. ffmpeg takes them as they are (mono out); a piece that
    goes on from the one before starts as the one stream does."""
    db.open_db(tmp_path / "t.db")
    bean = encode("-ac", "2", "-application", "audio", "-frame_duration", "20", "-b:a", "64k", "-vbr", "off")
    assert {len(p) for p in bean} == {160} and {p[0] >> 3 for p in bean} == {23} and bean[0][0] & 4, "CELT WB 20 ms, stereo, CBR"
    a, b = audio.ogg(bean[:100], 20, channels=2), audio.ogg(bean[100:200], 20, channels=2)
    assert audio.save("D3200-0A1B", T, a, "audio/ogg") == audio.save("D3200-0A1B", T + 2000, b, "audio/ogg") == 2.0
    x = audio.samples(db.conn().execute("SELECT * FROM pieces WHERE start_ms = ?", (T + 2000,)).fetchone())
    one = audio.decode(audio.ogg(bean[:200], 20, channels=2))[100 * 320:]
    assert len(x) == len(one) == 100 * 320, (len(x), len(one))
    assert abs(lvl(x[:640]) - lvl(one[:640])) < 1, f"the join: first 40 ms {lvl(x[:640]) - lvl(one[:640]):+.1f} dB against one stream"
    assert lvl(audio.decode(b)[:640]) - lvl(one[:640]) > 6, "on its own the same piece starts loud"
