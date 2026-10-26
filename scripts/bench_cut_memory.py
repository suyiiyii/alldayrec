"""5 s playback RSS from a synthetic stored import, in an independent process.

PYTHONPATH=server uv run --project server python scripts/bench_cut_memory.py --seconds 3600
Use the same script with PYTHONPATH pointing at an exported old server for comparison.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import resource
import sys
import tempfile
import wave

from alldayrec import audio, db


def peak():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=3600)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='alldayrec-cut-bench-') as tmp:
        root = Path(tmp)
        t = db.open_db(root / 'live.db')
        path = t.recordings / 'synthetic.wav'
        path.parent.mkdir(parents=True, exist_ok=True)
        block = bytes(32000)
        with wave.open(str(path), 'wb') as out:
            out.setparams((1, 2, 16000, 0, 'NONE', ''))
            for _ in range(args.seconds):
                out.writeframesraw(block)
        start = 1700000000000
        db.conn().execute('INSERT INTO pieces (device, start_ms, seconds, path, pos, size) VALUES (?, ?, ?, ?, 0, ?)',
                          ('synthetic', start, args.seconds, path.name, path.stat().st_size))
        a = dt.datetime.fromtimestamp(start / 1000 + args.seconds - 10)
        before = peak()
        result = audio.cut(a, a + dt.timedelta(seconds=5), 's16le', 'synthetic')
        assert len(result) == int(5.4 * 16000) * 2
        report = {'seconds': args.seconds, 'cut_seconds': 5, 'before_peak_bytes': before,
                  'after_peak_bytes': peak(), 'cut_samples': len(result) // 2}
        if sys.platform == 'linux' and Path('/sys/fs/cgroup/memory.peak').exists():
            report['cgroup_peak_bytes'] = int(Path('/sys/fs/cgroup/memory.peak').read_text())
        print(json.dumps(report))


if __name__ == '__main__':
    main()
