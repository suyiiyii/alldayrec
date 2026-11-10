"""replay_case's two volc calls retry 3 times, 5 s apart, then give up; a success is cached."""
import importlib
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from alldayrec import volc_asr
from alldayrec.segmenter import SR


def flaky(fails: int, utts: list[dict]):
    calls = []

    async def fake(x, *args, **kwargs):
        calls.append(len(x))
        if len(calls) <= fails:
            raise OSError(f"boom {len(calls)}")
        return utts, {"seconds": 1.0, "untimed": []}
    return fake, calls


@pytest.fixture
def rc():
    yield importlib.import_module("alldayrec.replay_case")
    sys.modules.pop("alldayrec.replay_case")  # an offline tool: keep it out of test_isolation's scan of the server's modules


@pytest.fixture
def case(rc, tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(rc.time, "sleep", sleeps.append)
    monkeypatch.delenv("VOLC_MODE", raising=False)
    return SimpleNamespace(work=tmp_path, x=np.zeros(SR * 4, dtype=np.float32)), sleeps


@pytest.mark.parametrize("name, fn, label", [("transcribe", "_volc_window", "volc"), ("stream", "_volc_session", "stream")])
def test_retries_then_succeeds_and_caches(rc, case, monkeypatch, capsys, name, fn, label):
    c, sleeps = case
    fn = getattr(rc, fn)
    fake, calls = flaky(2, [{"start_ms": 0, "end_ms": 500, "text": "hi"}])
    monkeypatch.setattr(volc_asr, name, fake)
    segs = fn(c, 1.0, 3.0)
    assert calls == [SR * 2] * 3 and sleeps == [5, 5]
    assert segs[0]["speaker"] == "SPEAKER_0" and segs[0]["engine"] == "volc" and segs[0]["text"] == "hi"
    assert segs[0]["start_ms"] == (1000 if label == "stream" else 0)  # a session's times are moved to the case
    out = capsys.readouterr().out
    assert f"  {label} 1-3 attempt 1 failed: OSError('boom 1')" in out and "attempt 3" not in out
    [p] = c.work.glob("*/1.0-3.0.json")
    assert json.loads(p.read_text()) == segs
    assert fn(c, 1.0, 3.0) == segs and len(calls) == 3  # the second call reads the cache


@pytest.mark.parametrize("name, fn, label", [("transcribe", "_volc_window", "volc"), ("stream", "_volc_session", "stream")])
def test_gives_up_after_three(rc, case, monkeypatch, name, fn, label):
    c, sleeps = case
    fn = getattr(rc, fn)
    fake, calls = flaky(99, [])
    monkeypatch.setattr(volc_asr, name, fake)
    with pytest.raises(RuntimeError, match=f"^{label} gave up on 1-3$"):
        fn(c, 1.0, 3.0)
    assert len(calls) == 3 and sleeps == [5, 5, 5]
    assert not list(c.work.glob("*/*.json"))
