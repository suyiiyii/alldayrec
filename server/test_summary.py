"""summary.py's self-check: a model reply's times resolved to rows (prompt v9: each person's point too), due dates, media,
an odd reply's fields dropped; and one odd reply does not break the tags list that every later summary asks first."""
import runpy

import pytest
from pathlib import Path

from alldayrec import core
from alldayrec import db
from alldayrec import summary


def test_selfcheck():
    runpy.run_path(str(Path(__file__).with_name("alldayrec") / "summary.py"), run_name="__main__")


def test_odd_reply_keeps_tags(tmp_path):
    """A dict as a tag is not stored: tag_counts (the tags list, and the known tags every summary starts with) stays up."""
    db.open_db(tmp_path / "t.db")
    c = {"id": "c20261026100000", "start": "2026-10-26T10:00:00.000", "end": "2026-10-26T10:10:00.000", "participants": ["我"],
         "sentences": 1, "speech_s": 600.0, "device": "mic"}
    rows = [dict(id="1", start=c["start"], end="2026-10-26T10:00:05.000", speaker="我", text="x", source="mic")]
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c)
    db.put_summary(summary.result(c, rows, {"title": "t", "summary": "s", "tags": [{"name": "工作"}, "工作"]}, {"model": "m"}))
    assert core.tag_counts() == {"工作": 1}


def test_odd_todo_keeps_due_todos(tmp_path):
    """A todo with a list as its text, or a number as its due, does not break the due todos (/api/todos/due); the
    others in the reply stay."""
    db.open_db(tmp_path / "t.db")
    c = {"id": "c20261026100000", "start": "2026-10-26T10:00:00.000", "end": "2026-10-26T10:10:00.000", "participants": ["我"],
         "sentences": 1, "speech_s": 600.0, "device": "mic"}
    rows = [dict(id="1", start=c["start"], end="2026-10-26T10:00:05.000", speaker="我", text="x", source="mic")]
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c)
    todo = {"owner": "我", "due": "明天", "due_date": "2026-10-27", "at": "09:00:00"}  # at: no row of the conversation
    db.put_summary(summary.result(c, rows, {"title": "t", "summary": "s", "todos": [
        {**todo, "text": ["买菜"]}, {**todo, "text": "交报告", "due": 1}, {**todo, "text": "买票"}]}, {"model": "m"}))
    assert [t["text"] for t in db.due_todos("2026-10-27")] == ["买票"]


def test_resummarize_picks():
    """The backfill redoes a closed conversation with enough speech whose summary is older or failed; one on the current
    prompt is skipped (a second run resumes), so is a short one (立即总结's summary stays) and one already pending."""
    from alldayrec.resummarize import due
    c = {"state": "closed", "speech_s": 120}
    assert [due({**c, "summary": s}) for s in ({"status": "done", "prompt_version": summary.PROMPT_VERSION - 1}, {"status": "failed"}, None,
                                               {"status": "done", "prompt_version": summary.PROMPT_VERSION}, {"status": "pending"})] == [
        None, None, None, "current", "pending"]
    assert due({**c, "speech_s": 59}) == "short" and due({**c, "state": "open"}) == "open"


