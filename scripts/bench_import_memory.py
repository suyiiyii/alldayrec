"""Synthetic HTTP upload -> real decode/store/stream/VAD; no ASR or external API.

Run from the repo root: OPENBLAS_NUM_THREADS=1 MODELS_DIR=/path/to/models \
    PYTHONPATH=server uv run --project server python scripts/bench_import_memory.py --seconds 3600
Needs the pinned VAD and CMVN files (infer.SOURCES); no owner audio is used.
Measures the server with /usr/bin/time (-l on macOS, -v on Linux), and samples
RSS every 20 ms with a 6 GiB safety ceiling. Each run has an empty DATA_DIR.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import resource
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave


def serve(root, port):
    import uvicorn
    from fastapi import FastAPI
    from alldayrec import audio, db, infer, routes_upload, tenant
    from alldayrec.segmenter import Segmenter
    t = tenant.Tenant('benchmark', '', root, root / 'recordings')
    tenant.enter(t)
    db.init()
    app = FastAPI()
    app.include_router(routes_upload.router)
    peaks, sizes = {}, []
    def peak(stage):
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peaks[stage] = r if sys.platform == 'darwin' else r * 1024
    for name in ('decode', 'health', 'file_health', 'save_file'):
        if not hasattr(audio, name):
            continue
        orig = getattr(audio, name)
        def wrapped(*a, _f=orig, _name=name, **kw):
            out = _f(*a, **kw)
            peak(_name)
            return out
        setattr(audio, name, wrapped)
    orig = infer.StreamVad.gain
    def gain(self, x):
        sizes.append(len(x))
        out = orig(self, x)
        peak('gain')
        return out
    infer.StreamVad.gain = gain
    @app.middleware('http')
    async def context(request, call_next):
        tenant.enter(t)
        return await call_next(request)
    @app.get('/ready')
    def ready():
        peak('ready')
        return {"pid": os.getpid(), "stages": peaks}
    @app.post('/drain')
    async def drain(seconds: int):
        seg, fed = Segmenter(), 0
        async for at, x in audio.stream('import-synthetic', None):
            await seg.apush(at, x)
            fed += len(x)
            if fed >= seconds * 16000:
                break
        peak('vad')
        return {'peak_bytes': max(peaks.values()), 'stages': peaks,
                'vad_calls': len(sizes), 'max_vad_samples': max(sizes)}
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='error')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seconds', type=int, default=3600)
    p.add_argument('--serve', type=Path)
    p.add_argument('--port', type=int)
    args = p.parse_args()
    if args.serve:
        serve(args.serve, args.port)
        return
    import numpy as np
    import scipy.signal as ss

    def speech(seconds, seed=0):
        rng, out, n_all = np.random.default_rng(seed), [], 0
        vowels = [(730, 1090), (270, 2290), (300, 870), (530, 1840), (570, 840)]
        while n_all < seconds * 16000:
            n = int(rng.uniform(0.15, 0.3) * 16000)
            y = (np.diff(np.floor(np.cumsum(rng.uniform(110, 180) * np.linspace(1.1, 0.9, n) / 16000)), prepend=0) > 0).astype(float)
            for f in vowels[rng.integers(len(vowels))]:
                y = ss.lfilter(*ss.iirpeak(f, f / 80, 16000), y)
            gap = np.zeros(int((0.5 if rng.random() < 0.15 else rng.uniform(0.03, 0.12)) * 16000))
            out += [y * np.hanning(n), gap]
            n_all += n + len(gap)
        x = np.concatenate(out)[:int(seconds * 16000)]
        return x / np.sqrt((x * x).mean())

    def at(x, db):
        noise = np.random.default_rng(1).normal(size=len(x)) * 10 ** (-75 / 20)
        return np.clip((x * 10 ** (db / 20) + noise) * 32768, -32768, 32767).astype(np.int16)
    with tempfile.TemporaryDirectory(prefix='alldayrec-import-bench-') as tmp:
        root = Path(tmp)
        wav = root / 'synthetic.wav'
        block = at(speech(10), -40).tobytes()
        with wave.open(str(wav), 'wb') as w:
            w.setparams((1, 2, 16000, 0, 'NONE', ''))
            for i in range(0, args.seconds, 10):
                w.writeframes(block[:min(10, args.seconds-i)*32000])
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        env = {**os.environ, 'DATA_DIR': str(root), 'ENGINE': 'local', 'ENGINE_FALLBACK': '', 'OPENBLAS_NUM_THREADS': '1'}
        time_report = root / 'time.txt'
        timer = ['/usr/bin/time', '-l' if sys.platform == 'darwin' else '-v', '-o', str(time_report)] if Path('/usr/bin/time').exists() else []
        proc = subprocess.Popen([*timer, sys.executable, __file__, '--serve', str(root), '--port', str(port)],
                                env=env, start_new_session=True)
        stop, sampled, server_pid = threading.Event(), [0], [None]
        def sample():
            while not stop.wait(.02) and proc.poll() is None:
                if server_pid[0] is None:
                    continue
                if sys.platform == 'linux':
                    try:
                        status = Path(f'/proc/{server_pid[0]}/status').read_text()
                    except FileNotFoundError:
                        continue
                    match = re.search(r'VmRSS:\s+(\d+)', status)
                    rss = int(match[1]) * 1024 if match else 0
                else:
                    result = subprocess.run(['ps', '-o', 'rss=', '-p', str(server_pid[0])], capture_output=True, text=True)
                    rss = int(result.stdout.strip() or 0) * 1024
                if rss:
                    sampled[0] = max(sampled[0], rss)
                    if sampled[0] > 6 * 1024**3:
                        os.killpg(proc.pid, signal.SIGKILL)
        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        def call(method, path, body=None, headers={}):
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=300)
            c.request(method, path, body=body, headers=headers)
            r = c.getresponse()
            data = r.read()
            c.close()
            if r.status != 200:
                raise RuntimeError((r.status, data))
            return json.loads(data)
        try:
            for _ in range(100):
                try:
                    server_pid[0] = call('GET', '/ready')['pid']
                    break
                except OSError:
                    time.sleep(.1)
            with wav.open('rb') as f:
                uploaded = call('POST', '/api/chunks?device=import-synthetic&start=1700000000000', f,
                                {'Content-Type': 'audio/wav', 'Content-Length': str(wav.stat().st_size)})
            call('POST', f'/api/chunks/end?device=import-synthetic&t={1700000000000+args.seconds*1000}')
            result = call('POST', f'/drain?seconds={args.seconds}')
        finally:
            stop.set()
            if server_pid[0] is not None and proc.poll() is None:
                os.kill(server_pid[0], signal.SIGTERM)
            elif proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            monitor.join()
        report = time_report.read_text() if timer else ''
        peak = re.search(r"(\d+)\s+maximum resident set size", report) if sys.platform == 'darwin' else re.search(r"Maximum resident set size \(kbytes\): (\d+)", report)
        if timer and peak is None:
            raise RuntimeError('no RSS measurement in /usr/bin/time output')
        time_peak = int(peak[1]) * (1 if sys.platform == 'darwin' else 1024) if peak else None
        print(json.dumps({'seconds': args.seconds, 'uploaded': uploaded, 'sampled_peak_bytes': sampled[0],
                          'time_peak_bytes': time_peak, **result}))


if __name__ == '__main__':
    main()
