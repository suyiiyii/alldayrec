"""Sound events (sound_events.py) from synthetic sounds rows only, no model, no audio: a minute's events and a
conversation's tags; the events the scores do not tell (media, typing, ...) never said; quiet never made of gaps,
digital zeros or uncalibrated audio; a day's runs with no data left blank; the sound filter of search; the corrections
kept and exported; off (the tenant's sound false, or no rows), every route and MCP tool as before."""
import datetime as dt
import json

import anyio
import numpy as np
import pytest

from alldayrec import db, mcp_server, routes_api, sound, sound_events, sounds
from alldayrec.tenant import T

SR = sound.SR
DEV = "sample-recorder"
DAY = "2026-11-16"
CLASSES = sound.MODELS["mn10_as"]["classes"]
SPEECH, MUSIC, CAR, TV, TYPING = 0, sounds.MUSIC, 300, 524, 384  # AudioSet (mn10_as): Speech, Music, Vehicle, Television, Typing


def ms(t: str) -> int:
    return round(dt.datetime.fromisoformat(f"{DAY}T{t}").timestamp() * 1000)


def put(start: str, frames: list[tuple[float, dict]], gain_db: float = -11.0, gaps=(), device: str = DEV):
    """A mn10_as row from start (HH:MM:SS): one 10 s frame per (level dBFS, {class: score}), every other class 0.01.
    The level: a square wave of that power (None: digital zeros)."""
    v = np.full((len(frames), CLASSES), 0.01, np.float32)
    x = np.zeros(len(frames) * 10 * SR, np.int16)
    for i, (level, scores) in enumerate(frames):
        for c, s in scores.items():
            v[i, c] = s
        if level is not None:
            a = round(32768 * 10 ** (level / 20))
            x[i * 10 * SR:(i + 1) * 10 * SR] = np.where(np.arange(10 * SR) % 2, a, -a)
    with db.tx() as c:
        sounds.write(c, device, "mn10_as", sounds.row("mn10_as", ms(start), x, v), gaps, gain_db)


def minute(scores: dict | None = None, level: float = -40.0) -> list:
    """One minute's six frames of these scores at this level."""
    return [(level, scores or {})] * 6


def conv(start: str, end: str, cid: str | None = None, device: str = DEV) -> dict:
    c = dict(id=cid or "c" + DAY.replace("-", "") + start.replace(":", ""), start=f"{DAY}T{start}.000", end=f"{DAY}T{end}.000",
             device=device, participants=["我", "S1"], sentences=1, speech_s=60)
    with db.tx() as t:
        db.insert_conversation(t, c)
        db.insert_sentences(t, [dict(id="s" + c["id"], start=c["start"], end=c["end"], speaker="S1", text="Synthetic.", source=device)])
        db.put_summary({"conversation": c["id"], "status": "done", "title": "t", "summary": "s", "tags": []}, t)
    return db.conversation(c["id"])


