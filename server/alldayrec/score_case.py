"""Score a sentence hypothesis against a case's gt.jsonl.

    uv run python -m alldayrec.score_case --gt <case>/gt.jsonl --hyp hyp.jsonl [--label brief]

Both files: one sentence per line with start/end (wall-clock ISO), speaker, text. gt speakers are
我 / 同事; hypothesis speakers may be 我 / 他人 or anonymous labels (S1, SPEAKER_00, ...).
Reports, on GT sentences (all, and the owner-reviewed subset):
  spk_direct   我-vs-not accuracy taking the hypothesis label as is (我 == owner)
  spk_mapped   accuracy after mapping hypothesis labels one-to-one onto GT speakers (Hungarian on overlap)
  covered      share of GT sentences overlapped by any hypothesis sentence
and on hypothesis sentences: mixed (span holds two GT speakers >= 0.5 s each), count ratio, CER per
60 s window, and shown_at - end latency when present.
"""
import argparse
import datetime as dt
import json
import re
import statistics
from collections import Counter

P = dt.datetime.fromisoformat
PUNCT = re.compile(r"[\s，。？！、,.?!:：;；\"'“”‘’…—\-()（）]")


def load_live(data_dir, day):
    """Final rows plus the pending rows after the last finalized time, like /api/sentences."""
    import os
    fp, pp = os.path.join(data_dir, f"{day}.final.jsonl"), os.path.join(data_dir, f"{day}.jsonl")
    recs = [json.loads(l) for l in open(fp)] if os.path.exists(fp) else []
    rows = []
    for rec in recs:
        rows = [r for r in rows if not rec["from"] <= r["start"] < rec["to"]] + rec["rows"]
    rows += [r for r in map(json.loads, open(pp)) if r.get("text") and not any(rec["from"] <= r["start"] < rec["to"] for rec in recs)]
    return rows


def load(path):
    rows = [json.loads(l) for l in open(path)]
    for r in rows:
        r["a"], r["b"] = P(r["start"]), P(r["end"])
    return sorted(rows, key=lambda r: r["a"])


def overlap(x, y):
    return max(0.0, (min(x["b"], y["b"]) - max(x["a"], y["a"])).total_seconds())


def edit_distance(s, t):
    prev = list(range(len(t) + 1))
    for i, cs in enumerate(s, 1):
        cur = [i]
        for j, ct in enumerate(t, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (cs != ct)))
        prev = cur
    return prev[-1]


