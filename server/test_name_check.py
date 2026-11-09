"""B3, naming a voice: a typed name is checked first against the people it may be already (name_check: the same name
in use, a voice near another name), and a rename says which earlier conversations have the voice still unnamed
(earlier), for 一起改 to name them by POST /api/speakers/name with the voices alone."""
import time

import anyio
import anyio.to_thread

from alldayrec import bank, db, routes_speakers, threads
from test_voice_check import E, P, conv, near, store  # noqa: F401  (store: the fixture)


def rename(cid, frm, to, **kw):
    return anyio.run(routes_speakers.rename, cid, {"from": frm, "to": to, **kw})


def test_check_same_name_and_like(store):
    conv(1, "P", near(P, 0.9, E[2]))  # P in another conversation
    near_p, far = conv(2, "S1", near(P, 0.75, E[3])), conv(3, "S1", E[4])
    assert "check" not in rename(far, "S1", "R", check=True) and db.conversation(far)["participants"] == ["R"], "nothing to ask: renamed"
    assert rename(near_p, "S1", "Q", check=True) == {"check": {"like": [{"name": "P", "score": 0.75}]}}
    assert routes_speakers.name_check([(conv(4, "S1", near(P, 0.65, E[5])), "S1")], "Q") is None, "0.65: under LIKE_MIN"
    clear = conv(5, "S1", near(P, 0.95, E[6]))
    assert rename(clear, "S1", "P", check=True)["check"]["same"]["alike"] is True, "the name in use, the voice clearly it: asked too"
    assert db.conversation(near_p)["participants"] == ["S1"], "asked: nothing renamed"
    assert rename(near_p, "S1", "P", check=True) == {"check": {"same": {"name": "P", "convs": 1, "alike": True, "free": "P2"}}}
    with db.tx() as t:
        db.insert_conversation(t, {"id": "c-p2", "start": "2026-01-01T00:00:00", "end": "2026-01-01T00:01:00",
                                   "participants": ["P2"], "sentences": 0, "speech_s": 0, "device": "mic"})
    got = routes_speakers.name_check([(far, "R")], "P")["same"]
    assert (got["alike"], got["free"]) == (False, "P3"), "a voice far from P, P2 in use"
    assert routes_speakers.name_check([(near_p, "S1")], "我") is None
    assert routes_speakers.name_check([(near_p, "S1")], "S5") is None
    assert routes_speakers.name_check([("c-none", "S1")], "Z") is None, "no voice, a new name: nothing to ask"
    assert routes_speakers.name_check([("c-none", "S1")], "P")["same"] == {"name": "P", "convs": 1, "alike": None, "free": "P3"}
    long = "长" * 20
    with db.tx() as t:
        db.insert_conversation(t, {"id": "c-long", "start": "2026-01-02T00:00:00", "end": "2026-01-02T00:01:00",
                                   "participants": [long], "sentences": 0, "speech_s": 0, "device": "mic"})
    assert len(routes_speakers.name_check([(near_p, "S1")], long)["same"]["free"]) == 20


def test_check_card(store):
    """The speakers page: a card's typed name checked over its voices; its own name is no 「很像」."""
    cid = conv(1, "S1", near(P, 0.75, E[3]))
    gid = f"{cid}|S1"
    got = anyio.run(routes_speakers.speaker_name, {"name": "Q", "group": gid, "check": True})
    assert got == {"check": {"like": [{"name": "P", "score": 0.75}]}} and db.conversation(cid)["participants"] == ["S1"]
    assert anyio.run(routes_speakers.speaker_name, {"name": "Q", "group": gid})["renamed"] == 1
    cp = conv(2, "P", near(P, 0.9, E[2]))
    assert "like" not in (routes_speakers.name_check([(cp, "P")], "P2") or {}), "P is its own name"


