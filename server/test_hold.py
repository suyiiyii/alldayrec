"""A phone that keeps saying hold without sending older audio cannot stop finals indefinitely."""
import datetime as dt

import anyio
import numpy as np
import pytest

from alldayrec import audio, db, monitor, pipeline_state, routes_upload, tenant


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


def refresh_until(clock, until):
    """The phone stays connected and repeats its hold at intervals shorter than the silence threshold."""
    while clock[0] < until:
        clock[0] = min(until, clock[0] + 200)
        audio.hold("bean", 1_000_000)


def test_live_uploads_do_not_renew_stale_hold(held_store, caplog):
    clock = held_store
    for second in (3000, 3199):
        refresh_until(clock, second)
        audio.save("bean", second * 1000, b"live", "audio/test")
        assert audio.held("bean") == 1_000_000
    clock[0] = 3200
    audio.hold("bean", 1_000_000)
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
    refresh_until(clock, 3100)
    audio.save("bean", 900_000, b"older", "audio/test")
    refresh_until(clock, 3200)
    assert audio.held("bean") == 1_000_000
    refresh_until(clock, 4200)
    audio.save("bean", 900_000, b"older", "audio/test")  # identical retry
    audio.save("other", 800_000, b"older", "audio/test")
    audio.save("bean", 1_000_000, b"boundary", "audio/test")
    assert audio.held("bean") == 1_000_000
    refresh_until(clock, 4300)
    assert audio.held("bean") is None


def test_progress_and_expiry_survive_restart(held_store):
    clock = held_store
    refresh_until(clock, 3100)
    audio.save("bean", 900_000, b"older", "audio/test")
    t = tenant.T()
    tenant.enter(tenant.Tenant(t.id, "", t.data, t.recordings, db=t.db))
    refresh_until(clock, 4000)
    assert audio.held("bean") == 1_000_000
    refresh_until(clock, 4300)
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
    audio.hold("bean", 1_000_000)  # the returning phone confirms the legacy hold at this grace period's start
    refresh_until(clock, 4199)
    assert audio.held("bean") == 1_000_000
    refresh_until(clock, 4200)
    assert audio.held("bean") is None


def test_max_s_still_applies(held_store):
    held_store[0] += audio.HOLD_MAX_S - 1
    assert audio.held("bean") == 1_000_000 and audio.hold_expired("bean") is None
    held_store[0] += 1
    assert audio.held("bean") is None and audio.hold_expired("bean") is None


def test_repeated_reconnects_cannot_extend_total_hold_life(held_store):
    clock = held_store
    since = clock[0]
    for elapsed in range(6 * 60, audio.HOLD_MAX_S, 6 * 60):
        clock[0] = since + elapsed
        audio.hold("bean", 1_000_000)  # each six-minute silence gives fresh stale grace
        assert audio.held("bean") == 1_000_000
        if elapsed == audio.HOLD_MAX_S // 2:
            t = tenant.T()
            tenant.enter(tenant.Tenant(t.id, "", t.data, t.recordings, db=t.db))
    clock[0] = since + audio.HOLD_MAX_S - 1
    audio.hold("bean", 1_000_000)
    audio.save("bean", 900_000, b"older", "audio/test")
    d = monitor.device_status("bean", pipeline_state.PipelineState())
    assert d["hold"] is not None
    assert d["hold_until"] == dt.datetime.fromtimestamp(since + audio.HOLD_MAX_S).isoformat(timespec="seconds")
    clock[0] += 1
    audio.hold("bean", 1_000_000)
    assert audio.held("bean") is None
    clock[0] += 6 * 60
    audio.hold("bean", 1_000_000)
    audio.save("bean", 800_000, b"older", "audio/test")
    assert audio.held("bean") is None  # another reset or genuine progress cannot revive the same boundary
    audio.hold("bean", 1_100_000)
    assert audio.held("bean") == 1_100_000  # a different boundary starts its own lifetime


@pytest.mark.parametrize("refresh_first", [False, True])
def test_legacy_hold_caps_from_last_known_seen(held_store, refresh_first):
    clock = held_store
    since = clock[0]
    db.put_kv("hold:bean", {"at": 1_000_000, "seen": since, "progress": since})
    clock[0] = since + audio.HOLD_MAX_S - 1
    if refresh_first:
        audio.hold("bean", 1_000_000)
    assert audio.held("bean") == 1_000_000
    clock[0] += 1
    audio.hold("bean", 1_000_000)
    assert audio.held("bean") is None


def test_silent_phone_returns_live_first_then_backlog(held_store):
    clock = held_store
    clock[0] += 30 * 60
    assert audio.held("bean") == 1_000_000 and audio.hold_expired("bean") is None
    assert not tenant.T().events
    audio.hold("bean", 1_000_000)  # live uploads first on reconnect: a fresh grace period for the backlog
    audio.save("bean", 1_100_000, b"live", "audio/test")
    assert audio.held("bean") == 1_000_000
    refresh_until(clock, 4900)  # more than the original grace period has passed, less than the fresh one
    assert audio.held("bean") == 1_000_000
    audio.save("bean", 900_000, b"older", "audio/test")
    refresh_until(clock, 5000)
    assert audio.held("bean") == 1_000_000 and not tenant.T().events
    audio.hold("bean", None)  # the backlog is uploaded: the client releases it

    async def run():
        stream = audio.stream("bean", None)
        with anyio.fail_after(2):
            starts = [(await anext(stream))[0] for _ in range(2)]
        await stream.aclose()
        assert starts == [dt.datetime.fromtimestamp(s) for s in (900, 1100)]
    anyio.run(run)


def test_explicit_release_does_not_trigger_stale_warning(held_store, caplog):
    refresh_until(held_store, 3200)  # eligible to expire, but neither the stream nor status has checked it
    anyio.run(routes_upload.hold_release, "bean")
    anyio.run(routes_upload.hold_release, "bean")  # an explicit release is idempotent
    assert audio.held("bean") is None and audio.hold_expired("bean") is None
    assert [e["kind"] for e in tenant.T().events] == ["info"]
    assert "不用再等了" in tenant.T().events[0]["text"]
    assert not any("没有进展" in r.message for r in caplog.records)


def test_reconnect_grace_expires_if_backlog_never_arrives(held_store):
    held_store[0] += 30 * 60
    audio.hold("bean", 1_000_000)
    refresh_until(held_store, 4999)
    assert audio.held("bean") == 1_000_000
    refresh_until(held_store, 5000)
    assert audio.held("bean") is None and audio.hold_expired("bean")["reason"] == "stale"


def test_hold_behind_finalized_position_is_ignored(held_store):
    db.put_kv("finalized_to:bean", dt.datetime.fromtimestamp(1100).isoformat())
    assert audio.held("bean") is None
    held_store[0] += 1200
    assert audio.held("bean") is None and not tenant.T().events


def test_stream_itself_expires_the_hold(held_store):
    audio.save("bean", 1_100_000, b"live", "audio/test")
    refresh_until(held_store, 3200)

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
            refresh_until(clock, 3200)
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
