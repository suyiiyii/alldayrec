"""Voice tools (tenant "voice_tools", voice_tools.py): a sentence's voiceprint kept or made from the audio and never
written, a stretch of a mixed sentence, the compare and nearest answers (a person's sample made without the sentence
that taught it, a label's own sentence said), sentence references; off: not listed, refused, 404; the limits (a busy
call refused at once, never two inside the model, an infer service tried once). Synthetic voices only: the audio
of a time says whose it is, the stand-in model sums those voices."""
import contextvars
import datetime as dt
import json
import threading
import time

import numpy as np
import pytest
from starlette.testclient import TestClient

from alldayrec import compute
from alldayrec import core
from alldayrec import db
from alldayrec import mcp_server as m
from alldayrec import server
from alldayrec import voice_tools as vt
from alldayrec.tenant import T

SR = 16000
T0 = dt.datetime(2026, 10, 1, 10)
A, B, C = (np.eye(192)[i] for i in range(3))  # 我, 王五, someone else
CID = "c20261001100000"
# whose voice each second of mic's audio is: 0-3 A (s0); 3-9 A then 7-9 B (s1, mixed); 10-13 B (s2); 20-40 C (s3)
VOICE = [(0, 7, 1), (7, 9, 2), (10, 13, 2), (20, 40, 3)]


def iso(s: float) -> str:
    return core.iso(T0 + dt.timedelta(seconds=s))


def cut(a, b, fmt, device=None):
    """mic's samples of [a, b]: each the number of its voice (0: silence), none past 40 s or of another device."""
    if device != "mic" or (a - T0).total_seconds() >= 40:
        return None
    t = (a - T0).total_seconds() + np.arange(round((b - a).total_seconds() * SR)) / SR
    x = np.zeros(len(t), np.int16)
    for lo, hi, k in VOICE:
        x[(t >= lo) & (t < hi)] = k
    return x


def embed(x):
    """The stand-in model: its voices summed by how many samples each has."""
    return vt.unit(sum(np.count_nonzero(x == k) * v for k, v in ((1, A), (2, B), (3, C))))


@pytest.fixture
def store(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / "t.db")
    t.voice_tools, t.owner = True, {"m": A}
    monkeypatch.setattr(vt.audio, "cut", cut)
    made = []
    monkeypatch.setitem(core.state, "embed", lambda x: made.append(len(x)) or embed(x))
    rows = [dict(id=f"f{i}", start=iso(a), end=iso(b), speaker=s, text=f"第 {i} 句", source="mic")
            for i, (a, b, s) in enumerate([(0, 3, "我"), (3, 9, "我"), (10, 13, "王五"), (20, 40, "S1")])]
    with db.tx() as c:
        ids = db.insert_sentences(c, rows)
        db.insert_sentences(c, [dict(id="f0", start=iso(0), end=iso(3), speaker="我", text="另一台", source="phone")])
        db.insert_conversation(c, dict(id=CID, start=iso(0), end=iso(40), participants=["我", "王五", "S1"], speech_s=35,
                                       sentences=4, device="mic"))
        db.insert_voices(c, [{"conv": CID, "label": "S1", "e": C}, {"conv": CID, "label": "我", "e": A}])
        c.execute("UPDATE voices SET used = ? WHERE label = '我'", (json.dumps([[iso(0), iso(3), "mic"]]),))
        # 王五 taught by s2 and one sentence elsewhere (a little of A in it): without s2 its sample is that one
        for row, e in ((ids[2], B), (9999, vt.unit(B + 0.3 * A))):
            c.execute("INSERT INTO enrolled VALUES ('王五', ?, ?, 1, ?)", (CID if row == ids[2] else "c-other", row, db.blob(e)))
        c.execute("INSERT INTO people VALUES ('王五', 2, ?)", (db.blob(B),))
    return {"t": t, "ids": ids, "made": made}


def tool(name, **args):
    status, out = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, {})
    r = out.get("result") or {}
    return (r.get("content") or [{}])[0].get("text"), r.get("isError"), r.get("structuredContent"), out.get("error")


def names(write=True):
    return [t["name"] for t in m.tool_list(write)]


def test_off_is_as_before(store, monkeypatch):
    store["t"].voice_tools = False
    assert not set(m.VOICE) & set(names()) and not set(m.VOICE) & set(names(False))
    before = [t for t in m.TOOL_LIST if t["name"] not in m.VOICE]
    monkeypatch.setattr(m.sound_events, "model", lambda: "x")
    assert m.tool_list(True) == before, "everything else exactly as before"
    for n in m.VOICE:
        assert tool(n, sentence="f1")[3]["code"] == -32602
    monkeypatch.setattr(core, "TENANTS", [store["t"]])
    c = TestClient(server.app, raise_server_exceptions=False)
    assert [c.get(f"/api/voiceprint{p}?sentence=f1").status_code for p in ("", "/compare", "/nearest")] == [404] * 3
    store["t"].voice_tools = True
    assert set(m.VOICE) <= set(names(False)), "read tools: a read-only link gets them"
    assert c.get("/api/voiceprint?sentence=f1").status_code == 200


