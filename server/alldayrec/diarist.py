"""Conversation-level speaker labels. Every new sentence re-clusters the open conversation's voiceprints
(ReDimNet2, average-link cosine) and takes its cluster's label: 我 when that cluster's mean voiceprint is
close to the owner bank (>= OWNER_MIN; a split owner stays 我 in every piece), else a person's name when that
close to a voiceprint named by hand on the conversation page (>= PEOPLE_MIN; closer than the bank wins when both pass), else the S<n> most of the cluster's earlier sentences
carry, so ids follow membership as clusters grow and merge. Only the new sentence is labelled; earlier
rows are never rewritten. Case replays 09-29: see artifacts/record/voiceprint.

    uv run python -m alldayrec.diarist   # self-check on synthetic voiceprints
"""
import os

import numpy as np

TH = float(os.environ.get("DIAR_TH", "0.5"))  # cosine distance; lower splits one person into several ids (mergeable later), higher merges people (not undoable): 0.6 merged two colleagues in the 05-26 meeting
OWNER_MIN = 0.7   # ReDimNet2 cluster vs owner bank: my clusters 0.79-0.99, others <= 0.63 (incl. unseen channels)
MIN_DUR = 1.0     # shorter sentences are not clustered: they join the nearest labelled centroid
JOIN = float(os.environ.get("DIAR_JOIN", "0.35"))  # ... when at least this close, else S?; also for a lone long sentence
MIN_ME = 3        # a cluster needs this many sentences before it can be 我 or a name
PEOPLE_MIN = float(os.environ.get("PEOPLE_MIN", "0.7"))  # same evidence as OWNER_MIN: other people score <= 0.63 against one person's voiceprint
KEEP = 1500       # clustering is O(n^2): a four-hour conversation keeps its latest sentences
SR = 16000


def load_embedder():
    """ReDimNet2 B6, VoxBlink2 + VoxCeleb2 + CN-Celeb (Interspeech 2026), on onnxruntime (infer.py); int16 audio -> unit vector."""
    from alldayrec import infer
    return infer.embedder()


def _unit(v):
    return v / np.linalg.norm(v)


def _closest(m: np.ndarray, people: dict[str, np.ndarray]) -> tuple[float, str | None]:
    return max(((float(m @ v), k) for k, v in people.items()), default=(0.0, None))


def _me(owner: float, near: tuple[float, str | None]) -> bool:
    """A cluster scoring `owner` against the bank and `near` (score, name) against the closest named voice is 我 only
    when no named voice passes PEOPLE_MIN closer: a person who sounds like the owner on one recorder can pass both
    (10-06, a friend's instance: the owner's cluster 0.69-0.73 against the friend's bank, 0.94 against the owner's own voiceprint)."""
    return owner >= OWNER_MIN and not (near[0] >= PEOPLE_MIN and near[0] > owner)


