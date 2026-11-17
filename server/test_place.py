"""Where the recorder probably was (tenant "place"): the reply is checked against what the model was given, the place
goes into the summary with nothing else of it changed, and with the flag off nothing happens. Made-up rows only."""
import anyio
import pytest

from alldayrec import core, db, place, profiles, sound_events, summaries, summary, tenant

CID = "c20261020100000"
START = "2026-10-20T10:00:00.000"
TEXTS = [("我", "上高速了，前面收费站"), ("S1", "导航说还有二十公里"), ("S2", "欢迎来到海边乐园"), ("我", "帮我查一下明天的会"), ("S1", "嗯，好。"),
         ("S2", "回家吧回家吧，回到温暖的家"), ("我", "我到家了，记一下")]
MUSIC, DICTATED = 5, 6


def rows():
    return [dict(id=f"r{i}", start=f"2026-10-20T10:00:{i:02}.000", end=f"2026-10-20T10:00:{i:02}.900", speaker=w, text=x, source="mic",
                 music=i == MUSIC, dictation="notes" if i == DICTATED else None) for i, (w, x) in enumerate(TEXTS)]


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
        {"kind": "line", "at": "10:00:05"}, {"kind": "line", "at": "10:00:06"},  # a song's words, a dictation
        {"kind": "sound", "event": "vehicle"}, {"kind": "sound", "event": "music"},  # music: no minutes measured
        {"kind": "sound", "event": "typing"},  # not an event the layer gives
        {"kind": "line", "at": "10:00:01"}]}, r, SPANS, {"covered": 20, "vehicle": 18.4, "speech": 5})
    assert got == {"label": "car", "detail": "高速上", "confidence": "high", "evidence": [
        {"kind": "line", "at": r[0]["start"], "who": "我", "text": "上高速了，前面收费站"},
        {"kind": "sound", "event": "vehicle", "minutes": 18},  # measured, not the model's number
        {"kind": "line", "at": r[1]["start"], "who": "S1", "text": "导航说还有二十公里"}]}, got
    assert place.checked({"label": "车上", "detail": "空", "confidence": "medium", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                         r, SPANS, None)["detail"] == ""  # "空": the model's way of writing none
    # a detail is kept only from a kept sentence: not a name only a video said, not one in no sentence at all
    for detail in ("海边乐园", "温泉酒店", "家"):
        got = place.checked({"label": "其他", "detail": detail, "confidence": "high", "evidence": [
            {"kind": "line", "at": "10:00:00"}, {"kind": "line", "at": "10:00:02"}]}, r, SPANS, None)
        assert got["label"] == "other" and got["detail"] == "" and len(got["evidence"]) == 1, (detail, got)
    # a sensitive place: none shown, whether in the detail (even one no sentence says) or in a cited sentence
    med = [*r, dict(r[0], id="r9", start="2026-10-20T10:00:09.000", text="门诊三楼人好多，等叫号")]
    for res in ({"label": "其他", "detail": "妇产医院", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                {"label": "其他", "detail": "", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:09"}]},
                {"label": "其他", "detail": "Church", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:00"}]}):
        assert place.checked(res, med, SPANS, None) == {"label": None}, res
    # by stem: the names the review found shown with a list of whole names
    for text, detail in (("我们到社区卫生服务中心了", "卫生服务中心"), ("到村卫生室了排队", "卫生室"), ("这家中医馆人好多", "中医馆"),
                         ("在病房里陪床呢", "病房"), ("体检中心排队好久", "体检中心"), ("疾控中心打疫苗排队", "疾控中心"),
                         ("我们在教会聚会", "教会")):
        got = place.checked({"label": "其他", "detail": detail, "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                            [dict(r[0], text=text)], {}, None)
        assert got == {"label": None}, (text, got)
        got = place.checked({"label": "其他", "detail": "", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                            [dict(r[0], text=text)], {}, None)
        assert got == {"label": None}, ("in the sentence alone", text, got)
    # one trailing 上/里 at most, and two characters left: 海边 is not 海 of 上海
    sea = [dict(r[0], text="我们在上海呢，挺热的")]
    assert place.checked({"label": "户外", "detail": "海边", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                         sea, {}, None)["detail"] == ""
    assert "医院" not in place.SYSTEM.split("敏感场所")[0], "no medical place as an example of a detail"
    # a reply of the wrong shape: nothing that holds, no error
    for bad in ({"label": "车上", "confidence": "high", "evidence": 5},
                {"label": "车上", "confidence": "high", "evidence": [{"kind": "line", "at": ["10:00:00"]}, {"kind": "sound", "event": ["vehicle"]}]}):
        assert place.checked(bad, r, SPANS, {"covered": 20, "vehicle": 18}) == {"label": None}
    # unsure, low, an unknown label, or nothing that holds: no place shown
    for res in ({"label": "不确定", "confidence": "low", "evidence": []},
                {"label": "车上", "confidence": "low", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                {"label": "火星", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:00"}]},
                {"label": "户外", "confidence": "high", "evidence": [{"kind": "line", "at": "10:00:02"}]},
                {"label": "车上", "confidence": "high", "evidence": [{"kind": "sound", "event": "vehicle"}]},  # no sound data
                {"label": "车上", "confidence": "high", "evidence": [{"kind": "sound", "event": "vehicle"}], "_ev": True},  # sound alone
                ["not an object"]):
        assert place.checked(res, r, SPANS, {"covered": 20, "vehicle": 18} if isinstance(res, dict) and res.get("_ev") else None) == {"label": None}, res


def test_prompt_marks_media_and_ai_lines(tmp_path):
    db.open_db(tmp_path / "t.db")
    conv = {"start": START, "end": "2026-10-20T10:00:03.900"}
    user = place.prompt(conv, rows(), SPANS, None)[1]["content"]
    assert "[10:00:02] S2（外放）: 欢迎来到海边乐园" in user and "[10:00:03] 我（对AI）: 帮我查一下明天的会" in user
    assert "[10:00:05] S2（歌词）: 回家吧" in user and "[10:00:06] 我（口述）: 我到家了" in user
    assert "[10:00:00] 我: 上高速了" in user and "没有可用的环境声音" in user and "2026-10-20（周二）10:00" in user
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
    # pending again with its created_at (a split, late audio: split.py, late_merge.py): the old rows' place not kept
    db.put_summary({k: v for k, v in db.summary(CID).items() if k != "place"} | {"status": "pending"})
    made_from = {**db.summary(CID), "status": "done"}
    assert place.keep(CID, made_from, {"label": "car"}) is False and "place" not in db.summary(CID)


def test_events_from_the_sound_layer(monkeypatch):
    asked = []

    def tags(c):
        asked.append(c)
        return {"covered": 30, "events": [{"event": "vehicle", "minutes": 18, "conf": 0.8}, {"event": "music", "minutes": 6, "conf": 0.5, "wrong": 7}]}
    monkeypatch.setattr(sound_events, "tags", tags)
    conv = {"device": "mic", "start": START, "end": "2026-10-20T10:00:03.900"}
    assert place.events_of(conv) == {"covered": 30, "vehicle": 18}, "the page's tags, not music the owner said 不对 of"
    assert asked == [conv]
    monkeypatch.setattr(sound_events, "tags", lambda c: None)  # off, no scores or no tag
    assert place.events_of(conv) is None
