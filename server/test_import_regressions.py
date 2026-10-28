"""Real no-audio inputs and windowed playback regressions; synthetic data only."""
import datetime as dt
import errno
import subprocess
import sys
import tempfile
import time

import anyio
import numpy as np
import pytest

from alldayrec import audio, db
from test_import_memory import client, wav_file

START = 1700000000000


def legacy_cut(a, b, fmt, device=None):
    a_ms, b_ms = int(a.timestamp() * 1000), int(b.timestamp() * 1000)
    pieces = db.conn().execute(
        'SELECT * FROM pieces WHERE start_ms <= ? AND start_ms + seconds * 1000 >= ? '
        'AND (? IS NULL OR device = ?) ORDER BY start_ms', (b_ms, a_ms, device, device)).fetchall()
    xs, end = [], {}
    for p in pieces:
        if got := audio.fresh(p, end.get(p['device'], 0.0)):
            end[p['device']] = got[0] + len(got[1]) * 1000 / audio.SR
            xs.append(got)
    if not xs:
        return None
    i = int(max(0.0, (a_ms - xs[0][0]) / 1000 - 0.2) * audio.SR)
    x = np.concatenate([x for _, x in xs])[i:i + int(((b - a).total_seconds() + 0.4) * audio.SR)]
    return None if not len(x) else x.tobytes() if fmt == 's16le' else audio.wav(x)


@pytest.mark.parametrize('kind', ['mp4', 'png'])
def test_no_audio_is_400_and_quarantined(tmp_path, kind):
    t = db.open_db(tmp_path / 'live.db')
    path = tmp_path / f'no-audio.{kind}'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=16x16:d=0.1',
                    '-frames:v', '1', '-an', '-y', str(path)], check=True)
    body = path.read_bytes()
    r = client(t).post(f'/api/chunks?device=fixture&start={START}', content=body)
    assert r.status_code == 400 and r.json()['detail'] == 'could not decode'
    assert (t.recordings / 'rejected' / 'fixture' / f'{START}.bin').read_bytes() == body
    assert db.conn().execute('SELECT count(*) FROM pieces').fetchone()[0] == 0


@pytest.mark.parametrize('code', [errno.ENOSPC, errno.EDQUOT, errno.EIO])
def test_pcm_write_errno_is_retryable(tmp_path, monkeypatch, code):
    t = db.open_db(tmp_path / 'live.db')
    path = tmp_path / 'valid.wav'
    wav_file(path, 1)
    original = audio.decode_process
    class BrokenOutput:
        def write(self, data):
            raise OSError(code, 'fixture write failed')
    def fail_write(args, output):
        return original(args, BrokenOutput())
    monkeypatch.setattr(audio, 'decode_process', fail_write)
    r = client(t).post(f'/api/chunks?device=fixture&start={START}', content=path.read_bytes())
    assert r.status_code == 503 and 'disk space' in r.json()['detail']
    assert not (t.recordings / 'rejected').exists()
    assert db.conn().execute('SELECT count(*) FROM pieces').fetchone()[0] == 0
    monkeypatch.setattr(audio, 'decode_process', original)
    assert client(t).post(f'/api/chunks?device=fixture&start={START}', content=path.read_bytes()).status_code == 200


def test_decode_timeout_has_own_http_error(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / 'live.db')
    def timeout(*args):
        raise subprocess.TimeoutExpired('ffmpeg', 300)
    monkeypatch.setattr(audio, 'save_file', timeout)
    r = client(t).post(f'/api/chunks?device=fixture&start={START}', content=b'fixture')
    assert r.status_code == 504
    assert 'decoding timed out' in r.json()['detail'] and 'disk' not in r.json()['detail']
    assert not (t.recordings / 'rejected').exists()


def app_client(t, monkeypatch):
    from starlette.testclient import TestClient
    from alldayrec import core, server
    monkeypatch.setattr(core, 'TENANTS', [t])  # one tenant without a token: the app's own handlers and middlewares, no auth
    return TestClient(server.app)