def test_voiceprint_made_from_the_audio_and_not_kept(store):
    text, bad, sc, _ = tool("voiceprint", sentence=f"{CID}@10:00:10")
    assert not bad and "刚从录音算的声纹" in text and "王五" in text
    v = sc["voiceprint"]
    assert v["from"] == "audio" and v["span"] == [0, 3.0] and v["sentence"]["id"] == "f2" and float(np.dot(v["e"], B)) > 0.999
    assert db.conn().execute("SELECT count(*) FROM seg_emb").fetchone()[0] == 0, "nothing written"
    assert max(store["made"]) <= (vt.MAX_S + 0.4) * SR, store["made"]
    long = vt.voiceprint(f"{CID}@10:00:20")  # 20 s: its first 15 s
    assert long["span"] == [0, 15.0] and long["seconds"] == 20


def test_kept_voiceprints_with_their_parts(store):
    """seg_emb's rows of the mixed sentence (two parts), not the audio's: each part scored on its own."""
    r = db.conn().execute("SELECT rowid, start FROM sentences WHERE id = 'f1' AND source = 'mic'").fetchone()
    for k, (a, b, e) in enumerate([(3, 7, A), (7, 9, B)], 1):
        db.conn().execute("INSERT INTO seg_emb VALUES ('mic', ?, ?, ?, 'SPEAKER_0', ?, ?, 0, ?, 0, ?)",
                          (db.ms(iso(a)), db.ms(iso(b)), k, vt.model(), np.asarray(e, np.float16).tobytes(), r[0], r[1]))
    db.conn().execute("INSERT INTO seg_emb VALUES ('mic', 0, 1, 0, NULL, 'old@0', ?, 0, ?, 1, ?)", (np.asarray(C, np.float16).tobytes(), r[0], r[1]))
    store["made"].clear()
    v = vt.voiceprint(f"{CID}@10:00:03")
    assert v["from"] == "seg_emb" and [(p["from"], p["to"]) for p in v["parts"]] == [(0, 4), (4, 6)] and not store["made"]
    assert float(v["e"] @ C) == 0, "another model's voiceprint is not taken"
    text, bad, _, _ = tool("voice_compare", sentence=f"{CID}@10:00:03", person="我")
    assert not bad and "第 0–4 秒 1.0" in text and "第 4–6 秒 0.0" in text and "两个人" in text, text
    text, _, _, _ = tool("voice_nearest", sentence=f"{CID}@10:00:03")
    assert "第 4–6 秒这段最像 王五" in text, text


def test_a_stretch_of_a_mixed_sentence(store):
    me, him = (vt.compare(f"{CID}@10:00:03", a, b, person=p)["score"] for a, b, p in ((0, 4, "我"), (4.2, 6, "王五")))
    assert me > 0.99 and him > 0.9, (me, him)
    whole = vt.compare(f"{CID}@10:00:03", person="我")["score"]
    assert 0.5 < whole < 0.99, "the whole sentence hides the second voice"
    s = vt.compare(f"{CID}@10:00:03", 0, 4, other=f"{CID}@10:00:03", other_from=4.2, other_to=6)
    assert s["score"] < 0.3 and s["with"]["span"] == [4.2, 6]


@pytest.mark.parametrize("frm, to, says", [
    (0, 0.3, "0.5–15 秒"), (5, 7, "≤ 6.00"), (-1, 2, "≤ 6.00"), (3, 3, "≤ 6.00"), (None, 6.5, "≤ 6.00")])
def test_bad_stretch(store, frm, to, says):
    text, bad, _, _ = tool("voiceprint", sentence=f"{CID}@10:00:03", **{k: v for k, v in (("from_s", frm), ("to_s", to)) if v is not None})
    assert bad and says in text, text


def test_over_15_s(store):
    text, bad, _, _ = tool("voiceprint", sentence=f"{CID}@10:00:20", from_s=1, to_s=17)
    assert bad and "0.5–15 秒" in text
    assert not tool("voiceprint", sentence=f"{CID}@10:00:20", from_s=2, to_s=17)[1]