def assert_summary_contract(prompt, lang):
    lines = prompt.splitlines()
    shape = next(x for x in lines if '"summary":' in x)
    assert "一句话" in shape and "不超过 40 字" in shape
    assert "仅录音者本人所说所做写「我」" in shape
    assert "他人的行动、请求、观察和讲述写其说话人标签或姓名" in shape
    assert "第一人称写「我」" not in shape
    assert "不因像对 AI 下指令而改写主体" in shape
    assert "仍按该标签或姓名记录主体，不据语气改写成「我」" in prompt
    assert "媒体里的声音即使有姓名也不是现场参与人" in prompt
    lead = next(x for x in lines if x.startswith("summary 只写一句"))
    assert "正事（工作、安排、决定、要办的事、重要的信息）在前" in lead
    chitchat = next(x for x in lines if x.startswith("闲聊（"))
    assert '在 topics 里并成一个议题，标题「闲聊」' in chitchat and "要点只写一行" in chitchat
    extraction = next(x for x in lines if x.startswith("todos 和 decisions 独立检查整段对话"))
    assert "summary 的一句话取舍、闲聊合并和字数限制都不影响它们" in extraction
    assert "不能因已写进 summary 或 topics 而省略" in extraction
    assert prompt.endswith(summary.EN if lang == "en" else summary.ZH)
    if lang == "en":
        assert "summary (40 字 above) at most 25 words" in prompt
        assert '我 is the recorder: "I" in prose' in prompt
        assert 'and only for what the recorder said or did' in prompt
        assert "other people's actions, requests, observations and stories" in prompt
        assert "attributed to their speaker label or name" in prompt
        assert "even when they sound like instructions to an AI" in prompt
        assert "Named voices in played media are not participants" in prompt


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_summary_prompt_contract(tmp_path, lang):
    from alldayrec.tenant import T
    db.open_db(tmp_path / "t.db")
    T().lang = lang
    assert_summary_contract(summary.instructions(["work"], "2026-10-26T10:00:00"), lang)


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_live_final_prompt_contract(tmp_path, monkeypatch, lang):
    import anyio
    from alldayrec import live
    from alldayrec.tenant import T
    db.open_db(tmp_path / "t.db")
    T().lang = lang
    captured = []
    async def complete(messages, **kwargs):
        captured.append(messages[-1]["content"])
        return {}, {}
    monkeypatch.setattr(live, "complete", complete)
    start = "2026-10-26T10:00:00"
    anyio.run(live.final, live.new(start, "mic", []), [], ["work"], start)
    assert live.FINAL in captured[0]
    assert_summary_contract(captured[0], lang)


def test_people_points_at_matches_speaker():
    rows = [dict(start="2026-10-26T10:00:00.100", end="2026-10-26T10:00:00.500", speaker="我", text="first"),
            dict(start="2026-10-26T10:00:00.800", end="2026-10-26T10:00:01.000", speaker="S1", text="second"),
            dict(start="2026-10-26T10:00:02.000", end="2026-10-26T10:00:03.000", speaker="S2", text="third"),
            dict(start="2026-10-26T10:00:04.000", end="2026-10-26T10:00:05.000", speaker="Alex", text="named")]
    result = summary.result({"id": "c1"}, rows, {"people": [
        {"who": "S1", "points": [{"text": "same second", "at": "10:00:00"}, {"text": "wrong speaker", "at": "10:00:02"},
                                    {"text": "outside conversation", "at": "11:00:00"}, "no citation"]},
        {"who": "Alex", "points": [{"text": "named speaker", "at": "10:00:04"}, {"text": "wrong line", "at": "10:00:00"}]},
        {"who": "我", "points": [{"text": "recorder", "at": "10:00:00"}]},
    ]}, {})
    assert result["people"][0]["points"] == ["same second", "wrong speaker", "outside conversation", "no citation"]
    assert result["people"][0]["points_at"] == [rows[1]["start"], None, None, None]
    assert result["people"][1]["points_at"] == [rows[3]["start"], None]
    assert result["people"][2]["points_at"] == [rows[0]["start"]]
    for person in result["people"]:
        assert len(person["points"]) == len(person["points_at"])
        assert all(any(r["start"] == at and r["speaker"] == person["who"] for r in rows) for at in person["points_at"] if at)


