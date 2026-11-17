"""Where the recorder probably was (tenant "place"): the reply is checked against what the model was given, the place
goes into the summary with nothing else of it changed, and with the flag off nothing happens. Made-up rows only."""
import anyio
import pytest

from alldayrec import core, db, place, profiles, summaries, summary, tenant

CID = "c20261020100000"
START = "2026-10-20T10:00:00.000"
TEXTS = [("我", "上高速了，前面收费站"), ("S1", "导航说还有二十公里"), ("S2", "欢迎来到海边乐园"), ("我", "帮我查一下明天的会"), ("S1", "嗯，好。")]


def rows():
    return [dict(id=f"r{i}", start=f"2026-10-20T10:00:{i:02}.000", end=f"2026-10-20T10:00:{i:02}.900", speaker=w, text=x, source="mic")
            for i, (w, x) in enumerate(TEXTS)]


SPANS = {"media_spans": [{"from": "2026-10-20T10:00:02.000", "to": "2026-10-20T10:00:02.000"}],
         "ai_spans": [{"from": "2026-10-20T10:00:03.000", "to": "2026-10-20T10:00:03.000"}]}


def test_checked_keeps_only_what_holds():
    r = rows()
    got = place.checked({"label": "车上", "detail": "高速上", "confidence": "high", "evidence": [
        {"kind": "line", "at": "10:00:00"}, {"kind": "line", "at": "10:00:00"},  # twice: once
        {"kind": "line", "at": "10:00:02"},  # a video's line: no place of the recorder's
        {"kind": "line", "at": "10:00:03"},  # said to an AI
        {"kind": "line", "at": "10:09:99"}, "x",  # no such line, not an object
        {"kind": "line", "at": "10:00:04"},  # 嗯，好。: says nothing of a place
        {"kind": "sound", "event": "vehicle"}, {"kind": "sound", "event": "music"},  # music: no minutes measured
        {"kind": "sound", "event": "typing"},  # not an event the layer gives
        {"kind": "line", "at": "10:00:01"}]}, r, SPANS, {"covered": 20, "vehicle": 18.4, "speech": 5})
    assert got == {"label": "car", "detail": "高速上", "confidence": "high", "evidence": [
        {"kind": "line", "at": r[0]["start"], "who": "我", "text": "上高速了，前面收费站"},
        {"kind": "sound", "event": "vehicle", "minutes": 18},  # measured, not the model's number
        {"kind": "line", "at": r[1]["start"], "who": "S1", "text": "导航说还有二十公里"}]}, got
    assert place.checked({"label": "车上", "detail": "空", "confidence": "medium", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                         r, SPANS, None)["detail"] == ""  # "空": the model's way of writing none
    # unsure, low, an unknown label, or nothing that holds: no place shown
    for res in ({"label": "不确定", "confidence": "low", "evidence": []},
                {"label": "车上", "confidence": "low", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                {"label": "火星", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                {"label": "户外", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:02"}]},
                {"label": "车上", "confidence": "high", "evidence": [{"kind": "sound", "event": "vehicle"}]},  # no sound data
                ["not an object"]):
        assert place.checked(res, r, SPANS, None) == {"label": None}, res


def test_prompt_marks_media_and_ai_lines(tmp_path):
    db.open_db(tmp_path / "t.db")
    conv = {"start": START, "end": "2026-10-20T10:00:03.900"}
    user = place.prompt(conv, rows(), SPANS, None)[1]["content"]
    assert "[10:00:02] S2（外放）: 欢迎来到海边乐园" in user and "[10:00:03] 我（对AI）: 帮我查一下明天的会" in user
    assert "[10:00:00] 我: 上高速了" in user and "没有环境声音数据" in user and "2026-10-20（周二）10:00" in user
    assert "（引擎或路噪，车、地铁、飞机都算）（vehicle）18 分钟" in place.sound_block({"covered": 30, "vehicle": 18, "music": 0})


class Model:
    """The summary call, then the place call (and any other call the worker makes after a summary)."""
    def __init__(self):
        self.kinds = []

    async def chat(self, text, *args):
        return {"title": "开车去海边", "summary": "去海边", "todos": [], "media_spans": [{"from": "10:00:02", "to": "10:00:02"}],
                "ai_spans": [{"from": "10:00:03", "to": "10:00:03"}]}, {"model": "m"}

    async def complete(self, messages, timeout=300, kind="summary", **extra):
        self.kinds.append(kind)
        if kind == "place":
            return {"label": "车上", "detail": "高速上", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:00"}]}, {"model": "m"}
        return {}, {"model": "m"}


@pytest.fixture
def model(tmp_path, monkeypatch):
    db.open_db(tmp_path / "test.db")
    r = rows()
    with db.tx() as t:
        db.insert_sentences(t, r)
        db.insert_conversation(t, dict(id=CID, start=START, end=r[-1]["end"], participants=["我", "S1", "S2"],
                                       sentences=len(r), speech_s=120, device="mic"))
    monkeypatch.setitem(core.state, "embed", None)
    m = Model()
    monkeypatch.setattr(summary, "chat", m.chat)
    monkeypatch.setattr(summary, "complete", m.complete)

    async def quiet(msg):
        pass
    monkeypatch.setattr(summaries.ws, "broadcast", quiet)
    return m


def summarize_once():
    async def run():
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(summaries.summarize_loop)
                core.queue_summary(db.conversation(CID))
                while (db.summary(CID) or {}).get("status") != "done":
                    await anyio.sleep(0.01)
                await anyio.wait_all_tasks_blocked()
                tg.cancel_scope.cancel()
    anyio.run(run)


def test_off_no_call_and_no_field(model):
    assert tenant.T().place is False
    summarize_once()
    s = db.summary(CID)
    assert "place" not in s and "place" not in model.kinds and s["title"] == "开车去海边"


def test_on_adds_only_place(model):
    tenant.T().place = True
    summarize_once()
    s = db.summary(CID)
    assert model.kinds.count("place") == 1
    assert s["place"]["label"] == "car" and s["place"]["evidence"][0]["text"] == "上高速了，前面收费站", s["place"]
    assert s["place"]["prompt_version"] == place.PROMPT_VERSION and s["place"]["model"] == "m"
    # the rest of the summary is the one the summary call wrote; a v2 profile's hash of it is the same as without a place
    without = {k: v for k, v in s.items() if k != "place"}
    r = core.conversation_rows(db.conversation(CID))
    assert profiles.sha(r, s) == profiles.sha(r, without) and profiles.looked(s)
    assert s["media_spans"] == [{"from": r[2]["start"], "to": r[2]["start"]}]
    # once it has one, a later pass makes no call; a summary without a model call (too short) gets none
    assert anyio.run(place.place, db.conversation(CID)) is None and model.kinds.count("place") == 1


def test_not_kept_on_a_replaced_summary(model):
    tenant.T().place = True
    db.put_summary({"conversation": CID, "status": "done", "model": "m", "title": "a", "created_at": "2026-10-20T11:00:00"})
    made_from = db.summary(CID)
    db.put_summary({**made_from, "title": "b", "created_at": "2026-10-20T11:05:00"})  # a redo meanwhile
    assert place.keep(CID, made_from, {"label": None}) is False and "place" not in db.summary(CID)
    assert place.keep(CID, db.summary(CID), {"label": None}) is True and db.summary(CID)["title"] == "b"