def test_earlier_then_together(store):
    """Renaming one conversation's S1 tells the earlier ones with its voice unnamed; 一起改 names those, one undo."""
    v = E[1] + E[2]
    c1, c2, c3, c4 = conv(1, "S1", v + 0.1 * E[3]), conv(2, "S1", v + 0.1 * E[4]), conv(3, "S2", E[6]), conv(4, "S1", v)
    c5 = conv(5, "S1", v + 0.1 * E[5])  # later: not 之前
    routes_speakers.T().speakers["groups"] = None
    assert anyio.run(routes_speakers.earlier, c4, "S1") is None, "the groups not made yet (the conversation page makes them)"
    routes_speakers.speaker_groups()
    r = rename(c4, "S1", "R")
    assert r["also"] == {"voices": [f"{c2}|S1", f"{c1}|S1"], "n": 2, "group": f"{c1}|S1"} and c3 not in str(r["also"]) and c5 not in str(r["also"])
    db.conn().execute("UPDATE sentences SET speaker = 'S7' WHERE speaker = 'S1' AND start < ?", (db.conversation(c2)["start"],))
    db.conn().execute("UPDATE conversations SET participants = '[\"S7\"]' WHERE id = ?", (c1,))  # renamed since the cards were made
    assert anyio.run(routes_speakers.earlier, c5, "S1") == {"voices": [f"{c2}|S1"], "n": 1, "group": f"{c1}|S1"}
    got = anyio.run(routes_speakers.speaker_name, {"name": "R", "voices": r["also"]["voices"], "group": r["also"]["group"], "undo": r["undo"]})
    assert got["renamed"] == 1 and db.conversation(c2)["participants"] == ["R"] and db.conversation(c1)["participants"] == ["S7"]
    anyio.run(routes_speakers.speaker_undo, {"undo": got["undo"]})
    assert [db.conversation(c)["participants"] for c in (c2, c4)] == [["S1"], ["S1"]], "one undo: 一起改 and the rename before it"
    assert rename(c5, "S1", "S9")["also"] is None, "an S<n> (a merge into another label): nothing to name together"


def test_earlier_keeps_the_loop_free(store, monkeypatch):
    """bank.halves (k-means: 0.5 s for 3000 voices, 2 s more for sklearn's first import) runs in a thread: the other
    requests go on meanwhile; a voice whose card has nothing earlier and unnamed runs none."""
    v = E[1] + E[2]
    c1, c2 = conv(1, "S1", v + 0.1 * E[3]), conv(2, "S1", v + 0.1 * E[4])
    named = conv(3, "P", E[6])
    routes_speakers.speaker_groups()
    monkeypatch.setattr(bank, "halves", lambda ms, vec: time.sleep(0.5))

    async def run():
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                await anyio.sleep(0.01)
                ticks += 1
        async with anyio.create_task_group() as tg:
            tg.start_soon(tick)
            got = await routes_speakers.earlier(c2, "S1")
            tg.cancel_scope.cancel()
        return got, ticks
    got, ticks = anyio.run(run)
    assert got == {"voices": [f"{c1}|S1"], "n": 1, "group": f"{c1}|S1"} and ticks >= 20, ticks
    monkeypatch.setattr(bank, "halves", lambda ms, vec: 1 / 0)
    assert anyio.run(routes_speakers.earlier, named, "P") is None, "nothing to offer: no k-means"


async def ticking(f, *a):
    """f(*a)'s answer, and how many 10 ms ticks the event loop ran meanwhile."""
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await anyio.sleep(0.01)
            ticks += 1
    async with anyio.create_task_group() as tg:
        tg.start_soon(tick)
        r = await f(*a)
        tg.cancel_scope.cancel()
    return r, ticks


def test_k_means_neither_waits_on_infer_nor_holds_the_loop(store, monkeypatch):
    """Both INFER slots held (a backlog's voiceprints, a remote infer retrying while down): earlier() and the halves
    test do not wait for one; the halves test (both ways in) runs in a thread, the other requests go on meanwhile."""
    v = E[1] + E[2]
    c1, c2, _ = conv(1, "S1", v + 0.1 * E[3]), conv(2, "S1", v + 0.1 * E[4]), conv(3, "P", E[6])
    routes_speakers.speaker_groups()
    monkeypatch.setattr(bank, "halves", lambda ms, vec: time.sleep(0.5))

    async def busy_infer():
        took = {}
        async with anyio.create_task_group() as tg:
            for _ in range(threads.INFER.total_tokens):
                tg.start_soon(lambda: anyio.to_thread.run_sync(time.sleep, 3.0, limiter=threads.INFER))
            await anyio.sleep(0.05)
            for name, f, a in (("earlier", routes_speakers.earlier, (c2, "S1")), ("halves", routes_speakers.speaker_halves, (f"{c1}|S1",))):
                t0 = time.monotonic()
                took[name] = (await ticking(f, *a))[1], round(time.monotonic() - t0, 2)
            tg.cancel_scope.cancel()
        return took
    took = anyio.run(busy_infer)
    assert all(t >= 20 and s < 1.5 for t, s in took.values()), took
    # in tests enroll_soon awaits enroll (INFER) itself, so with INFER free here
    r, ticks = anyio.run(ticking, routes_speakers.speaker_name, {"group": f"{c1}|S1", "name": "Q"})
    assert r["renamed"] == 2 and ticks >= 20, (r, ticks)


