"""Who is 我 on an instance without a voiceprint: the card "哪个是你？". The voice that recurs across the
most conversations is most likely the wearer's. The voices table (one ReDimNet2 mean per conversation x label) is
peeled into candidates: the most recurrent voice with at most one member per conversation (wearer), then its members
and look-alikes leave and the next is taken, KMAX at most. Each is scored by the share of conversations it is in, its
share of the speech and how much louder it is than the others of the same conversation, as a conditional logit with
a "none of them" option (fitted on the owner's live era). The card pops at a
conversation's close once the first candidate is clear (due), else 24 h after the first voice. The voiceprint is taken
only when the user picks a candidate on the card, never by itself, however clear.

    uv run pytest test_selfvoice.py
"""
import datetime as dt
import os

import numpy as np

TAU = 0.65  # one person's voices across conversations (LR boards alike over 0.5-0.7; old mics merged people below 0.65)
KMAX = 5    # candidates peeled
SHOW = 3    # on the card: the user was among the first 3 at every trigger point of the replays
W_COV, W_SHARE, W_DB, W_NONE = 9.7, 3.5, 0.78, 2.9  # logit per share of conversations, of speech, per dB louder; "none of them"
# the first candidate's probability before the card pops by itself: picked on 14 + 18 replays whose one counter-example
# was at 93 %; to be reviewed with the users' answers (kv selfvoice.log)
POP_P = float(os.environ.get("SELFVOICE_POP_P", "0.95"))
POP_CONVS, POP_S = 3, 180  # ... with this many conversations, the first candidate in as many and speaking this long
FALLBACK_S = 24 * 3600     # never that clear: the card pops this long after the first voice anyway
LATER_S = 24 * 3600        # 以后再说: asked again a day later; 都不是: once there are twice the conversations
TIERS = ((1.0, 2.5), (0.5, 2.0), (0.0, 1.5))  # a clip: nobody else within (s), at least (s) long; relaxed while short of 3
CLIP_S, CLIP_CHARS, CLIP_DB = 10.0, 8, 3.0    # at most this long, this many characters, this far under its voice's level


def unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    return v / (np.linalg.norm(v) or 1)


def level(x: np.ndarray) -> float | None:
    """A sentence's level in dBFS: the median of its louder half of 25 ms frames (within 25 dB of the loudest)."""
    n = 1 + (len(x) - 400) // 160
    if n < 5:
        return None
    f = x[np.arange(400)[None] + 160 * np.arange(n)[:, None]].astype(np.float32) / 32768
    e = 10 * np.log10(np.mean(f ** 2, 1) + 1e-12)
    return float(np.median(e[(e >= np.percentile(e, 50)) & (e >= e.max() - 25)]))


def loudness(vs: list[dict]) -> list[float | None]:
    """Each voice's level minus the median of the other voices' in its conversation (None: no level, or alone)."""
    by: dict[str, list[float]] = {}
    for v in vs:
        if v.get("level") is not None:
            by.setdefault(v["conv"], []).append(v["level"])
    out = []
    for v in vs:
        ls = list(by.get(v["conv"], []))
        if v.get("level") is None or len(ls) < 2:
            out.append(None)
            continue
        ls.remove(v["level"])
        out.append(v["level"] - float(np.median(ls)))
    return out


def wearer(E: np.ndarray, ci: np.ndarray, sec: np.ndarray, tau: float = TAU, iters: int = 5) -> tuple[list[int], np.ndarray]:
    """Members (one voice per conversation at most) of the voice that recurs in the most other conversations, and their
    unit mean. E: unit voiceprints, ci: their conversation, sec: their speech (breaks a tie)."""
    convs = sorted(set(ci.tolist()))
    S = E @ E.T
    rec = sum(((S[:, ci == c].max(1) >= tau) & (ci != c)).astype(int) for c in convs)
    m = E[max(range(len(E)), key=lambda i: (rec[i], sec[i]))]
    members: list[int] = []
    for _ in range(iters):
        sc = E @ m
        members = []
        for c in convs:
            idx = np.flatnonzero(ci == c)
            j = int(idx[sc[idx].argmax()])
            if sc[j] >= tau:
                members.append(j)
        if not members:
            break
        m = unit(E[members].mean(0))
    return members, m


