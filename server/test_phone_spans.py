"""What a phone recording with its microphone says of it: when it heard nothing and why (a call,
another app holding the microphone), and when it played media aloud. Its events pair into spans for the timeline and
进度, and the media ones become the summary's media_spans (greyed, folded, never a person in the room)."""
import datetime as dt

import anyio

from alldayrec import db
from alldayrec import summary
from alldayrec.routes_upload import DeviceEvent, device_log_upload, phone_spans

D = dt.datetime(2026, 10, 26, 10, 0)
ms = lambda m, s=0: int((D + dt.timedelta(minutes=m, seconds=s)).timestamp() * 1000)
at = lambda m, s=0: (D + dt.timedelta(minutes=m, seconds=s)).isoformat(timespec="milliseconds")


def ev(src, m, kind, s=0, **f):
    return DeviceEvent(src=src, seq=ms(m, s), at=ms(m, s), data={"kind": kind, "text": "", **f})


def test_spans(tmp_path):
    db.open_db(tmp_path / "t.db")
    anyio.run(device_log_upload, "phone-a", [
        ev("app:a", 1, "mic_lost", reason="call"), ev("app:a", 4, "mic_back"),
        ev("app:a", 6, "media_on"), ev("app:a", 9, "media_off"),
        ev("app:a", 20, "mic_lost", reason="other_app")])  # still open
    anyio.run(device_log_upload, "LR-P3-0000", [ev("app:a", 22, "launch")])  # the app died: filed under its recorder's name
    anyio.run(device_log_upload, "phone-b", [ev("app:b", 2, "mic_lost", reason="other_app"), ev("app:b", 3, "connect")])
    got = anyio.run(phone_spans, at(0), at(60))
    assert [(x["device"], x["kind"], x["reason"], x["from"], x["to"]) for x in got] == [
        ("phone-a", "mic", "call", at(1), at(4)),
        ("phone-b", "mic", "other_app", at(2), None),  # another event of the app does not end it
        ("phone-a", "media", None, at(6), at(9)),
        ("phone-a", "mic", "other_app", at(20), at(22)),  # its install's launch does, whatever device it is filed under
    ], got
    assert [x["from"] for x in anyio.run(phone_spans, at(5), at(21))] == [at(2), at(6), at(20)], "overlapping, not starting inside"
    assert [x["from"] for x in db.phone_spans(at(0), at(60), "phone-a")] == [at(1), at(6), at(20)]


def test_media_into_summary(tmp_path):
    """The phone's media span becomes a media span of the summary even when the model saw none, and the model is told."""
    db.open_db(tmp_path / "t.db")
    anyio.run(device_log_upload, "phone-a", [ev("app:a", 2, "media_on"), ev("app:a", 3, "media_off")])
    c = {"id": "c20261026100000", "start": at(0), "end": at(10), "speech_s": 600.0, "device": "phone-a"}
    rows = [dict(start=at(1), end=at(1, 30), speaker="我", text="我们先定一下"),
            dict(start=at(2, 5), end=at(2, 50), speaker="S1", text="欢迎收看"),
            dict(start=at(2, 55), end=at(2, 58), speaker="S1", text="下期再见"),
            dict(start=at(4), end=at(4, 20), speaker="我", text="好")]
    assert summary.played(c, rows) == [{"from": at(2, 5), "to": at(2, 55)}]
    assert summary.played({**c, "device": "phone-b"}, rows) == []
    s = summary.result(c, rows, {"title": "t", "summary": "s"}, {"model": "m"})
    assert s["media_spans"] == [{"from": at(2, 5), "to": at(2, 55)}] and s["media_share"] == round(48 / (30 + 48 + 20), 2), s
    assert summary.media_labels(rows, s["media_spans"]) == {"S1"}  # its voice is the media's: not named, not a person
    # the model's own span over the same rows is not doubled
    s = summary.result(c, rows, {"media_spans": [{"from": "10:02:05", "to": "10:02:55"}]}, {})
    assert s["media_spans"] == [{"from": at(2, 5), "to": at(2, 55)}], s

    said = []
    async def chat(text, tags, start, todos=""):
        said.append(text)
        return {"title": "t"}, {}
    summary.chat, old = chat, summary.chat
    try:
        anyio.run(summary.summarize, c, rows)
    finally:
        summary.chat = old
    assert said[0].endswith("\n（手机报告：10:02:05 到 10:02:55 它在外放音乐或视频，这段里的声音按外放媒体算）"), said