@pytest.fixture
def store(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    monkeypatch.delenv("SOUND_EVENTS_MODEL", raising=False)
    T().sound = True


def music_ride():
    """10:00-10:10: 3 min of music, 4 min in a car, 3 min quiet; its conversation."""
    put("10:00:00", minute({MUSIC: 0.6, SPEECH: 0.5}) * 3 + minute({CAR: 0.5}) * 4 + minute(level=-70) * 3)
    return conv("10:00:00", "10:09:59")


def test_minutes_and_tags(store):
    c = music_ride()
    m = sound_events.minutes(DEV, ms("10:00:00"), ms("10:10:00"))
    assert len(m) == 10
    assert [sorted(m[k]) for k in sorted(m)] == [["music", "speech"]] * 3 + [["vehicle"]] * 4 + [["quiet"]] * 3
    assert m[ms("10:00:00") // 60000] == {"music": 1.0, "speech": 1.0}, "conf: the share of frames at HIT"
    t = sound_events.tags(c)
    assert t["covered"] == 10 and [(x["event"], x["minutes"]) for x in t["events"]] == [("vehicle", 4), ("music", 3), ("quiet", 3)]
    assert "mostly" not in t["events"][2], "3 of 10 is not most"
    assert all(x["conf"] == 1.0 for x in t["events"]) and t["device"] == DEV


def test_mostly_quiet_and_short_ones_left_out(store):
    put("10:00:00", minute(level=-70) * 8 + minute({MUSIC: 0.6}) * 1 + minute({SPEECH: 0.5}) * 1)
    t = sound_events.tags(conv("10:00:00", "10:09:59"))
    assert t["events"] == [{"event": "quiet", "minutes": 8, "conf": 1.0, "mostly": True}], "1 min of music: too short; speech: never a tag"


def test_unreliable_events_never_said(store):
    """TV, typing: not events; a car under VEHICLE_RUN minutes in a row: not a ride; a minute with no event is heard."""
    put("10:00:00", minute({TV: 0.9, TYPING: 0.9}) * 2 + minute({CAR: 0.6}) * 2 + minute() * 1 + minute({CAR: 0.6}) * 1)
    m = sound_events.minutes(DEV, ms("10:00:00"), ms("10:06:00"))
    assert len(m) == 6 and all(ev == {} for ev in m.values()), m
    assert sound_events.tags(conv("10:00:00", "10:05:59")) is None


def test_quiet_never_faked(store):
    """Gaps (zeros to the model), digital zeros (the -127 floor): no data, not quiet; uncalibrated audio: the -60 cut."""
    put("10:00:00", minute(level=-70) * 2, gaps=[[ms("10:00:00"), ms("10:01:00")]])  # its first minute: a gap
    put("10:02:00", [(None, {})] * 6)  # zeros: the microphone sent nothing
    put("10:03:00", minute(level=-57) * 1, gain_db=0.0)  # no calibration: -57 is not under -60
    put("10:04:00", minute(level=-57) * 1)  # calibrated: under -55
    m = sound_events.minutes(DEV, ms("10:00:00"), ms("10:05:00"))
    assert sorted(m) == [ms(t) // 60000 for t in ("10:01:00", "10:03:00", "10:04:00")], "no data: not a minute"
    assert m[ms("10:01:00") // 60000] == {"quiet": 1.0} and m[ms("10:03:00") // 60000] == {} and m[ms("10:04:00") // 60000] == {"quiet": 1.0}


def test_day_runs(store):
    c = music_ride()
    put("11:00:00", minute(level=-70) * 2)  # no conversation; 10:10-11:00 no data
    [d] = sound_events.day(DAY)
    runs = [(sound_events.hm(r["from"]), sound_events.hm(r["to"]), sorted(r["events"]), r["conv"]) for r in d["runs"]]
    assert d["device"] == DEV and runs == [("10:00", "10:03", ["music", "speech"], c["id"]), ("10:03", "10:07", ["vehicle"], c["id"]),
                                           ("10:07", "10:10", ["quiet"], c["id"]), ("11:00", "11:02", ["quiet"], None)], "the hour between: blank"


def test_search_by_sound(store):
    a = music_ride()
    put("12:00:00", minute(level=-70) * 5)
    b = conv("12:00:00", "12:04:59")
    assert [c["id"] for c in sound_events.search("music")] == [a["id"]]
    assert [c["id"] for c in sound_events.search("quiet")] == [b["id"], a["id"]]
    got = anyio.run(lambda: routes_api.search(sound="vehicle"))
    assert [c["id"] for c in got] == [a["id"]] and got[0]["sounds"]["events"][0]["event"] == "vehicle"
    text = mcp_server.call("search", {"sound": "music"})[0]
    assert a["id"] in text and b["id"] not in text and "有音乐 3 分钟" in text


def test_wrong_kept_and_exported(store):
    c = music_ride()
    t = sound_events.tags(c)
    body = {"device": DEV, "start_ms": t["from"], "end_ms": t["to"], "event": "music", "conv": c["id"]}
    i = anyio.run(routes_api.sound_wrong, body)["id"]
    [w] = anyio.run(routes_api.sound_wrongs)
    assert {k: w[k] for k in body} == body and w["model"] == sound.model_id("mn10_as") and w["at"]
    assert [x.get("wrong") for x in sound_events.tags(c)["events"]] == [None, i, None], "the tag says it was marked"
    anyio.run(routes_api.sound_wrong_undo, i)
    assert anyio.run(routes_api.sound_wrongs) == []
    with pytest.raises(Exception):
        anyio.run(routes_api.sound_wrong, {**body, "event": "media"})
    T().sound = False
    with pytest.raises(Exception, match="no_sound"):
        anyio.run(routes_api.sound_wrong, body)


def answers(cid: str) -> str:
    """Every answer that could carry sound: the API's conversations, search and conversation, MCP's list and texts."""
    status, tools = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {})
    out = [anyio.run(routes_api.conversations, DAY), anyio.run(lambda: routes_api.search(sound="music")),
           {k: v for k, v in anyio.run(routes_api.conversation, cid).items() if k != "standby"}, tools,
           [mcp_server.call(n, a) for n, a in (("day", {"date": DAY}), ("search", {"q": "s"}), ("conversation", {"id": cid}))]]
    return json.dumps(out, ensure_ascii=False, sort_keys=True, default=str)


def test_off_is_as_before(store, monkeypatch):
    """The tenant's sound off with rows, and no SOUND_MODEL, answer as with no rows at all (main's answers: the sound
    filter ignored, as an unknown parameter was); on with no rows too, but for the sound tool listed, search's sound and
    the filter, which then holds."""
    c = music_ride()
    T().sound = False
    off = answers(c["id"])
    assert sound_events.day(DAY) == [] and "sound" not in off and "环境声音" not in off
    monkeypatch.delenv("SOUND_MODEL")
    T().sound = True
    assert answers(c["id"]) == off
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    with db.tx() as t:
        t.execute("DELETE FROM sounds")
    T().sound = False
    assert answers(c["id"]) == off, "rows or not, off is off"
    T().sound = True
    on = json.loads(answers(c["id"]))
    names = [t["name"] for t in on[3]["result"]["tools"]]
    assert "sounds" in names and "sound" in next(t for t in on[3]["result"]["tools"] if t["name"] == "search")["inputSchema"]["properties"]
    assert on[1] == [], "on: the filter holds (no rows, nothing has music)"
    on[1], on[3] = json.loads(off)[1], json.loads(off)[3]
    assert json.dumps(on, ensure_ascii=False, sort_keys=True) == off, "on, no rows: nothing else changes"


def test_mcp_sounds_tool(store):
    c = music_ride()
    text = mcp_server.call("sounds", {"date": DAY, "start": "09:00", "end": "12:00"})[0]
    assert f"10:00-10:03 有人说话、有音乐 `{c['id']}`" in text and "10:03-10:07 在车里或别的交通工具上" in text
    assert "环境声音（模型从声音猜的，可能不对）：在车里或别的交通工具上 4 分钟、有音乐 3 分钟、安静 3 分钟" in mcp_server.call("conversation", {"id": c["id"]})[0]
    assert "没有声音数据" in mcp_server.call("sounds", {"date": DAY, "start": "13:00", "end": "14:00"})[0]
    T().sound = False
    assert mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "sounds", "arguments": {"date": DAY}}}, {})[1]["error"]


def test_events_model(store, monkeypatch):
    monkeypatch.setenv("SOUND_MODEL", "mn10_as,yamnet")
    assert sound_events.model() == "mn10_as"
    monkeypatch.setenv("SOUND_EVENTS_MODEL", "yamnet")
    assert sound_events.model() == "yamnet"
    sound_events.check()
    monkeypatch.setenv("SOUND_EVENTS_MODEL", "clap")
    with pytest.raises(SystemExit):
        sound_events.check()
