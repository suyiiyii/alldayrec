"""Tenant "media_view": the summary in three kinds (media played aloud, broadcasts, live), 改回现场 standing over a redo,
voiceprints learning no line of either, and an agent's transcript marking them. Off, all as before. Synthetic rows only."""
import datetime as dt

import anyio
import pytest
from fastapi import HTTPException

from alldayrec import core, db, profiles, routes_api, routes_speakers, summaries, summary, ws


class Done(Exception):
    pass


@pytest.fixture
def store(tmp_path):
    return db.open_db(tmp_path / "t.db")


def conversation(n=6):
    """A synthetic conversation of n 6-second rows by S1, 10:00:00 on, every 10 s."""
    t0 = dt.datetime.combine(dt.date.today(), dt.time(10))
    rows = [dict(id=f"r{i}", start=core.iso(t0 + dt.timedelta(seconds=10 * i)), end=core.iso(t0 + dt.timedelta(seconds=10 * i + 6)),
                 speaker="S1", text=f"合成句子{i}", source="rec-a") for i in range(n)]
    c = dict(id="c" + t0.strftime("%Y%m%d%H%M%S"), start=rows[0]["start"], end=rows[-1]["end"], device="rec-a",
             participants=["S1"], sentences=n, speech_s=6.0 * n)
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c)
    return c, rows


def hms(r):
    return r["start"][11:19]


@pytest.mark.parametrize("on", [False, True])
def test_prompt_and_version_follow_the_switch(store, on):
    store.media_view = on
    p = summary.instructions([], "2026-11-19T10:00:00")
    assert summary.version() == (19 if on else summary.PROMPT_VERSION)
    assert ("broadcast_spans" in p) == on and ("免提或外放打电话" in p) == on and ("视频、电视、直播、播客、广播、音乐" in p) != on
    assert summary.SYSTEM in p or on, "off: the v18 prompt, byte for byte"
    assert summary.AUDIENCE in summary.SYSTEM, "split.py's audience rules stay the default ones"


def test_reply_kinds_anchored_and_counted(store):
    store.media_view = True
    c, rows = conversation()
    reply = {"title": "t", "summary": "s", "media_spans": [{"from": hms(rows[0]), "to": hms(rows[1])}],
             "broadcast_spans": [{"from": hms(rows[3]), "to": hms(rows[3])}, {"from": "bad", "to": hms(rows[4])}]}
    s = summary.result(c, rows, reply, {"model": "m"})
    assert s["prompt_version"] == 19 and s["broadcast_spans"] == [{"from": rows[3]["start"], "to": rows[3]["start"]}]
    assert s["media_share"] == 0.5, "media and broadcasts are both not live: 3 of 6 rows"
    assert [r["id"] for r in rows if profiles.heard(r, s)] == ["r2", "r4", "r5"], "a profile hears neither kind"
    assert summary.media_labels(rows, summary.not_live(s)) == {"S1"}


def test_keep_live_splits_a_span(store):
    c, rows = conversation()
    s = {"status": "done", "media_spans": [{"from": rows[0]["start"], "to": rows[4]["start"]}], "broadcast_spans": []}
    out = summary.keep_live(s, [{"from": rows[2]["start"], "to": rows[2]["start"]}], rows)
    assert out["media_spans"] == [{"from": rows[0]["start"], "to": rows[1]["start"]}, {"from": rows[3]["start"], "to": rows[4]["start"]}]
    assert out["live_spans"] == [{"from": rows[2]["start"], "to": rows[2]["start"]}] and out["media_share"] == round(4 / 6, 2)


def test_marked_live_stands_over_a_redo(store, monkeypatch):
    """改回现场 on a fold, then a rename's redo whose model says media again: the marked rows stay live."""
    store.media_view = True
    c, rows = conversation()
    whole = [{"from": rows[0]["start"], "to": rows[-1]["start"]}]
    db.put_summary({"conversation": c["id"], "status": "done", "model": "m", "media_spans": whole, "broadcast_spans": []})
    with pytest.raises(HTTPException):
        anyio.run(routes_api.mark_live, c["id"], {"from": rows[3]["start"]})
    s = anyio.run(routes_api.mark_live, c["id"], {"from": rows[0]["start"], "to": rows[2]["start"], "kind": "media"})
    assert s["media_spans"] == [{"from": rows[3]["start"], "to": rows[5]["start"]}] and s["media_share"] == 0.5

    async def again(conv, tags):  # the model's redo: all media, as before the mark
        return summary.result(c, rows, {"title": "t", "summary": "s", "media_spans": [{"from": hms(rows[0]), "to": hms(rows[-1])}]}, {"model": "m"})

    async def broadcast(message):
        raise Done

    monkeypatch.setattr(summaries, "summarize", again)
    monkeypatch.setattr(ws, "broadcast", broadcast)

    async def run():
        await routes_api.resummarize(c["id"])  # 重新生成, as a rename and the backfill ask: queue_summary(keep=True)
        # a mark while the redo is queued: the summary shown stays done, the mark is taken
        assert (await routes_api.mark_live(c["id"], {"from": rows[3]["start"], "to": rows[3]["start"], "kind": "media"}))["status"] == "done"
        await summaries.summarize_loop()

    with pytest.raises(Done):
        anyio.run(run)
    s = db.summary(c["id"])
    assert s["media_spans"] == [{"from": rows[4]["start"], "to": rows[5]["start"]}] and s["live_spans"] == [
        {"from": rows[0]["start"], "to": rows[2]["start"], "kind": "media"}, {"from": rows[3]["start"], "to": rows[3]["start"], "kind": "media"}], s


