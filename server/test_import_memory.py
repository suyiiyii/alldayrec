"""Import working-set bounds and unchanged PCM/health/VAD, using generated audio only."""
import datetime as dt
import tempfile
import wave

import anyio
import numpy as np
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from alldayrec import audio, db, infer, routes_upload, tenant
from alldayrec.segmenter import Segmenter, next_cut


def wav_file(path, seconds):
    x = (np.sin(np.arange(audio.SR) * 2 * np.pi * 173 / audio.SR) * 1200).astype(np.int16)
    x[audio.SR // 2:] = 0
    with wave.open(str(path), 'wb') as w:
        w.setparams((1, 2, audio.SR, 0, 'NONE', ''))
        for _ in range(seconds):
            w.writeframes(x.tobytes())
    return x


def client(t):
    app = FastAPI()
    app.include_router(routes_upload.router)
    @app.middleware('http')
    async def context(request, call_next):
        tenant.enter(t)
        return await call_next(request)
    return TestClient(app)


def test_import_stream_is_bounded(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / 'live.db')
    path = tmp_path / 'long.wav'
    one_second = wav_file(path, 601)
    c = client(t)
    # Neither request buffering nor the old whole-piece decoder may run, even
    # when Content-Length is absent (chunked transfer).
    from starlette.requests import Request
    async def no_body(self):
        raise AssertionError('whole HTTP body read')
    monkeypatch.setattr(Request, 'body', no_body)
    monkeypatch.setattr(audio, 'decode', lambda *a, **kw: pytest.fail('whole file decoded'))
    sizes = []
    old_blocks = audio.pcm_blocks
    def blocks(*args):
        for x in old_blocks(*args):
            sizes.append(len(x))
            assert len(x) <= audio.BLOCK_S * audio.SR
            yield x
    monkeypatch.setattr(audio, 'pcm_blocks', blocks)
    with path.open('rb') as f:
        r = c.post('/api/chunks?device=import-test&start=1700000000000', content=iter(lambda: f.read(65536), b''),
                   headers={'content-type': 'audio/wav'})
    assert r.status_code == 200 and r.json()['seconds'] == 601
    assert c.post('/api/chunks/end?device=import-test&t=1700000601000').status_code == 200
    async def consume():
        tenant.enter(t)
        stream = audio.stream('import-test', None)
        count = 0
        try:
            async for at, x in stream:
                assert len(x) <= audio.BLOCK_S * audio.SR
                assert at == dt.datetime.fromtimestamp(1700000000) + dt.timedelta(seconds=count / audio.SR)
                np.testing.assert_array_equal(x, np.tile(one_second, len(x) // audio.SR))
                count += len(x)
                if count == 601 * audio.SR:
                    break
        finally:
            await stream.aclose()
        assert count == 601 * audio.SR
        assert t.read_to.get('import-test', -1) < 1700000000000, 'not acknowledged before final block consumed'
    anyio.run(consume)
    assert len(sizes) == 122, '61 health blocks plus 61 stream blocks'
    assert c.get('/missing').status_code == 404, 'server still serves requests'


def test_import_failure_can_retry(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / 'live.db')
    c = client(t)
    url = '/api/chunks?device=import-test&start=1700000000000'
    r = c.post(url, content=b'not audio')
    assert r.status_code == 400 and r.json()['detail'] == 'could not decode'
    assert db.conn().execute('SELECT count(*) FROM pieces').fetchone()[0] == 0
    original = audio.save_file
    def no_space(*args):
        raise OSError('No space left on device')
    monkeypatch.setattr(audio, 'save_file', no_space)
    r = c.post(url, content=b'upload')
    assert r.status_code == 503 and 'retry' in r.json()['detail']
    assert db.conn().execute('SELECT count(*) FROM pieces').fetchone()[0] == 0
    monkeypatch.setattr(audio, 'save_file', original)
    path = tmp_path / 'short.wav'
    wav_file(path, 2)
    with path.open('rb') as f:
        assert c.post(url, content=f).json()['seconds'] == 2


@pytest.mark.parametrize('diagnostic', ['No space left on device', 'Disk quota exceeded', 'Input/output error', 'Error opening output file'])
def test_decoder_diagnostics_are_not_storage_errors(tmp_path, monkeypatch, diagnostic):
    import sys
    t = db.open_db(tmp_path / 'live.db')
    c = client(t)
    path = tmp_path / 'valid.wav'
    wav_file(path, 2)
    url = '/api/chunks?device=import-test&start=1700000000000'
    popen = audio.subprocess.Popen
    with monkeypatch.context() as full:
        def fail_write(args, **kwargs):
            return popen([sys.executable, '-c', 'import sys; sys.stderr.write(sys.argv[1]); sys.exit(1)', diagnostic + '\n' + 'x' * 20000], **kwargs)
        full.setattr(audio.subprocess, 'Popen', fail_write)
        with path.open('rb') as f:
            r = c.post(url, content=f)
        assert r.status_code == 400, 'stderr is not evidence of an errno from our writes'
        assert db.conn().execute('SELECT count(*) FROM pieces').fetchone()[0] == 0
        assert (t.recordings / 'rejected' / 'import-test' / '1700000000000.bin').exists()
    with path.open('rb') as f:
        assert c.post(url, content=f).json()['seconds'] == 2


@pytest.mark.parametrize('seconds', [1, 10, 21, 300])
def test_import_health_matches_legacy(seconds):
    x = np.random.default_rng(42).integers(-32768, 32768, seconds * audio.SR + 17, dtype=np.int16)
    for k in [0, audio.SR * 10 - 100, audio.SR * 20 - 200, len(x) - 500]:
        if k >= 0:
            x[k:k + 350] = 0
    with tempfile.TemporaryFile() as pcm:
        pcm.write(x.tobytes())
        pcm.seek(0)
        assert audio.file_health(pcm, len(x)) == audio.health(x)


def legacy_gain(self, x):
    # The pre-fix calculation, retained here as an independent numerical oracle.
    fr = np.lib.stride_tricks.sliding_window_view(x, 400)[::160].astype(np.float64)
    levels = 10 * np.log10((fr * fr).mean(1) / 32768 ** 2 + 1e-12)
    lv = np.concatenate([self.levels, levels])
    self.levels = lv[-infer.LEVEL_FRAMES + 1:]
    p90 = np.partition(np.lib.stride_tricks.sliding_window_view(lv, infer.LEVEL_FRAMES),
                       infer.LEVEL_FRAMES * 9 // 10, axis=1)[:, infer.LEVEL_FRAMES * 9 // 10]
    return np.clip(infer.VAD_TARGET_DB - p90, 0, infer.VAD_GAIN_MAX_DB)



def legacy_fbank(x, gain_db=0.0):
    options = infer.knf.FbankOptions()
    options.frame_opts.samp_freq, options.frame_opts.dither = 16000, 0
    options.frame_opts.snip_edges, options.mel_opts.num_bins = True, 80
    f = infer.knf.OnlineFbank(options)
    f.accept_waveform(16000, x.tolist())
    _, mean, istd = infer._vad()
    feat = np.vstack([f.get_frame(i) for i in range(f.num_frames_ready)]) + np.reshape(gain_db, (-1, 1)) * (np.log(10) / 10)
    return ((feat - mean) * istd).astype(np.float32)

def test_import_gain_matches_legacy_and_bounds_partition(monkeypatch):
    from test_vad_gain import at, speech
    x = at(speech(35), -55)
    expected = legacy_gain(infer.StreamVad(), x)
    original = np.partition
    calls = []
    def partition(a, *args, **kwargs):
        calls.append(a.shape)
        assert a.shape[0] <= 1000
        return original(a, *args, **kwargs)
    monkeypatch.setattr(np, 'partition', partition)
    vad = infer.StreamVad()
    np.testing.assert_array_equal(vad.gain(x), expected)
    assert len(calls) > 1
    assert vad.levels.base is None, 'history must not retain the whole input levels'


@pytest.mark.skipif(not all((infer.DIR / n).is_file() for n in (infer.VAD, infer.CMVN)), reason='no VAD model files (CI)')
def test_five_minute_import_matches_legacy(tmp_path, monkeypatch):
    from test_vad_gain import at, speech
    db.open_db(tmp_path / 'live.db')
    x = np.tile(at(speech(10), -55), 30)
    body = audio.wav(x)
    start = 1700000000000
    assert audio.save('old', start, body, 'audio/wav') == 300
    with tempfile.NamedTemporaryFile() as source:
        source.write(body)
        source.flush()
        assert audio.save_file('new', start, source, 'audio/wav') == 300
    rows = db.conn().execute('SELECT * FROM pieces ORDER BY device').fetchall()
    keys = ('start_ms', 'seconds', 'size', 'n', 'rms', 'hum', 'zero_ms', 'full')
    assert [tuple(r[k] for k in keys) for r in rows] == [tuple(rows[0][k] for k in keys)] * 2
    for row in rows:
        assert audio.read(row) == body
        np.testing.assert_array_equal(audio.samples(row), x)
    begin = dt.datetime.fromtimestamp(start / 1000)
    def run(step, legacy=False):
        seg = Segmenter()
        if legacy:
            seg.vad.gain = legacy_gain.__get__(seg.vad)
        utts = []
        for i in range(0, len(x), step):
            utts.extend(seg.push(begin + dt.timedelta(seconds=i/audio.SR), x[i:i+step]))
        utts.extend(seg.push(begin + dt.timedelta(seconds=300), np.zeros(audio.SR, np.int16)))
        return [(a, b, y.tobytes()) for a, b, y in utts], seg.speech
    with monkeypatch.context() as old_code:
        old_code.setattr(infer, "fbank", legacy_fbank)
        old = run(len(x), legacy=True)
    assert run(len(x)) == old, '5-minute files retain their existing VAD segments and PCM pieces'
    assert run(audio.BLOCK_S * audio.SR) == old, 'long-file block boundaries do not move VAD segments'
    cuts = lambda utts: next_cut([((a-begin).total_seconds(), (b-begin).total_seconds()) for a, b, _ in utts], 300, False, True)
    assert cuts(old[0]) == cuts(run(len(x))[0])


@pytest.mark.parametrize('check', ['test_join', 'test_bean_join', 'test_m4a_moov_at_end', 'test_resend'])
def test_file_upload_preserves_live_and_retry_behavior(tmp_path, monkeypatch, check):
    import test_import_m4a
    import test_preroll
    import test_resend
    def upload(device, start, body, kind, n=None):
        with tempfile.NamedTemporaryFile() as source:
            source.write(body)
            source.flush()
            return audio.save_file(device, start, source, kind, n)
    monkeypatch.setattr(audio, 'save', upload)
    if check == 'test_join':
        test_preroll.test_join(tmp_path, monkeypatch)
    elif check == 'test_bean_join':
        test_preroll.test_bean_join(tmp_path)
    elif check == 'test_resend':
        test_resend.test_resend()
    else:
        test_import_m4a.test_m4a_moov_at_end()