def stop_decoder(monkeypatch, stop):
    """The real decode_process on a stand-in ffmpeg that writes some PCM, then is stopped by `stop`."""
    original = audio.decode_process
    monkeypatch.setattr(audio, 'decode_process', lambda args, output: original(
        [sys.executable, '-c', f'import os, signal, sys; os.write(1, bytes(3200)); {stop}'], output))


# a signal in ffmpeg's first ms kills it; later its own handler exits 255, with part of the PCM written
@pytest.mark.parametrize('stop', ['os.kill(os.getpid(), signal.SIGTERM)', 'sys.exit(255)'])
def test_stopped_decoder_upload_is_503_without_traceback(tmp_path, monkeypatch, caplog, stop):
    t = db.open_db(tmp_path / 'live.db')
    stop_decoder(monkeypatch, stop)
    r = app_client(t, monkeypatch).post(f'/api/chunks?device=fixture&start={START}', content=b'fixture')
    assert r.status_code == 503 and 'again' in r.json()['detail']
    assert [(x.levelname, x.exc_info) for x in caplog.records if x.name == 'live'] == [('WARNING', None)]
    assert not (t.recordings / 'rejected').exists()
    assert db.conn().execute('SELECT count(*) FROM pieces').fetchone()[0] == 0


def test_crashed_decoder_upload_is_400_and_kept(tmp_path, monkeypatch):
    # a crash (SIGSEGV, SIGABRT) is the file's doing and would recur on every resend: a bad upload, not a stop
    t = db.open_db(tmp_path / 'live.db')
    stop_decoder(monkeypatch, 'os.kill(os.getpid(), signal.SIGSEGV)')
    r = app_client(t, monkeypatch).post(f'/api/chunks?device=fixture&start={START}', content=b'fixture')
    assert r.status_code == 400
    assert (t.recordings / f'rejected/fixture/{START}.bin').read_bytes() == b'fixture'


def long_piece_stopped(tmp_path, monkeypatch):
    """A stored piece over WHOLE_S (cut() decodes only the window asked: piece_window), its decoder then exiting 255."""
    t = db.open_db(tmp_path / 'live.db')
    wav_file(tmp_path / 'long.wav', 301)
    with (tmp_path / 'long.wav').open('rb') as f:
        audio.save_file('fixture', START, f, 'audio/wav')
    stop_decoder(monkeypatch, 'sys.exit(255)')
    a = dt.datetime.fromtimestamp(START / 1000 + 100)
    return t, a.isoformat(), (a + dt.timedelta(seconds=5)).isoformat()


def test_stopped_decoder_playback_is_503_without_traceback(tmp_path, monkeypatch, caplog):
    t, a, b = long_piece_stopped(tmp_path, monkeypatch)
    r = app_client(t, monkeypatch).get('/api/audio', params={'start': a, 'end': b, 'device': 'fixture'})
    assert r.status_code == 503 and 'again' in r.json()['detail']
    assert not [x for x in caplog.records if x.exc_info]