def candidates(vs: list[dict]) -> tuple[list[dict], int]:
    """The candidates of all voices, most likely first, and the number of conversations. vs: voices (conv, label, e,
    speech_s, level); a candidate: members [{conv, label, speech_s}], bank (unit mean), convs, speech_s, p."""
    if not vs:
        return [], 0
    E = np.array([unit(v["e"]) for v in vs])
    names = sorted({v["conv"] for v in vs})
    ci = np.array([names.index(v["conv"]) for v in vs])
    sec = np.array([v.get("speech_s") or 0.0 for v in vs])
    rel, total = loudness(vs), float(sec.sum()) or 1.0
    out, left = [], np.arange(len(vs))
    while len(left) and len(out) < KMAX:
        mem, bank = wearer(E[left], ci[left], sec[left])
        if not mem:
            break
        g = left[mem]
        r = [rel[i] for i in g if rel[i] is not None]
        c = {"members": [{"conv": vs[i]["conv"], "label": vs[i]["label"], "speech_s": float(sec[i])} for i in g], "bank": bank,
             "convs": len(g), "speech_s": float(sec[g].sum()), "loud": float(np.median(r)) if r else 0.0}
        c["z"] = W_COV * c["convs"] / len(names) + W_SHARE * c["speech_s"] / total + W_DB * c["loud"]
        out.append(c)
        taken = set(g.tolist())
        left = np.array([i for i in left if i not in taken and float(E[i] @ bank) < TAU], int)
    z = np.array([W_NONE] + [c["z"] for c in out])
    p = np.exp(z - z.max())
    for c, x in zip(out, p[1:] / p.sum()):
        c["p"] = float(x)
    return sorted(out, key=lambda c: -c["p"]), len(names)


def due(st: dict, cands: list[dict], n_conv: int, now: dt.datetime) -> str | None:
    """Why the card pops now, or None. st: the kv selfvoice (first_at; wait after an answer; card while one is up;
    confirmed). The first time: when the first candidate is clear, else FALLBACK_S after the first voice; after
    以后再说 a day later, after 都不是 once the conversations doubled."""
    if not cands or st.get("card") or st.get("confirmed"):
        return None
    if w := st.get("wait"):
        ok = now >= dt.datetime.fromisoformat(w["until"]) if "until" in w else n_conv >= 2 * w["convs"]
        return "again" if ok else None
    top = cands[0]
    if n_conv >= POP_CONVS and top["convs"] >= POP_CONVS and top["speech_s"] >= POP_S and top["p"] >= POP_P:
        return "rule"
    if now - dt.datetime.fromisoformat(st["first_at"]) >= dt.timedelta(seconds=FALLBACK_S):
        return "fallback"
    return None


def tier(p: float) -> str:
    """What the card says instead of a %: out of fold, 80 %+ was the user 99 % of the time, 40-80 % 57-75 %, under 40 % 1-25 %."""
    return "很可能" if p >= 0.8 else "可能" if p >= 0.4 else "不确定"


def _s(iso: str) -> float:
    return dt.datetime.fromisoformat(iso).timestamp()


def shortlist(rows: list[dict], label: str, per: int = 3) -> list[dict]:
    """The sentences of `label` in a conversation's rows worth decoding for a clip: short enough, enough text, in the
    loosest tier; the strictest tier, then the longest, first. Each gets gap (s to another speaker's row) and dur."""
    a0 = np.array([_s(r["start"]) for r in rows])
    a1 = np.array([_s(r["end"]) for r in rows])
    who = np.array([r.get("speaker") or "" for r in rows])
    out = []
    for i, r in enumerate(rows):
        d = a1[i] - a0[i]
        if who[i] != label or len(r.get("text") or "") < CLIP_CHARS or d > CLIP_S or r.get("music"):
            continue
        o = who != label
        gap = float(np.maximum(a0[o] - a1[i], a0[i] - a1[o]).min()) if o.any() else 99.0  # < 0: they overlap
        k = next((k for k, (g, lo) in enumerate(TIERS) if gap >= g and d >= lo), None)
        if k is not None:
            out.append({**r, "gap": gap, "dur": float(d), "k": k})
    return sorted(out, key=lambda r: (r["k"], -r["dur"]))[:per]


def pick(pool: list[dict], own: np.ndarray, others: list[np.ndarray], n: int = 3) -> list[dict]:
    """A candidate's clips from its decoded sentences (gap, dur, text, lvl, lmed: its voice's level, e): of the strictest
    tier giving n, those sounding most like it and least like the other candidates, one per conversation first."""
    score = lambda r: float(r["e"] @ own) - max((float(r["e"] @ o) for o in others), default=0.0)
    for k, (g, lo) in enumerate(TIERS):
        xs = sorted((r for r in pool if r.get("e") is not None and r.get("lvl") is not None and lo <= r["dur"] <= CLIP_S
                     and len(r.get("text") or "") >= CLIP_CHARS and r["gap"] >= g
                     and (r.get("lmed") is None or r["lvl"] >= r["lmed"] - CLIP_DB)), key=score, reverse=True)
        out, convs = [], set()
        for r in xs:
            if r["conv"] not in convs:
                out.append(r)
                convs.add(r["conv"])
        out += [r for r in xs if all(r is not x for x in out)][:max(0, n - len(out))]
        if len(out) >= n or k == len(TIERS) - 1:
            return out[:n]
    return []