def score(gt, hyp):
    # per GT sentence: hypothesis label with the most overlap
    by_gt = []
    j = 0
    for g in gt:
        votes = Counter()
        while j < len(hyp) and hyp[j]["b"] <= g["a"] and (j + 1 >= len(hyp) or hyp[j + 1]["a"] <= g["a"]):
            j += 1
        while j and hyp[j - 1]["b"] > g["a"]:
            j -= 1
        k = j
        while k < len(hyp) and hyp[k]["a"] < g["b"]:
            o = overlap(g, hyp[k])
            if o > 0:
                votes[hyp[k]["speaker"] or "?"] += o  # unlabelled still counts as covered
            k += 1
        by_gt.append(votes.most_common(1)[0][0] if votes else None)
    labels = sorted({l for l in by_gt if l is not None}, key=str)
    truths = sorted({g["speaker"] for g in gt})
    # one-to-one labels -> truths maximizing agreement (Hungarian); every truth can be matched, however many
    from scipy.optimize import linear_sum_assignment
    M = [[sum(1 for g, x in zip(gt, by_gt) if x == l and g["speaker"] == t) for t in truths] for l in labels]
    mapping = {}
    if labels:
        r, c = linear_sum_assignment([[-v for v in row] for row in M])
        mapping = {labels[i]: truths[j] for i, j in zip(r, c) if M[i][j] > 0}

    def direct(l):  # 我 vs everyone else, whatever the other truth labels are called
        return None if l is None else l == "我"

    def acc(rows, fn):
        if fn is direct:
            return sum(1 for g, x in rows if x is not None and direct(x) == (g["speaker"] == "我")), len(rows)
        ok = sum(1 for g, x in rows if fn(x) == g["speaker"])
        return ok, len(rows)

    pairs = list(zip(gt, by_gt))
    HUMAN = {"ok", "me", "other", "mixed", "text"}  # verdicts from the owner's listening pass; consensus rows are not human-reviewed
    reviewed = [(g, x) for g, x in pairs if g.get("review") in HUMAN]
    covered = [(g, x) for g, x in pairs if x is not None]
    out = {
        "gt_sentences": len(gt), "hyp_sentences": len(hyp),
        "covered": sum(1 for x in by_gt if x is not None) / len(gt),
        "spk_direct": acc(pairs, direct), "spk_mapped": acc(pairs, mapping.get),
        "spk_direct_covered": acc(covered, direct),
        "spk_direct_reviewed": acc(reviewed, direct), "spk_mapped_reviewed": acc(reviewed, mapping.get),
        "mapping": {str(k): v for k, v in mapping.items()},
    }
    # mixed hypothesis sentences
    mixed = 0
    for h in hyp:
        t = Counter()
        for g in gt:
            if g["b"] <= h["a"]:
                continue
            if g["a"] >= h["b"]:
                break
            t[g["speaker"]] += overlap(g, h)
        mixed += sum(1 for v in t.values() if v >= 0.5) >= 2
    out["mixed"] = (mixed, len(hyp))
    # purity: share of covered truth sentences whose label's majority truth is their own; merged people lower it
    by_label = {}
    for g, x in pairs:
        if x is not None and x != "S?":
            by_label.setdefault(x, Counter())[g["speaker"]] += 1
    n = sum(sum(c.values()) for c in by_label.values())
    out["purity"] = sum(c.most_common(1)[0][1] for c in by_label.values()) / n if n else None
    out["hyp_labels"] = dict(Counter(h["speaker"] for h in hyp).most_common(8))  # registry ids: ideally one per real person
    # CER per 60 s window
    t0 = gt[0]["a"]
    win = lambda r: int((r["a"] - t0).total_seconds() // 60)
    gw, hw = {}, {}
    for r in gt:
        gw[win(r)] = gw.get(win(r), "") + PUNCT.sub("", r["text"])
    for r in hyp:
        hw[win(r)] = hw.get(win(r), "") + PUNCT.sub("", r.get("text") or "")
    errs = sum(edit_distance(hw.get(w, ""), gw[w]) for w in gw)
    out["cer"] = errs / max(1, sum(len(v) for v in gw.values()))
    lat = [(P(h["shown_at"]) - h["b"]).total_seconds() for h in hyp if h.get("shown_at")]
    out["latency_s"] = statistics.median(lat) if lat else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--hyp", help="hypothesis jsonl")
    ap.add_argument("--live-data", help="instead of --hyp: a live data dir, scored for --date")
    ap.add_argument("--date")
    ap.add_argument("--label", default="")
    ap.add_argument("--gt-until", help="drop GT sentences starting at/after this ISO time (untrusted tail)")
    a = ap.parse_args()
    gt = load(a.gt)
    if a.gt_until:
        gt = [g for g in gt if g["start"] < a.gt_until]
    if a.live_data:
        hyp = load_live(a.live_data, a.date)
        for r in hyp:
            r["a"], r["b"] = P(r["start"]), P(r["end"])
        hyp.sort(key=lambda r: r["a"])
    else:
        hyp = load(a.hyp)
    r = score(gt, hyp)
    f = lambda p: f"{p[0]}/{p[1]} = {p[0] / max(1, p[1]):.1%}"
    print(f"== {a.label or a.hyp}")
    print(f"  sentences gt {r['gt_sentences']}  hyp {r['hyp_sentences']}  covered {r['covered']:.1%}")
    print(f"  spk_direct {f(r['spk_direct'])}   reviewed {f(r['spk_direct_reviewed'])}   covered-only {f(r['spk_direct_covered'])}")
    print(f"  spk_mapped {f(r['spk_mapped'])}   reviewed {f(r['spk_mapped_reviewed'])}   mapping {r['mapping']}")
    print(f"  mixed hyp sentences {f(r['mixed'])}   cer {r['cer']:.1%}   latency {r['latency_s']}   labels {r['hyp_labels']}")
    print(json.dumps({k: v for k, v in r.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