def test_stopped_decoder_is_skipped_by_background_readers(tmp_path, monkeypatch):
    # enroll, voices_loop and self_card skip a piece routes_audio.cut answers with an HTTPException (as a 404)
    from fastapi import HTTPException
    from alldayrec import routes_audio
    _, a, b = long_piece_stopped(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        anyio.run(routes_audio.cut, a, b, 's16le', 'fixture')
    assert e.value.status_code == 503


@pytest.mark.parametrize('path', ['/api/chunks', '/api/ask/voice'])
def test_client_gone_mid_body_without_traceback(tmp_path, monkeypatch, caplog, path):
    from starlette.requests import ClientDisconnect, Request
    t = db.open_db(tmp_path / 'live.db')
    async def gone(self):
        yield b'fixture'
        raise ClientDisconnect()
    monkeypatch.setattr(Request, 'stream', gone)  # body() reads it too
    monkeypatch.setattr(audio, 'save_file', lambda *a: pytest.fail('a piece cut short was decoded'))
    caplog.set_level('INFO', 'live')
    r = app_client(t, monkeypatch).post(path, params={'device': 'fixture', 'start': START}, content=b'fixture')
    assert r.status_code == 499
    assert [(x.levelname, x.exc_info) for x in caplog.records if x.name == 'live'] == [('INFO', None)]
    assert ('POST', path, 499) in t.requests  # perf.Requests counts it


def test_decoder_supervises_progress_not_total_runtime(monkeypatch):
    monkeypatch.setattr(audio, 'DECODE_IDLE_S', .25)
    with tempfile.TemporaryFile() as out:
        before = time.monotonic()
        args = [sys.executable, '-c', 'import os,time\nfor _ in range(12):\n os.write(1,b"xx"); time.sleep(.05)']
        assert audio.decode_process(args, out) == 0
        assert time.monotonic() - before > .25
        assert out.tell() == 24
        with pytest.raises(subprocess.TimeoutExpired):
            audio.decode_process([sys.executable, '-c', 'import time; time.sleep(30)'], out)


def test_stream_timeout_retries_are_bounded(tmp_path, monkeypatch):
    from contextlib import contextmanager
    db.open_db(tmp_path / 'live.db')
    db.conn().execute('INSERT INTO pieces (device, start_ms, seconds, path) VALUES (?, ?, 3600, ?)',
                      ('fixture', START, 'fixture.wav'))
    attempts, notes = [], []
    @contextmanager
    def timeout(*args):
        attempts.append(1)
        raise subprocess.TimeoutExpired('ffmpeg', 300)
        yield
    async def no_wait(seconds):
        assert seconds == 60
    monkeypatch.setattr(audio, 'piece_blocks', timeout)
    monkeypatch.setattr(audio.anyio, 'sleep', no_wait)
    monkeypatch.setattr(audio.ws, 'note', lambda text, level: notes.append(text))
    async def run():
        stream = audio.stream('fixture', None)
        with anyio.move_on_after(.3) as scope:
            await anext(stream)
        assert scope.cancel_called
        await stream.aclose()
    anyio.run(run)
    assert len(attempts) == 3
    assert '已暂停' in notes[-1]
    assert audio.T().read_to['fixture'] < START


@pytest.mark.parametrize('seconds', [601, 1801])
def test_cut_decodes_bounded_window(tmp_path, monkeypatch, seconds):
    db.open_db(tmp_path / 'live.db')
    source = tmp_path / 'long.wav'
    one = wav_file(source, seconds)
    with source.open('rb') as f:
        audio.save_file('fixture', START, f, 'audio/wav')
    monkeypatch.setattr(audio, 'samples', lambda p: pytest.fail('whole recording decoded'))
    original = audio.decode_process
    decoded = []
    def bounded(args, out):
        assert '-ss' in args and '-t' in args
        code = original(args, out)
        decoded.append(out.tell())
        assert out.tell() <= int(5.4 * audio.SR) * 2
        return code
    monkeypatch.setattr(audio, 'decode_process', bounded)
    a = dt.datetime.fromtimestamp(START / 1000 + seconds - 10)
    result = audio.cut(a, a + dt.timedelta(seconds=5), 's16le', 'fixture')
    expected = np.tile(one, 7)[int(.8 * audio.SR):int(6.2 * audio.SR)]
    assert result == expected.tobytes()
    assert decoded


@pytest.mark.parametrize('kind', ['wav', 'flac', 'mp3', 'm4a', 'ogg'])
@pytest.mark.parametrize('at', [0, 13.137, 22.5])
@pytest.mark.parametrize('rate', [16000, 44100])
def test_cut_window_matches_legacy(tmp_path, monkeypatch, kind, at, rate):
    db.open_db(tmp_path / 'live.db')
    source = tmp_path / f'fixture.{kind}'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'anoisesrc=d=24:r={rate}:a=.05:seed=13',
                    *(['-c:a', 'libopus'] if kind == 'ogg' else []),
                    '-y', str(source)], check=True)
    with source.open('rb') as f:
        audio.save_file('fixture', START, f, 'audio/' + kind)
    a = dt.datetime.fromtimestamp(START / 1000 + at)
    b = a + dt.timedelta(seconds=1.1)
    expected = legacy_cut(a, b, 's16le', 'fixture')
    monkeypatch.setattr(audio, 'WHOLE_S', 5)  # exercise the long-import path on a short oracle fixture
    actual = audio.cut(a, b, 's16le', 'fixture')
    x, y = np.frombuffer(actual, np.int16), np.frombuffer(expected, np.int16)
    assert x.shape == y.shape
    # Lossless is sample-exact; codecs can round by at most 2 PCM16 units
    # after the two-second seek preroll (about -84 dBFS).
    assert np.abs(x.astype(np.int32) - y).max() <= (0 if kind in ('wav', 'flac') else 2)