class Diarist:
    def __init__(self, bank: dict[str, np.ndarray], th: float = TH, people: dict[str, np.ndarray] | None = None):
        self.th = th
        self.set_bank(bank)
        self.people = {k: _unit(v) for k, v in (people or {}).items()}  # the server adds names as they are given
        self.reset()

    def set_bank(self, bank: dict[str, np.ndarray]):
        """The owner bank: 我 confirmed on the card (selfvoice) comes in between conversations, without a restart."""
        self.bank = [_unit(v) for v in bank.values()]

    def reset(self):
        self.E, self.dur, self.ids, self.nid = [], [], [], 0

    def mark(self) -> tuple[int, int]:
        return len(self.E), self.nid

    def rollback(self, mark: tuple[int, int]):
        """Forget sentences added after mark (labelled in a pass but left open for the next one)."""
        n, self.nid = mark
        del self.E[n:], self.dur[n:], self.ids[n:]

    def add(self, e: np.ndarray, dur: float) -> tuple[str, float]:
        """Label one sentence (its unit voiceprint and seconds); returns (label, owner score of its cluster)."""
        from scipy.optimize import linear_sum_assignment
        from sklearn.cluster import AgglomerativeClustering
        self.E.append(e); self.dur.append(dur); self.ids.append(None)
        i = len(self.E) - 1
        lo = max(0, i + 1 - KEEP)
        big = [j for j in range(lo, i + 1) if self.dur[j] >= MIN_DUR]
        E = np.array(self.E)
        if i not in big:
            return self._nearest(i, big)
        lab = AgglomerativeClustering(n_clusters=None, distance_threshold=self.th, metric="cosine",
                                      linkage="average").fit_predict(E[big]) if len(big) > 1 else np.zeros(1, int)
        mean = {c: _unit(E[big][lab == c].mean(0)) for c in set(lab)}
        owner = {c: max((float(m @ o) for o in self.bank), default=0.0) for c, m in mean.items()}  # no bank: never 我
        near = {c: _closest(m, self.people) for c, m in mean.items()}
        me = {c for c in owner if (lab == c).sum() >= MIN_ME and _me(owner[c], near[c])}
        c = lab[big.index(i)]
        if c in me:
            self.ids[i] = "我"
            return "我", round(owner[c], 3)
        if (lab == c).sum() >= MIN_ME and near[c][0] >= PEOPLE_MIN:
            self.ids[i] = near[c][1]
            return near[c][1], round(near[c][0], 3)
        clusters = sorted(set(lab) - me)
        known = sorted({v for v in self.ids if v not in (None, "S?", "我")})
        cmap = {}
        if known:
            M = np.array([[sum(1 for j, l in zip(big, lab) if l == cl and self.ids[j] == k) for k in known] for cl in clusters])
            r, cc = linear_sum_assignment(-M)
            cmap = {clusters[a]: known[b] for a, b in zip(r, cc) if M[a, b] > 0}
        members = [j for j, l in zip(big, lab) if l == c]
        if c in cmap:
            self.ids[i] = cmap[c]
        elif len(members) >= 2:  # a new person once two sentences agree; the earlier one adopts the id for later rounds
            self.nid += 1
            self.ids[i] = f"S{self.nid}"
            for j in members:
                if self.ids[j] in (None, "S?"):
                    self.ids[j] = self.ids[i]
        else:  # alone in its cluster: the nearest speaker if close enough
            return self._nearest(i, [j for j in big if j != i])
        return self.ids[i], round(owner[c], 3)

    def _nearest(self, i: int, pool: list[int]) -> tuple[str, float]:
        cents = {}
        for j in pool:
            if self.ids[j] not in (None, "S?"):
                cents.setdefault(self.ids[j], []).append(self.E[j])
        best = max(((k, float(self.E[i] @ _unit(np.mean(v, 0)))) for k, v in cents.items()), key=lambda t: t[1], default=("S?", 0.0))
        self.ids[i] = best[0] if best[1] >= JOIN else "S?"
        return self.ids[i], round(best[1], 3)


GUARD = float(os.environ.get("DIAR_GUARD", "0.6"))  # an engine group whose long sentence is this far from the group's mean voiceprint is split