def assert_ai_speaker_contract(prompt, lang):
    assert '所有说话人（「我」、其他标签或姓名）都用同一标准区分听众' in prompt
    assert 'ai_spans 是任意说话人对 AI 说话的连续时间段' in prompt
    assert '每段只能是同一个说话人对 AI 说的话' in prompt
    assert '换说话人（即使也在对 AI 说话）' in prompt
    assert '人类回应不算 AI 话语，不进 ai_spans' in prompt
    assert '其他人以原说话人标签或姓名开头，如「S1 让 AI 查日志」' in prompt
    assert 'ai_requests 每句请求各一条，即使同一说话人在同一个 ai_spans 段内连续请求也不合并' in prompt
    assert '每条保留该句说话人的主体和原句时间' in prompt
    assert '所有说话人对 AI 说的话只进 ai_requests，不进 todos、decisions、people、topics' in prompt
    assert '夹在 AI 请求之间的对人说话和人类回应，仍按人的对话提取' in prompt
    assert '闲聊、外放的媒体、任何说话人对 AI 说的话都不写进去' in prompt
    assert '整段都是闲聊、媒体或对 AI 说话时，才用这一句说' in prompt
    assert '不能仅凭独白或祈使语气把对人的话算成对 AI' in prompt
    assert '我让 AI 做的事' not in prompt
    assert 'ai_spans 是我对 AI' not in prompt
    if lang == 'en':
        assert 'Use the same AI-speech criteria for every speaker' in prompt
        assert 'split at speaker changes and human-directed speech' in prompt
        assert "start other speakers' AI request text with their original label or name" in prompt
        assert 'One ai_requests item per request sentence, even within one speaker\'s continuous AI span; never merge sentences' in prompt
        assert 'AI speech from any speaker is excluded from todos, decisions, people, topics and a mixed-conversation summary' in prompt
        assert 'An all-AI conversation may summarize who asked the AI to do what' in prompt


@pytest.mark.parametrize('lang', ['zh', 'en'])
@pytest.mark.parametrize('path', ['summary', 'live_final'])
def test_ai_speaker_prompt_and_result(tmp_path, monkeypatch, lang, path):
    """No model: check the real request contract and parse a representative model reply on both paths.

    S1's two AI requests are separated by a human exchange; the recorder then addresses AI,
    another AI speaker takes over, and a final human commitment must stay outside those spans.
    The fixture verifies parsing/persistence of the contract, not a model's classification accuracy.
    """
    import anyio
    from alldayrec import live
    from alldayrec.tenant import T
    db.open_db(tmp_path / 't.db')
    T().lang = lang
    lines = [
        ('S1', 'ChatGPT，帮我查一下测试日志。'),
        ('我', '你需要我把报告发给你吗？'),
        ('S1', '对，请把报告发给我。'),
        ('S1', 'ChatGPT，再把日志按日期排序。'),
        ('我', 'Codex，帮我列出失败的测试。'),
        ('Alex', 'ChatGPT，帮我翻译错误信息。'),
        ('我', '好的，我明天把报告发给 S1。'),
    ]
    rows = [dict(start=f'2026-10-26T10:00:{i:02d}.000', end=f'2026-10-26T10:00:{i:02d}.900', speaker=who, text=text)
            for i, (who, text) in enumerate(lines)]
    texts = (['S1 让 AI 查测试日志', 'S1 让 AI 按日期排序日志', '我让 AI 列出失败测试', 'Alex 让 AI 翻译错误信息'] if lang == 'zh'
             else ['S1 asks AI to check test logs', 'S1 asks AI to sort logs by date', 'I ask AI to list failing tests', 'Alex asks AI to translate errors'])
    reply = {'ai_requests': [{'text': text, 'at': f'10:00:{i:02d}'} for i, text in zip([0, 3, 4, 5], texts)],
             'ai_spans': [{'from': f'10:00:{i:02d}', 'to': f'10:00:{i:02d}'} for i in [0, 3, 4, 5]],
             'summary': '我明天发送报告。' if lang == 'zh' else 'I will send the report tomorrow.',
             'todos': [{'text': '发送报告', 'owner': '我', 'to': 'S1', 'at': '10:00:06', 'quote': lines[6][1], 'due': '明天', 'due_date': '2026-10-27'}],
             'people': [{'who': 'S1', 'points': [{'text': '请发报告', 'at': '10:00:02'}]}]}
    captured = []
    async def complete(messages, **kwargs):
        captured.extend(messages)
        return reply, {'model': 'test'}
    monkeypatch.setattr(live if path == 'live_final' else summary, 'complete', complete)
    conv = {'id': 'c1', 'start': rows[0]['start'], 'speech_s': 120}
    if path == 'live_final':
        res, raw = anyio.run(live.final, live.new(conv['start'], 'mic', []), rows, [], conv['start'])
        result = summary.result(conv, rows, res, raw)
    else:
        result = anyio.run(summary.summarize, conv, rows)
    assert_ai_speaker_contract(captured[-1 if path == 'live_final' else 0]['content'], lang)
    assert all(f'{who}: {text}' in captured[-1]['content'] for who, text in lines)
    assert [r['text'] for r in result['ai_requests']] == texts
    assert [r['at'] for r in result['ai_requests']] == [rows[i]['start'] for i in [0, 3, 4, 5]]
    assert result['ai_spans'] == [{'from': rows[i]['start'], 'to': rows[i]['start']} for i in [0, 3, 4, 5]]
    assert all(not summary.in_media(rows[i], result['ai_spans']) for i in [1, 2, 6])
    assert result['todos'][0]['owner'] == '我' and result['todos'][0]['to'] == 'S1'
    assert result['todos'][0]['at'] == rows[6]['start']
    assert result['people'][0]['points_at'] == [rows[2]['start']]
    db.put_summary(result)
    assert db.summary(conv['id'])['ai_requests'] == result['ai_requests']