@pytest.mark.parametrize('kind', ['recorder', 'bean', 'aac'])
def test_cut_device_pieces_matches_legacy(tmp_path, kind):
    from test_preroll import encode
    db.open_db(tmp_path / 'live.db')
    if kind == 'aac':
        body = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=d=2:r=16000',
                               '-c:a', 'aac', '-f', 'adts', 'pipe:1'], capture_output=True, check=True).stdout
        pieces = [body, body, body]
        content_type = 'audio/aac'
    else:
        ms = 10 if kind == 'recorder' else 20
        packets = encode('-application', 'lowdelay', '-frame_duration', str(ms), '-b:a', '32k')
        pieces = [audio.ogg(packets[i:i+1000//ms], ms) for i in (0, 1000//ms, 2000//ms)]
        content_type = 'audio/ogg'
    for offset, body in zip((0, 900, 2500), pieces):
        audio.save('fixture', START + offset, body, content_type)
    for start, duration in ((0, .3), (.7, 2.2), (2.6, .2)):
        a = dt.datetime.fromtimestamp(START / 1000 + start)
        b = a + dt.timedelta(seconds=duration)
        for fmt in ('s16le', 'wav'):
            assert audio.cut(a, b, fmt, 'fixture') == legacy_cut(a, b, fmt, 'fixture')


def test_cut_keeps_submillisecond_tail(tmp_path, monkeypatch):
    db.open_db(tmp_path / 'live.db')
    body = audio.wav(np.arange(24 * audio.SR + 7, dtype=np.int16))
    audio.save('fixture', START, body, 'audio/wav')
    a = dt.datetime.fromtimestamp(START / 1000 + 23.7)
    b = a + dt.timedelta(seconds=.4)
    expected = legacy_cut(a, b, 's16le', 'fixture')
    monkeypatch.setattr(audio, 'WHOLE_S', 5)
    assert audio.cut(a, b, 's16le', 'fixture') == expected


@pytest.mark.parametrize('content_type', ['audio/ogg', 'application/octet-stream'])
def test_flac_in_ogg_uses_bounded_disk_fallback(tmp_path, monkeypatch, content_type):
    db.open_db(tmp_path / 'live.db')
    path = tmp_path / 'fixture.ogg'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anoisesrc=d=24:r=44100:a=.05:seed=13',
                    '-c:a', 'flac', '-y', str(path)], check=True)
    with path.open('rb') as source:
        audio.save_file('fixture', START, source, content_type)
    a = dt.datetime.fromtimestamp(START / 1000 + 13.137)
    b = a + dt.timedelta(seconds=1.1)
    expected = legacy_cut(a, b, 's16le', 'fixture')
    monkeypatch.setattr(audio, 'WHOLE_S', 5)
    monkeypatch.setattr(audio, 'samples', lambda p: pytest.fail('whole recording in RAM'))
    original = audio.decode_process
    def disk_only(args, output):
        assert '-ss' not in args, 'FLAC-in-Ogg must not use the misaligned seek'
        assert output.fileno() >= 0
        return original(args, output)
    monkeypatch.setattr(audio, 'decode_process', disk_only)
    assert audio.cut(a, b, 's16le', 'fixture') == expected