def test_check_where_the_name_is(store):
    """The name only in this conversation (a merge of two of its labels): nothing to ask; only in the people bank: asked."""
    cid = conv(1, "S1", E[4])
    db.conn().execute("UPDATE conversations SET participants = '[\"S1\", \"Q\"]' WHERE id = ?", (cid,))
    assert routes_speakers.name_check([(cid, "S1")], "Q") is None
    assert routes_speakers.name_check([(cid, "S1")], "P")["same"] == {"name": "P", "convs": 0, "alike": False, "free": "P2"}


def test_earlier_in_a_suggestion_and_not_named_ones(store):
    """The voice in an unnamed group offered to a name (a suggestion): its other voices; voices named already: never."""
    v = E[1] + E[2]
    conv(1, "P", v), conv(2, "P", v + 0.1 * E[3]), conv(5, "S2", E[6]), conv(6, "S3", E[5])
    c3, c4 = conv(3, "S1", v + 0.1 * E[4]), conv(4, "S1", v + 0.1 * E[7])
    gs = routes_speakers.speaker_groups()
    assert any(f"{c4}|S1" in {routes_speakers.voice_key(m) for m in s["members"]} for g in gs for s in g["suggested"]), "set up as a suggestion"
    assert anyio.run(routes_speakers.earlier, c4, "S1") == {"voices": [f"{c3}|S1"], "n": 1, "group": "P", "suggestion": f"{c3}|S1"}
    rename(c3, "S1", "P")
    routes_speakers.speaker_groups()
    assert anyio.run(routes_speakers.earlier, c3, "P") is None, "P's own earlier voices are named already"


def test_together_on_a_card_of_two(store, monkeypatch):
    """A card that sounds like two people (bank.halves): a rename offers only its voice's half to 一起改, and voices of
    both halves sent with the card are not named together (one half is): nobody's voiceprint learns the other person."""
    v = E[1] + E[2]
    cs = [conv(h, "S1", v + 0.1 * E[3 + h % 3]) for h in range(1, 6)]
    conv(7, "S2", E[6])
    a = set(cs[::2])  # one person in conversations 1, 3, 5; the other in 2 and 4
    monkeypatch.setattr(bank, "halves", lambda ms, vec: [[m for m in ms if m["conv"] in a], [m for m in ms if m["conv"] not in a]])
    g = next(g for g in routes_speakers.speaker_groups() if any(m["conv"] == cs[0] for m in g["members"]))
    keys = [routes_speakers.voice_key(m) for m in g["members"]]
    assert len(keys) == 5 and anyio.run(routes_speakers.speaker_name, {"name": "R", "group": g["id"], "voices": keys})["mixed"]
    r = rename(cs[4], "S1", "R")
    assert sorted(r["also"]["voices"]) == sorted(f"{c}|S1" for c in cs[:4:2]), "only conversation 5's half"
    got = anyio.run(routes_speakers.speaker_name, {"name": "R", "voices": r["also"]["voices"], "group": r["also"]["group"]})
    assert got["renamed"] == 2, "the card made again after the rename: the voices still named, no 409"
    assert [db.conversation(c)["participants"] for c in cs] == [["R"], ["S1"], ["R"], ["S1"], ["R"]]


def test_together_to_me_one_undo(store):
    """这是我, then 一起改: one undo takes back both and the centroid each click taught 我, not only the first's."""
    v = E[3] + E[4]
    c1, c2, c4 = conv(1, "S1", v + 0.1 * E[5]), conv(2, "S1", v + 0.1 * E[6]), conv(4, "S1", v)
    conv(3, "S2", E[2])
    routes_speakers.speaker_groups()
    r = rename(c4, "S1", "我")
    a = r["also"]
    got = anyio.run(routes_speakers.speaker_name, {"name": "我", "voices": a["voices"], "group": a["group"], "undo": r["undo"]})
    assert got["renamed"] == 2 and got["learned"] and db.kv("me_centroids")
    anyio.run(routes_speakers.speaker_undo, {"undo": got["undo"]})
    assert [db.conversation(c)["participants"] for c in (c1, c2, c4)] == [["S1"]] * 3
    assert db.kv("me_centroids") == {} and list(routes_speakers.owner_bank()) == ["self"]