def test_undo_puts_a_run_back_in_its_kind(store):
    """恢复为外放 / 恢复为广播: the mark gone, its rows back in the spans they were in; one never made is a 404."""
    store.media_view = True
    c, rows = conversation()
    db.put_summary({"conversation": c["id"], "status": "done", "model": "m", "media_spans": [{"from": rows[0]["start"], "to": rows[1]["start"]}],
                    "broadcast_spans": [{"from": rows[3]["start"], "to": rows[4]["start"]}]})
    before = db.summary(c["id"])
    for x, k in ((rows[0], "media"), (rows[3], "broadcast")):
        anyio.run(routes_api.mark_live, c["id"], {"from": x["start"], "to": x["start"], "kind": k})
    s = db.summary(c["id"])
    assert [m["kind"] for m in s["live_spans"]] == ["media", "broadcast"] and s["media_share"] == round(2 / 6, 2)
    for x in (rows[0], rows[3]):
        s = anyio.run(routes_api.unmark_live, c["id"], {"from": x["start"], "to": x["start"]})
    assert (s["media_spans"], s["broadcast_spans"], s["live_spans"], s["media_share"]) == (
        before["media_spans"], before["broadcast_spans"], [], round(4 / 6, 2)), s
    with pytest.raises(HTTPException):
        anyio.run(routes_api.unmark_live, c["id"], {"from": rows[0]["start"], "to": rows[0]["start"]})


@pytest.mark.parametrize("on", [False, True])
def test_voiceprints_skip_lines_played_aloud(store, monkeypatch, on):
    """A rename (and a voice check's yes, the same learnable) teaches no line in media or a broadcast: with the switch."""
    store.media_view = on
    monkeypatch.setitem(core.state, "embed", lambda x: None)
    c, rows = conversation()
    db.put_summary({"conversation": c["id"], "status": "done", "media_spans": [{"from": rows[0]["start"], "to": rows[1]["start"]}],
                    "broadcast_spans": [{"from": rows[4]["start"], "to": rows[4]["start"]}]})
    got = [r["id"] for r in routes_speakers.learnable(c["id"], "张三", rows)]
    assert got == (["r2", "r3", "r5"] if on else [r["id"] for r in rows])


@pytest.mark.parametrize("on", [False, True])
def test_agent_transcript_marks_kinds(store, on):
    from alldayrec import mcp_server
    store.media_view = on
    c, rows = conversation(3)
    db.put_summary({"conversation": c["id"], "status": "done", "media_spans": [{"from": rows[0]["start"], "to": rows[0]["start"]}],
                    "broadcast_spans": [{"from": rows[1]["start"], "to": rows[1]["start"]}]})
    text = mcp_server.transcript(c["id"])
    assert ("S1（外放）: 合成句子0" in text and "S1（广播）: 合成句子1" in text and "S1: 合成句子2" in text) == on
    assert ("（外放）" in text) == on


def test_off_summary_has_no_new_key(store):
    """Off, a summary is v18's key for key: no broadcast_spans, from the model's reply or a short conversation's."""
    c, rows = conversation()
    s = summary.result(c, rows, {"title": "t", "summary": "s", "broadcast_spans": [{"from": hms(rows[0]), "to": hms(rows[0])}]}, {"model": "m"})
    assert "broadcast_spans" not in s and s["prompt_version"] == summary.PROMPT_VERSION and s["media_share"] == 0.0
    short = anyio.run(summary.summarize, {**c, "speech_s": 10}, rows)
    assert "broadcast_spans" not in short
    store.media_view = True
    assert "broadcast_spans" in anyio.run(summary.summarize, {**c, "speech_s": 10}, rows)


def test_marks_need_the_switch_and_a_run_of_their_kind(store):
    """Off, 改回现场 is not there (404); on, a mark names its kind and two rows, the first in a span of that kind (400)."""
    c, rows = conversation()
    db.put_summary({"conversation": c["id"], "status": "done", "model": "m", "media_spans": [{"from": rows[0]["start"], "to": rows[1]["start"]}],
                    "broadcast_spans": []})
    body = {"from": rows[0]["start"], "to": rows[1]["start"], "kind": "media"}
    for f in (routes_api.mark_live, routes_api.unmark_live):
        with pytest.raises(HTTPException) as e:
            anyio.run(f, c["id"], body)
        assert e.value.status_code == 404
    store.media_view = True
    for bad in ({"kind": None}, {"kind": "broadcast"}, {"from": rows[3]["start"], "to": rows[3]["start"]}, {"to": "2026-01-01T00:00:00.000"}):
        with pytest.raises(HTTPException) as e:
            anyio.run(routes_api.mark_live, c["id"], {**body, **bad})
        assert e.value.status_code == 400, bad
    assert "live_spans" not in db.summary(c["id"]), "nothing written by a refused mark"
    assert anyio.run(routes_api.mark_live, c["id"], body)["media_spans"] == []