def test_nearest_leaves_out_what_the_sentence_taught(store):
    x = vt.nearest(f"{CID}@10:00:10")
    top = x["nearest"][0]
    assert top["name"] == "王五" and top["left_out"] and top["score"] < 0.99, "its sample made without s2: B + a little A"
    assert x["known"] == 4 and {c.get("name") or c["label"] for c in x["nearest"]} == {"王五", "我", "S1"}
    me = vt.compare(f"{CID}@10:00:00", label="我")
    assert me["with"]["in_it"] is True and me["score"] > 0.99
    text, _, _, _ = tool("voice_compare", sentence=f"{CID}@10:00:00", label="我")
    assert "这句就是它的来源之一" in text
    assert vt.compare(f"{CID}@10:00:20", label=f"{CID}@S1")["with"]["in_it"] is None


def test_references(store):
    assert tool("voiceprint", sentence="f2")[2]["voiceprint"]["sentence"]["conv"] == CID
    text, bad, _, _ = tool("voiceprint", sentence="f0")
    assert bad and "两台设备" in text
    for ref, says in (("nope", "没有这句"), ("c1@10:00:00", "没有这段对话"), (f"{CID}@10:00", "HH:MM:SS"), (f"{CID}@10:00:05", "没有 10:00:05")):
        text, bad, _, _ = tool("voice_nearest", sentence=ref)
        assert bad and says in text, (ref, text)
    assert "正好填一个" in tool("voice_compare", sentence="f2")[0]
    assert "没有「赵六」" in tool("voice_compare", sentence="f2", person="赵六")[0]
    with db.tx() as c:  # past 40 s: no audio
        db.insert_sentences(c, [dict(id="f9", start=iso(50), end=iso(52), speaker="S1", text="x", source="mic")])
    assert "录音已经不在了" in tool("voiceprint", sentence="f9")[0]


def test_http(store, monkeypatch):
    monkeypatch.setattr(core, "TENANTS", [store["t"]])
    c = TestClient(server.app, raise_server_exceptions=False)
    v = c.get("/api/voiceprint", params={"sentence": f"{CID}@10:00:03", "from_s": 0, "to_s": 4}).json()
    assert v["from"] == "audio" and len(v["e"]) == 192 and v["model"] == vt.model()
    s = c.get("/api/voiceprint/compare", params={"sentence": "f2", "person": "王五"}).json()
    assert s["with"]["left_out"] is True and 0.9 < s["score"] < 1
    n = c.get("/api/voiceprint/nearest", params={"sentence": "f2", "n": 2}).json()
    assert len(n["nearest"]) == 2 and n["nearest"][0]["name"] == "王五"
    r = c.get("/api/voiceprint", params={"sentence": "f2", "from_s": 0, "to_s": 0.2})
    assert r.status_code == 400 and "0.5–15" in r.json()["detail"]
    assert c.get("/api/voiceprint", params={"sentence": "nope"}).status_code == 404


def test_busy_is_refused_at_once_and_never_two_in_the_model(store, monkeypatch):
    inside, most, lock = [0], [0], threading.Lock()

    def slow(x):
        with lock:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
        time.sleep(0.3)
        with lock:
            inside[0] -= 1
        return embed(x)
    monkeypatch.setitem(core.state, "embed", slow)
    out = []
    call = lambda: out.append((time.monotonic(), tool("voiceprint", sentence=f"{CID}@10:00:10")))
    ts = [threading.Thread(target=contextvars.copy_context().run, args=(call,)) for _ in range(4)]  # each in the tenant
    t0 = time.monotonic()
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    busy = [at - t0 for at, (text, bad, _, _) in out if bad]
    assert most[0] == 1 and len(out) == 4 and busy and all("正在算别的声纹" in r[1][0] for r in out if r[1][1])
    assert max(busy) < 0.25, "refused at once, not queued behind the one running"


def test_infer_service_tried_once(store, monkeypatch):
    """Down: refused at once, the service's state untouched; up but hung: one try of TIMEOUT_S, no backoff set."""
    from test_compute import TOKEN, bound
    sock = bound()
    sock.listen()  # taken, never answered
    r = compute.Remote(f"http://127.0.0.1:{sock.getsockname()[1]}", TOKEN, 30.0)
    monkeypatch.setattr(compute, "REMOTE", r)
    monkeypatch.setattr(vt, "TIMEOUT_S", 0.5)
    r.ok = False  # unchecked since the start
    t0 = time.monotonic()
    text, bad, _, _ = tool("voiceprint", sentence="f2")
    assert bad and "推理服务现在不可用" in text and time.monotonic() - t0 < 0.3
    r.ok = True
    t0 = time.monotonic()
    text, bad, _, _ = tool("voiceprint", sentence="f2")
    assert bad and "推理服务现在不可用" in text and 0.4 < time.monotonic() - t0 < 2
    assert r.down_seconds() == 0 and r.next_try == 0, "an agent's timeout holds up no pipeline call"
    sock.close()
