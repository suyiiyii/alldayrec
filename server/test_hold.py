"""A phone that keeps saying hold without sending older audio cannot stop finals indefinitely."""
import datetime as dt

import anyio
import numpy as np
import pytest

from alldayrec import audio, db, monitor, pipeline_state, routes_upload, tenant, ws


@pytest.fixture
def held_store(tmp_path, monkeypatch):
    db.open_db(tmp_path / "live.db", tmp_path / "recordings")
    clock = [2000.0]
    monkeypatch.setattr(audio.time, "time", lambda: clock[0])
    monkeypatch.setattr(audio, "HOLD_STALE_S", 1200)
    monkeypatch.setattr(audio, "decode", lambda *a, **k: np.ones(160, dtype=np.int16))
    monkeypatch.setattr(audio, "health", lambda x: (-60, -60, 0, 0))
    audio.hold("bean", 1_000_000)
    return clock


def test_live_uploads_do_not_renew_stale_hold(held_store, caplog):
    clock = held_store
    for second in (3000, 3199):
        clock[0] = second
        audio.hold("bean", 1_000_000)
        audio.save("bean", second * 1000, b"live", "audio/test")
        assert audio.held("bean") == 1_000_000
    clock[0] = 3200
    assert audio.held("bean") is None
    assert audio.hold_expired("bean") == {"reason": "stale", "seconds": 1200}
    audio.hold("bean", 1_000_000)
    assert audio.held("bean") is None
    assert len(tenant.T().events) == 1 and tenant.T().events[0]["kind"] == "warn"
    assert len([r for r in caplog.records if "没有进展" in r.message]) == 1
    d = monitor.device_status("bean", pipeline_state.PipelineState())
    assert d["hold"] is None and d["hold_expired"]["reason"] == "stale"
    anyio.run(routes_upload.hold_release, "bean")
    assert audio.hold_expired("bean") is None


def test_older_pieces_renew_but_retries_and_other_devices_do_not(held_store):
    clock = held_store
    clock[0] = 3100
    audio.save("bean", 900_000, b"older", "audio/test")
    clock[0] = 3200
    assert audio.held("bean") == 1_000_000
    clock[0] = 4200
    audio.save("bean", 900_000, b"older", "audio/test")  # identical retry
    audio.save("other", 800_000, b"older", "audio/test")
    audio.save("bean", 1_000_000, b"boundary", "audio/test")
    assert audio.held("bean") == 1_000_000
    clock[0] = 4300
    assert audio.held("bean") is None


def test_progress_and_expiry_survive_restart(held_store):
    clock = held_store
    clock[0] = 3100
    audio.save("bean", 900_000, b"older", "audio/test")
    t = tenant.T()
    tenant.enter(tenant.Tenant(t.id, "", t.data, t.recordings, db=t.db))
    clock[0] = 4000
    audio.hold("bean", 1_000_000)
    assert audio.held("bean") == 1_000_000
    clock[0] = 4300
    assert audio.held("bean") is None
    tenant.enter(tenant.Tenant(t.id, "", t.data, t.recordings, db=t.db))
    audio.hold("bean", 1_000_000)
    assert audio.held("bean") is None and not tenant.T().events
    audio.hold("bean", 1_100_000)
    assert audio.held("bean") == 1_100_000 and audio.hold_expired("bean") is None


def test_legacy_hold_gets_one_persisted_grace_period(held_store):
    clock = held_store
    db.put_kv("hold:bean", {"at": 1_000_000, "seen": 2000})
    clock[0] = 3000
    assert audio.held("bean") == 1_000_000
    clock[0] = 4199
    audio.hold("bean", 1_000_000)
    assert audio.held("bean") == 1_000_000
    clock[0] = 4200
    assert audio.held("bean") is None


def test_max_s_still_applies(held_store):
    held_store[0] += audio.HOLD_MAX_S
    assert audio.held("bean") is None and audio.hold_expired("bean") is None


def test_hold_behind_finalized_position_is_ignored(held_store):
    db.put_kv("finalized_to:bean", dt.datetime.fromtimestamp(1100).isoformat())
    assert audio.held("bean") is None
    held_store[0] += 1200
    assert audio.held("bean") is None and not tenant.T().events


def test_stream_itself_expires_the_hold(held_store):
    audio.save("bean", 1_100_000, b"live", "audio/test")
    held_store[0] = 3200

    async def run():
        stream = audio.stream("bean", None)
        with anyio.fail_after(2):
            at, _ = await anext(stream)
        assert at == dt.datetime.fromtimestamp(1100)
        assert len(tenant.T().events) == 1
        await stream.aclose()
    anyio.run(run)


def test_stale_hold_stream_resumes_and_warns_from_status_thread(held_store):
    clock = held_store
    audio.save("bean", 1_100_000, b"live", "audio/test")
    # The real stream checks held() before decoding; decoding the synthetic bytes is stubbed above.
    async def run():
        async with anyio.create_task_group() as tg:
            tenant.T().tg = tg
            clock[0] = 3200
            d = await anyio.to_thread.run_sync(monitor.device_status, "bean", pipeline_state.PipelineState())
            assert d["hold"] is None and len(tenant.T().events) == 1
            stream = audio.stream("bean", None)
            with anyio.fail_after(2):
                at, samples = await anext(stream)
            assert at == dt.datetime.fromtimestamp(1100) and len(samples) == 160
            await stream.aclose()
            tg.cancel_scope.cancel()
        tenant.T().tg = None
    anyio.run(run)
