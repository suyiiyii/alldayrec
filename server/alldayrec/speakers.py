"""Speakers across conversations, for the speakers page. A "voice" is one label of one conversation (its mean
voiceprint over a few sentences); labels that are names (and 我) group by name, the anonymous S<n> ones group by
voice. An anonymous group close to a named one is offered as a suggestion, never merged by itself.
"""
import re

import numpy as np

ANON = re.compile(r"S(\d+|\?)")


def key(v: dict) -> str:
    return f"{v['conv']}|{v['label']}"


def centered(voices: list[dict]) -> dict[str, np.ndarray]:
    """Each voiceprint minus its device's mean: without it one microphone's own sound pulls strangers together."""
    by_dev = {}
    for v in voices:
        by_dev.setdefault(v["device"], []).append(np.array(v["e"]))
    mean = {d: np.mean(es, 0) for d, es in by_dev.items()}
    out = {}
    for v in voices:
        x = np.array(v["e"]) - mean[v["device"]]
        out[key(v)] = x / (np.linalg.norm(x) or 1)
    return out


def cluster(voices: list[dict], vec: dict[str, np.ndarray], link: float, apart: set[tuple[str, str]]) -> list[list[dict]]:
    """Leader clustering, biggest voices first: a voice joins the closest group whose centroid is >= link, unless
    the group already has its conversation (two labels of one conversation are two people) or a pin keeps them
    apart. apart holds (voice key, group leader key)."""
    # ponytail: greedy, O(voices x groups); average-linkage when a few thousand voices get slow or too split
    groups = []  # [leader key, members, summed vector, conversations]
    for v in sorted(voices, key=lambda v: -v["n"]):
        x, k = vec[key(v)], key(v)
        best, best_s = None, link
        for g in groups:
            if v["conv"] in g[3] or (k, g[0]) in apart:
                continue
            s = float(x @ g[2]) / np.linalg.norm(g[2])
            if s >= best_s:
                best, best_s = g, s
        if best:
            best[1].append(v); best[2] += x; best[3].add(v["conv"])
        else:
            groups.append([k, [v], x.copy(), {v["conv"]}])
    return [g[1] for g in groups]


def close_pairs(keys: list[str], vec: dict[str, np.ndarray], at: float) -> list[tuple[float, str, str]]:
    """Pairs of the voices (keys) with a cosine of at least `at`, most alike first; keys without a voice are left out."""
    ks = [k for k in keys if k in vec]
    pairs = ((float(vec[a] @ vec[b]), a, b) for i, a in enumerate(ks) for b in ks[i + 1:])
    return sorted((p for p in pairs if p[0] >= at), reverse=True)


def speakers(voices: list[dict], link: float, suggest: float, apart: set[tuple[str, str]]) -> list[dict]:
    """Named groups (with anonymous groups that sound like them as suggestions), then anonymous groups. A suggestion
    leaves out the group's voices in conversations the name is in already (two labels of one conversation: the
    conversation page offers those as a merge); they stay an anonymous group, under the leader's key, which pins are
    against. Before 10-25 one shared conversation kept the whole group from the name (all 18 twins of a name)."""
    vec = centered(voices)
    named, anon = {}, []
    for v in voices:
        (anon.append(v) if ANON.fullmatch(v["label"]) else named.setdefault(v["label"], []).append(v))
    cent = {n: np.mean([vec[key(v)] for v in vs], 0) for n, vs in named.items()}
    cent = {n: c / (np.linalg.norm(c) or 1) for n, c in cent.items()}
    convs = {n: {v["conv"] for v in vs} for n, vs in named.items()}
    out = {n: {"id": n, "name": n, "members": vs, "suggested": []} for n, vs in named.items()}
    rest = []
    for g in cluster(anon, vec, link, apart):
        best = (suggest, None, g)
        for n, cn in cent.items():
            free = [v for v in g if v["conv"] not in convs[n]]
            if not free or any((key(v), n) in apart for v in g):
                continue
            c = np.mean([vec[key(v)] for v in free], 0)
            if (s := float(c @ cn) / (np.linalg.norm(c) or 1)) >= best[0]:
                best = (s, n, free)
        s, n, free = best
        if n:
            out[n]["suggested"].append({"score": round(s, 3), "members": free})
        if left := [v for v in g if v["conv"] in convs[n]] if n else g:
            rest.append({"id": key(g[0]), "name": None, "members": left, "suggested": []})
    return list(out.values()) + rest


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=8), rng.normal(size=8)
    mk = lambda conv, label, base, dev="mic": {"conv": conv, "label": label, "device": dev, "n": 5,
                                               "e": (base + rng.normal(scale=0.1, size=8)).tolist()}
    vs = [mk("c1", "我", a), mk("c2", "我", a), mk("c1", "S1", b), mk("c2", "S1", b), mk("c3", "S2", b), mk("c3", "S1", -b)]
    sp = speakers(vs, link=0.6, suggest=0.99, apart=set())
    anon = [g for g in sp if g["name"] is None]
    assert {len(g["members"]) for g in anon} == {3, 1}, anon  # the three b voices together, -b alone
    assert [g["name"] for g in sp if g["name"]] == ["我"]
    two = cluster([vs[2], vs[3]], centered(vs), 0.6, {("c2|S1", "c1|S1")})
    assert len(two) == 2  # a pin keeps them apart
    pairs = close_pairs(["c3|S2", "c3|S1", "c3|S9"], centered(vs), 0.6)
    assert pairs == [] and close_pairs(["c1|S1", "c2|S1", "c1|我"], centered(vs), 0.6)[0][1:] == ("c1|S1", "c2|S1")
    print("ok")
