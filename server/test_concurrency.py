"""One way to run things side by side in server/: anyio. Threads only through anyio.to_thread.run_sync with a
limiter (threads.py), tasks in TaskGroups, timeouts with fail_after / move_on_after, queues as memory object streams. No
asyncio primitives, no threading.Thread, no concurrent.futures pools, except where ALLOWED says why."""
import io
import json
import os
import subprocess
import sys
import tokenize
from pathlib import Path

HERE = Path(__file__).resolve().parent

# file: lines (stripped) that may still use them, and why
ALLOWED = {
    "replay_case.py": {  # an offline replay of recorded cases: sync urllib calls in a pool, each window on its own loop
        "import concurrent.futures as cf": "",
        'with cf.ThreadPoolExecutor(2) as ex, open(self.cache_path, "a") as f:  # the live service shares the key': "",
        "with cf.ThreadPoolExecutor(4) as ex:": "",
    },
    "migrate.py": {"import concurrent.futures as cf": "one-off (10-02 SQLite cutover): to delete",
                   "with cf.ThreadPoolExecutor(16) as ex:": "one-off: to delete"},
    "monitor.py": {  # loop_stalls' watcher runs beside the loop it watches for the process's life, not a job under a limiter
        'threading.Thread(target=watch, args=(beat, float(os.environ["LOOP_STALL_MS"]) / 1000), daemon=True, name="loop-stalls").start()': "",
    },
}


def uses(src: str) -> list[tuple[int, str]]:
    """(line, its text) of each use: the names asyncio, concurrent, ThreadPoolExecutor, run_in_executor and
    threading.Thread (code only: not in comments or strings)."""
    toks, out = list(tokenize.generate_tokens(io.StringIO(src).readline)), []
    lines = src.splitlines()
    for i, t in enumerate(toks):
        if t.type != tokenize.NAME:
            continue
        thread = t.string == "threading" and i + 2 < len(toks) and toks[i + 1].string == "." and toks[i + 2].string == "Thread"
        if t.string in ("asyncio", "concurrent", "ThreadPoolExecutor", "run_in_executor") or thread:
            if not out or out[-1][0] != t.start[0]:
                out.append((t.start[0], lines[t.start[0] - 1].strip()))
    return out


def test_one_way():
    found = {f.name: uses(f.read_text()) for f in sorted((HERE / "alldayrec").glob("*.py"))}
    bad = [f"{name}:{n}: {line}" for name, us in found.items() for n, line in us if line not in ALLOWED.get(name, {})]
    assert not bad, "use anyio (see threads.py):\n" + "\n".join(bad)
    stale = [f"{name}: {line}" for name, lines in ALLOWED.items() for line in lines if line not in {x for _, x in found.get(name, [])}]
    assert not stale, "allowed but no longer there, drop it from ALLOWED:\n" + "\n".join(stale)


def test_uses():
    src = ('import asyncio  # x\nx = "asyncio.run"  # asyncio in a comment\nthreading.Thread(target=f)\nthreading.Lock()\n'
           'loop.run_in_executor(None, f)\nwith cf.ThreadPoolExecutor(2) as ex:\n')
    assert [n for n, _ in uses(src)] == [1, 3, 5, 6]


def test_one_blas_thread():
    """numpy's and scipy's OpenBLAS pools in the server process: 1 thread each unless the env says otherwise (server.py)."""
    env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
    out = subprocess.run([sys.executable, "-c", "from alldayrec import server; import scipy.linalg, threadpoolctl, json; print(json.dumps("
                          "[i['num_threads'] for i in threadpoolctl.threadpool_info() if i['internal_api'] == 'openblas']))"],
                         cwd=HERE, env=env, capture_output=True, text=True, check=True).stdout
    assert json.loads(out) and set(json.loads(out)) == {1}
