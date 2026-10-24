"""The people bank (the voiceprint of each name the diarists name clusters by) kept as a ledger of what taught it, and
the two-half test of a voice card (#295).

The ledger, table enrolled: one row per sentence learnt (row: its rowid in sentences, n 1, e its voiceprint), and per
name at most one row NULL, n its sentences: the bank as it stood before the ledger (10-25). A name's voiceprint is
the n-weighted mean of its rows. A rename away from the name (apply_rename: 不是, 拆开, the conversation page) and an
undo drop the rows of the sentences they move, and the voiceprint is that mean again.
"""
import numpy as np

from alldayrec import db
from alldayrec.tenant import T

OWN_MIN = 0.6   # a sentence learnt is this close to its label's mean: one label can hold someone else's few sentences
BANK_MIN = 0.7  # ... and to the name's voiceprint, once it has one: N03's had drifted to 0.789 against its own voices
CAP = 40        # sentences one click teaches, over its conversations: naming a card of 10 taught ~200 at once


def unit(v) -> np.ndarray:
    v = np.asarray(v, np.float64)
    return v / (np.linalg.norm(v) or 1)


def pick(labels: list[list[np.ndarray]], bank: np.ndarray | None) -> list[tuple[int, int]]:
    """Which sentences of one click teach the name, as (label, sentence) indices: labels holds each renamed label's
    sentence voiceprints (one conversation's); a sentence OWN_MIN close to its label's mean and, when the name has a
    voiceprint (bank), BANK_MIN close to it; at most CAP, taken in turns from the labels, each label's closest first."""
    b = None if bank is None else unit(bank)
    ok = []
    for i, es in enumerate(labels):
        m = unit(np.mean(es, 0))
        good = sorted(((float(e @ m), j) for j, e in enumerate(es)
                       if float(e @ m) >= OWN_MIN and (b is None or float(e @ b) >= BANK_MIN)), key=lambda x: -x[0])
        ok += [(turn, -s, i, j) for turn, (s, j) in enumerate(good)]
    return [(i, j) for _, _, i, j in sorted(ok)[:CAP]]


def recompute(t, name: str) -> None:
    """The name's people row as the mean of its ledger rows; none left: no row (the diarists stop naming it)."""
    rows = t.execute("SELECT n, e FROM enrolled WHERE name = ?", (name,)).fetchall()
    if rows:
        n = sum(r[0] for r in rows)
        t.execute("INSERT OR REPLACE INTO people VALUES (?, ?, ?)", (name, n, db.blob(sum(r[0] * db.vec(r[1]) for r in rows) / n)))
    else:
        t.execute("DELETE FROM people WHERE name = ?", (name,))


def learn(t, name: str, conv: str | None, sentences: list[tuple[dict, np.ndarray]]) -> int:
    """Sentences (a row of sentences, its voiceprint) into the name's ledger, each only while it still says `name` (an
    undo of the rename can come before the learning: it took 40 s for a card of 10 conversations). How many."""
    k = 0
    for r, e in sentences:
        row = t.execute("SELECT rowid FROM sentences WHERE start = ? AND source IS ? AND speaker = ?",
                        (r["start"], r.get("source"), name)).fetchone()
        if row:
            t.execute("INSERT INTO enrolled VALUES (?, ?, ?, 1, ?)", (name, conv, row[0], db.blob(e)))
            k += 1
    if k:
        recompute(t, name)
    return k


def forget(t, name: str, rows: list[int]) -> list[tuple]:
    """The ledger rows of these sentences (rowids) taken out of the name's; what they were, for an undo (restore)."""
    q = f"FROM enrolled WHERE name = ? AND row IN ({','.join('?' * len(rows))})"
    gone = [tuple(r) for r in t.execute("SELECT * " + q, (name, *rows))]
    if gone:
        t.execute("DELETE " + q, (name, *rows))
        recompute(t, name)
    return gone


def restore(t, gone: list[tuple]) -> None:
    t.executemany("INSERT INTO enrolled VALUES (?, ?, ?, ?, ?)", gone)
    for name in {g[0] for g in gone}:
        recompute(t, name)


def sync(*names: str) -> None:
    """The names' voiceprints as the store has them now, into every diarist of the tenant (after the transaction)."""
    people = db.people()
    for dia in T().dias:
        for name in names:
            if name in people:
                dia.people[name] = unit(people[name][1])
            else:
                dia.people.pop(name, None)


MIXED_MIN = 20     # voices a card needs before naming it checks for two people in it
MIXED_BELOW = 0.5  # two halves this little alike (cosine of their centroids) are two people: #295, the one mixed named
                   # card 0.29 and 0.34 on its two recorders; 69 card x recorder splits median 0.72, p10 0.52
CLASS_MIN = 10     # voices of one recorder (device) a split needs: the halves are compared within one recorder
HALF_MIN = 3       # a smaller half is an odd voice or two (不是 on the card), not a second person


def halves(members: list[dict], vec: dict[str, np.ndarray]) -> list[list[dict]] | None:
    """The two-half test of a card (#295 §4): its voices of each recorder split in two (k-means, device-centred
    voiceprints, vec by conv|label); the recorder whose halves are least alike decides. Two people: the card's voices
    in two, each with the nearer of that split's centroids, the bigger half first. None: one person, or too few voices."""
    from sklearn.cluster import KMeans
    if len(members) < MIXED_MIN:
        return None
    key = lambda m: f"{m['conv']}|{m['label']}"
    by = {}
    for m in members:
        if key(m) in vec:
            by.setdefault(m.get("device"), []).append(vec[key(m)])
    worst = None
    for xs in by.values():
        if len(xs) < CLASS_MIN:
            continue
        X = np.array(xs)
        lab = KMeans(2, n_init=10, random_state=0).fit_predict(X)
        if min(lab.sum(), len(lab) - lab.sum()) < HALF_MIN:
            continue
        c = [unit(X[lab == k].mean(0)) for k in (0, 1)]
        if (s := float(c[0] @ c[1])) < MIXED_BELOW and (not worst or s < worst[0]):
            worst = (s, c)
    if not worst:
        return None
    out = [[], []]
    for m in members:
        if key(m) in vec:
            out[int(vec[key(m)] @ worst[1][1] > vec[key(m)] @ worst[1][0])].append(m)
    return sorted(out, key=lambda h: -sum(m.get("speech_s") or 0 for m in h))
