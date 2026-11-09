"""speaker_eval (the nightly guard): which moves fail it, and that the day it makes and the speakers it picks stay the same
from run to run (the pack and BASELINE depend on both). The run itself needs the pack and the models: speaker-eval.yml."""
from alldayrec import speaker_eval as se


def test_worse_only_past_slack_and_the_wrong_way():
    base = dict(se.BASELINE)
    assert se.worse(base, base) == []
    better = {**base, "known_right": base["known_right"] + 10, "known_wrong": 0.0, "purity": 100.0}
    assert se.worse(better, base) == []
    within = {**base, "known_right": base["known_right"] - se.SLACK["known_right"], "known_wrong": base["known_wrong"] + se.SLACK["known_wrong"]}
    assert se.worse(within, base) == []
    bad = {**base, "me_right": base["me_right"] - se.SLACK["me_right"] - 0.1, "stranger_named": base["stranger_named"] + se.SLACK["stranger_named"] + 0.1}
    assert se.worse(bad, base) == [f"me_right {base['me_right']} -> {bad['me_right']}", f"stranger_named {base['stranger_named']} -> {bad['stranger_named']}"]


def test_day_and_pick_fixed(tmp_path):
    man = {"speakers": [{"id": f"S{i:04d}", "utts": [{"file": f"S{i:04d}/{j}.ogg", "s": 2.0 + j % 5} for j in range(se.UTTS)]}
                        for i in range(se.SPEAKERS)]}
    d = se.day(man)
    assert d == se.day(man) and len(d) == se.CONVS
    assert all(any(k == 0 for k, _, _ in c) for c in d), "我 in every conversation"
    me_enrol = {u["file"] for u in man["speakers"][0]["utts"][-se.ME_UTTS:]}
    assert not any(f in me_enrol for c in d for _, f, _ in c), "我's enrolment utterances never in a conversation"
    strangers = [k for c in d for k in {k for k, _, _ in c} if k > se.KNOWN]
    assert len(strangers) == len(set(strangers)), "a stranger is in one conversation"
    info = tmp_path / "speaker.info"
    info.write_text("".join(f"{i:04d} {'M' if i % 2 else 'F'}\n" for i in range(2, 402)))
    got = se.pick(info)
    assert got == se.pick(info) and len(got) == se.SPEAKERS
    assert sum(int(s) % 2 for s in got) == se.SPEAKERS // 2, "half of each gender"