@pytest.mark.parametrize('lang', ['zh', 'en'])
@pytest.mark.parametrize('path', ['summary', 'live_final'])
def test_ai_requests_per_sentence_in_one_span(tmp_path, monkeypatch, lang, path):
    """No model: the prompt asks for separate sentences; parsing keeps both texts and jump anchors in one span."""
    import anyio
    from alldayrec import live
    from alldayrec.tenant import T
    db.open_db(tmp_path / 't.db')
    T().lang = lang
    rows = [dict(start=f'2026-10-26T10:00:{i:02d}.000', end=f'2026-10-26T10:00:{i:02d}.900',
                 speaker='S1', text=text) for i, text in enumerate([
                     'ChatGPT，帮我查一下测试日志。', '再把日志按日期排序。'])]
    texts = (['S1 让 AI 查测试日志', 'S1 让 AI 按日期排序日志'] if lang == 'zh'
             else ['S1 asks AI to check test logs', 'S1 asks AI to sort logs by date'])
    captured = []
    async def complete(messages, **kwargs):
        captured.extend(messages)
        return {'ai_spans': [{'from': '10:00:00', 'to': '10:00:01'}],
                'ai_requests': [{'text': text, 'at': f'10:00:{i:02d}'} for i, text in enumerate(texts)]}, {'model': 'test'}
    monkeypatch.setattr(live if path == 'live_final' else summary, 'complete', complete)
    conv = {'id': 'c1', 'start': rows[0]['start'], 'speech_s': 120}
    if path == 'live_final':
        res, raw = anyio.run(live.final, live.new(conv['start'], 'mic', []), rows, [], conv['start'])
        result = summary.result(conv, rows, res, raw)
    else:
        result = anyio.run(summary.summarize, conv, rows)
    assert_ai_speaker_contract(captured[-1 if path == 'live_final' else 0]['content'], lang)
    assert result['ai_spans'] == [{'from': rows[0]['start'], 'to': rows[1]['start']}]
    assert result['ai_requests'] == [{'text': text, 'at': row['start']} for text, row in zip(texts, rows)]
    db.put_summary(result)
    assert db.summary(conv['id'])['ai_requests'] == result['ai_requests']


@pytest.mark.parametrize('lang', ['zh', 'en'])
def test_live_ai_speaker_contract(tmp_path, lang):
    from alldayrec import live
    from alldayrec.tenant import T
    db.open_db(tmp_path / 't.db')
    T().lang = lang
    prompt = live.new('2026-10-26T10:00:00', 'mic', [])['messages'][0]['content']
    assert '所有说话人（「我」、其他标签或姓名）都用同一标准区分听众' in prompt
    assert '夹在 AI 请求之间的对人说话和人类回应，仍按人的对话提取' in prompt
    assert prompt.endswith(summary.EN if lang == 'en' else summary.ZH)