def relabel(E: list, dur: list[float], bank: dict[str, np.ndarray], groups: list | None = None,
            people: dict[str, np.ndarray] | None = None, th: float = TH, guard: float | None = GUARD) -> list[str]:
    """Labels for a whole conversation at once, after it closed: the online labels of a sentence were fixed with only
    the sentences before it. E: unit voiceprints (None: too short to embed); dur: seconds; groups: per sentence a key
    the engine gives (its speaker id within one piece): sentences sharing one are clustered as one unit, their mean
    voiceprint, so a turn's short sentences follow its long ones. Same rules as add(): 我 when a cluster of MIN_ME
    sentences is OWNER_MIN close to the bank, a name PEOPLE_MIN close to a named voice (the closer wins), else S<n> in order of first
    appearance; a sentence in no cluster joins the nearest labelled centroid at JOIN, else S?."""
    from sklearn.cluster import AgglomerativeClustering
    bank_v = [_unit(v) for v in bank.values()]
    people = {k: _unit(v) for k, v in (people or {}).items()}
    keys = groups if groups is not None else list(range(len(E)))
    units: dict = {}
    for i, k in enumerate(keys):
        if E[i] is not None:
            units.setdefault(k, []).append(i)
    long = lambda m: [i for i in m if dur[i] >= MIN_DUR]
    units = {k: m for k, m in units.items() if long(m)}  # a unit needs one sentence long enough to trust its voiceprint
    if groups is not None and guard is not None:  # the engine may give two people one id in a piece: such a group falls apart
        for k in list(units):
            m = _unit(np.mean([E[i] for i in long(units[k])], 0))
            if any(float(E[i] @ m) < guard for i in long(units[k])):
                for i in units.pop(k):
                    units[("alone", i)] = [i]
        units = {k: m for k, m in units.items() if long(m)}
    ks = list(units)
    out: list[str | None] = [None] * len(E)
    if ks:
        V = np.array([_unit(np.mean([E[i] for i in long(units[k])], 0)) for k in ks])
        lab = AgglomerativeClustering(n_clusters=None, distance_threshold=th, metric="cosine",
                                      linkage="average").fit_predict(V) if len(ks) > 1 else np.zeros(1, int)
        names, n_s = {}, 0
        for c in sorted(set(lab), key=lambda c: min(min(units[k]) for k, l in zip(ks, lab) if l == c)):
            members = [i for k, l in zip(ks, lab) if l == c for i in units[k]]
            m = _unit(np.mean([E[i] for i in members if dur[i] >= MIN_DUR], 0))
            near = _closest(m, people)
            if len(members) >= MIN_ME and _me(max((float(m @ o) for o in bank_v), default=0), near):
                names[c] = "我"
            elif len(members) >= MIN_ME and near[0] >= PEOPLE_MIN:
                names[c] = near[1]
            else:
                n_s += 1
                names[c] = f"S{n_s}"
        for k, l in zip(ks, lab):
            for i in units[k]:
                out[i] = names[l]
    cents: dict[str, list] = {}
    for i, l in enumerate(out):
        if l and dur[i] >= MIN_DUR:
            cents.setdefault(l, []).append(E[i])
    cents = {k: _unit(np.mean(v, 0)) for k, v in cents.items()}
    for i, k in enumerate(keys):
        if out[i] is None:
            mates = [out[j] for j, kk in enumerate(keys) if kk == k and out[j]] if groups is not None else []
            if mates:
                out[i] = max(set(mates), key=mates.count)  # its turn mates' label
            elif E[i] is not None and cents:
                score, best = max((float(E[i] @ v), k2) for k2, v in cents.items())
                out[i] = best if score >= JOIN else "S?"
            else:
                out[i] = "S?"
    return out


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    me, a, b = (_unit(rng.normal(size=192)) for _ in range(3))
    noisy = lambda v: _unit(v + rng.normal(size=192) * 0.035)  # cos to own centre ~0.8, like real sentences
    d = Diarist({"dji-mac": noisy(me)})
    seq = [me, a, me, a, b, a, me, b, b, me, a] * 3
    out = [d.add(noisy(v), 2.0)[0] for v in seq]
    tail = out[11:]
    assert all(l == "我" for l, v in zip(tail, seq[11:]) if v is me), out
    assert len({l for l, v in zip(tail, seq[11:]) if v is a}) == 1 and len({l for l, v in zip(tail, seq[11:]) if v is b}) == 1, out
    m = d.mark(); d.add(noisy(a), 2.0); d.rollback(m)
    assert d.mark() == m
    assert d.add(noisy(a), 0.4)[0] == tail[1], "a short sentence joins its speaker"
    d = Diarist({"dji-mac": noisy(me)}, people={"李四": noisy(a)})
    named = [d.add(noisy(v), 2.0)[0] for v in seq]
    assert set(l for l, v in zip(named[11:], seq[11:]) if v is a) == {"李四"}, named
    assert "李四" not in {l for l, v in zip(named, seq) if v is b}, named
    E = [noisy(v) for v in seq]  # the same conversation, labelled once at its close
    lab = relabel(E, [2.0] * len(E), {"dji-mac": noisy(me)})
    assert all(l == "我" for l, v in zip(lab, seq) if v is me), lab
    assert len({l for l, v in zip(lab, seq) if v is a}) == 1 and len({l for l, v in zip(lab, seq) if v is b}) == 1, lab
    assert {l for l, v in zip(lab, seq) if v is a}.isdisjoint({l for l, v in zip(lab, seq) if v is b}), lab
    mixed = relabel(E, [2.0] * len(E), {"dji-mac": noisy(me)}, groups=["one"] * len(E))  # an engine id over everyone
    assert mixed == lab, "a group whose voices disagree falls apart instead of making one speaker"
    # someone who sounds like the owner on this recorder: their cluster passes the bank too; named, the closer voice wins
    f = _unit(0.76 * me + np.sqrt(1 - 0.76 ** 2) * b)
    seq2 = [me, f, f, me] * 8
    E2 = [_unit(v + rng.normal(size=192) * 0.06) for v in seq2]  # cos ~0.77 to their centre, as real rows on one recorder
    both = relabel(E2, [2.0] * len(E2), {"mic": me})
    assert set(both) == {"我"}, both  # the old rule: both clusters 我
    lab2 = relabel(E2, [2.0] * len(E2), {"mic": me}, people={"王五": f})
    assert [l for l, v in zip(lab2, seq2) if v is f] == ["王五"] * 16 and [l for l, v in zip(lab2, seq2) if v is me] == ["我"] * 16, lab2
    d = Diarist({"mic": me}, people={"王五": f})
    on = [d.add(e, 2.0)[0] for e in E2]
    assert set(l for l, v in zip(on[8:], seq2[8:]) if v is f) == {"王五"} and set(l for l, v in zip(on[8:], seq2[8:]) if v is me) == {"我"}, on
    print("ok", out, named)
