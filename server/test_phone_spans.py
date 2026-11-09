"""What a phone recording with its microphone says of it: when it heard nothing and why (a call,
another app holding the microphone), and when it played media aloud. Its events pair into spans for the timeline and
进度, and the media ones are a note for the summary's model (people talking over them still count)."""
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


def test_media_is_a_hint(tmp_path):
    """The phone's media span is a note for the model, never media_spans by itself: people talking while the phone
    plays music keep their todos, their place among the people and their voices (not media_labels)."""
    db.open_db(tmp_path / "t.db")
    anyio.run(device_log_upload, "phone-a", [ev("app:a", 0, "media_on"), ev("app:a", 30, "media_off")])
    c = {"id": "c20261026100000", "start": at(0), "end": at(30), "speech_s": 600.0, "device": "phone-a"}
    rows = [dict(start=at(m), end=at(m, 20), speaker=w, text=t)
            for m, w, t in ((1, "我", "明天下午三点我把合同发给你"), (2, "S1", "好的我等你"), (3, "我", "顺便把报价也带上"), (4, "S1", "行"))]
    assert summary.played(c, rows) == [{"from": at(1), "to": at(4)}]
    assert summary.played({**c, "device": "phone-b"}, rows) == [] and summary.played({**c, "device": None}, rows) == []
    reply = {"title": "t", "summary": "s", "media_spans": [],
             "people": [{"who": "我", "points": [{"text": "发合同", "at": "10:01:00"}]}, {"who": "S1", "points": ["等合同"]}],
             "todos": [{"owner": "我", "to": "S1", "text": "把合同发给 S1", "due": "明天下午三点", "quote": "明天下午三点我把合同发给你", "at": "10:01:00"}]}
    s = summary.result(c, rows, reply, {"model": "m"})
    assert s["media_spans"] == [] and s["media_share"] == 0.0, s
    assert [t["text"] for t in s["todos"]] == ["把合同发给 S1"] and [p["who"] for p in s["people"]] == ["我", "S1"], s
    assert summary.media_labels(rows, s["media_spans"]) == set()  # their voices stay theirs: named, asked 「这是你吗」

    said = []
    async def chat(text, tags, start, todos=""):
        said.append(text)
        return {"title": "t"}, {}
    summary.chat, old = chat, summary.chat
    try:
        anyio.run(summary.summarize, c, rows)
    finally:
        summary.chat = old
    assert said[0].endswith("\n（手机报告：10:01:00 到 10:04:00 它在外放音乐或视频；这段里在场的人说的话照常算）"), said
    assert "就在那里断开分成两段" in summary.SYSTEM  # the model's rule for media with people talking over it stays
