"""Who-said-it on public audio: a day of made-up conversations from the eval pack (AISHELL-1, Apache 2.0, see
PACK_URL), labelled the way a closed conversation is (diarist.relabel) with 我's voiceprint and the people bank
(bank.pick, bank.samples) as the user teaches it, conversation after conversation. Scores: a known person's sentences
given their name, no name or another; 我's given 我; a stranger's given a name; and per conversation how pure its labels
are. CI (speaker-eval.yml) runs it nightly and fails on a step back from BASELINE by more than SLACK: a guard
against regressions, not a benchmark.

    python -m alldayrec.speaker_eval build <aishell dir> <pack dir>   # the maintainer: from resource_aishell + the unpacked wav tars
    python -m alldayrec.speaker_eval run <pack dir> [--out result.json] [--summary file.md]
"""
import json
import random
import subprocess
import sys
import wave
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from alldayrec import bank, diarist

PACK_URL = "https://github.com/suyiiyii/alldayrec/releases/download/eval-spk-v1/eval-spk-v1.tar.gz"
SEED = 20261108
SPEAKERS = 40          # picked from AISHELL-1's 400, half of each gender (resource_aishell/speaker.info)
UTTS = 30              # utterances kept of each, its first in id order
ME_UTTS = 5            # 我's last ones: the voiceprint selfvoice would enrol, never in a conversation
SR = 16000
CONVS, KNOWN = 30, 12  # conversations in the day; the people among the others who come back (the rest: strangers)
# sentence lengths a conversation is cut into (s): the server's rows run from a word to a long turn
LENGTHS = (0.6, 0.9, 1.3, 1.8, 2.5, 3.5, 5.0, 7.0)
NOTICE = """eval-spk-v1: a subset of AISHELL-1 (https://www.openslr.org/33/), re-encoded as Opus 32 kb/s.
AISHELL-1 is by Beijing Shell Shell Technology Co., Ltd., licensed under the Apache License 2.0
(https://www.apache.org/licenses/LICENSE-2.0). Changes: 40 speakers and 30 utterances of each picked
(alldayrec.speaker_eval.pick, seed 20261108), converted from 16 kHz WAV to Opus. manifest.json lists them.
"""
# main's rates (11-09, 4e49627: K=5 bank, voice check); raise them with the change that earns it
BASELINE = {"known_right": 73.7, "known_wrong": 3.2, "me_right": 82.3, "stranger_named": 0.0, "purity": 93.4}
# a nightly run fails when a rate moves the wrong way by more than this (points): the day is fixed, so a move is the code
SLACK = {"known_right": 3.0, "known_wrong": 1.0, "me_right": 2.0, "stranger_named": 1.0, "purity": 2.0}


def pick(info: Path) -> list[str]:
    """SPEAKERS ids of AISHELL-1 (speaker.info: id gender), half of each gender, by SEED."""
    sp = [line.split() for line in info.read_text().splitlines() if line.strip()]
    r = random.Random(SEED)
    return sorted(r.sample(sorted(s for s, g in sp if g == "M"), SPEAKERS // 2) + r.sample(sorted(s for s, g in sp if g == "F"), SPEAKERS // 2))


def build(root: Path, out: Path):
    """The pack: each picked speaker's first UTTS utterances as Opus (ffmpeg), and manifest.json listing them."""
    out.mkdir(parents=True, exist_ok=True)
    info = root / "resource_aishell" / "speaker.info"
    man = {"source": "AISHELL-1, https://www.openslr.org/33/ (Apache License 2.0), Beijing Shell Shell Technology Co., Ltd.",
           "seed": SEED, "speakers": []}
    (out / "NOTICE").write_text(NOTICE)
    for s in pick(info):
        files = sorted(root.rglob(f"*S{s}W*.wav"))[:UTTS]  # data_aishell/<split>/S0002/BAC009S0002W0122.wav
        if len(files) < UTTS:
            raise SystemExit(f"S{s}: {len(files)} utterances under {root}, not {UTTS} (its wav tar unpacked?)")
        utts = []
        for f in files:
            dst = out / f"S{s}" / (f.stem + ".ogg")
            dst.parent.mkdir(exist_ok=True)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(f), "-ac", "1", "-ar", str(SR), "-c:a", "libopus",
                            "-b:a", "32k", str(dst)], check=True)
            with wave.open(str(f)) as w:
                utts.append({"file": f"S{s}/{dst.name}", "id": f.stem, "s": round(w.getnframes() / w.getframerate(), 2)})
        man["speakers"].append({"id": f"S{s}", "utts": utts})
    (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1))


def decode(p: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(p), "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.int16)


def day(man: dict) -> list[list[tuple[int, str, float]]]:
    """The conversations: each a list of (speaker index, utterance file, seconds kept). Speaker 0 is 我 and in every
    one; 1..KNOWN come back again and again; the others are strangers, in one conversation each."""
    r = random.Random(SEED)
    n = len(man["speakers"])
    strangers = list(range(KNOWN + 1, n))
    used = Counter()
    out = []
    for _ in range(CONVS):
        who = [0] + r.sample(range(1, KNOWN + 1), r.choice((1, 1, 2, 3)))
        if strangers and r.random() < 0.5:
            who.append(strangers.pop(0))
        turns = []
        for _ in range(r.randint(10, 24)):
            k = r.choice(who)
            utts = man["speakers"][k]["utts"][:-ME_UTTS] if k == 0 else man["speakers"][k]["utts"]  # 我's last: enrolment
            u = utts[used[k] % len(utts)]
            used[k] += 1
            turns.append((k, u["file"], min(u["s"], r.choice(LENGTHS))))
        out.append(turns)
    return out


def run(pack: Path) -> dict:
    """Every rate of the day, in points."""
    from alldayrec import infer
    from alldayrec.routes_speakers import ENROLL
    infer.fetch()
    infer.check()
    embed = infer.embedder()
    man = json.loads((pack / "manifest.json").read_text())
    convs = day(man)
    me = [embed(decode(pack / u["file"])) for u in man["speakers"][0]["utts"][-ME_UTTS:]]  # 我, as selfvoice enrols
    owner = {"self": diarist._unit(np.mean(me, 0))}
    taught: dict[str, list[tuple]] = defaultdict(list)  # name: its ledger rows (conv, row, n, e), as bank.learn writes them
    n = Counter()
    purity = []
    for ci, turns in enumerate(convs):
        E, dur, truth = [], [], []
        for k, f, s in turns:
            x = decode(pack / f)[:int(s * SR)]
            E.append(embed(x) if s >= 0.3 else None)
            dur.append(s)
            truth.append(k)
        people = {k: bank.samples(v) for k, v in taught.items()}
        labels = diarist.relabel(E, dur, owner, people=people)
        for k, lab in zip(truth, labels):
            name = f"P{k}"
            if k == 0:
                n["me"] += 1
                n["me_right"] += lab == "我"
            elif name in people:
                n["known"] += 1
                n["known_right"] += lab == name
                n["known_wrong"] += lab not in (name,) and not diarist_anon(lab)
            elif k > KNOWN:
                n["stranger"] += 1
                n["stranger_named"] += not diarist_anon(lab) and lab != "我"
        by = defaultdict(list)
        for k, lab in zip(truth, labels):
            by[lab].append(k)
        purity.append(sum(Counter(v).most_common(1)[0][1] for v in by.values()) / len(labels))
        # the user names what was not named: each unnamed label of a returning person, after the conversation
        for lab, ks in by.items():
            k, c = Counter(ks).most_common(1)[0]
            if diarist_anon(lab) and 1 <= k <= KNOWN and c > len(ks) / 2:
                idx = [i for i, l in enumerate(labels) if l == lab and E[i] is not None and dur[i] >= bank.LEARN_MIN_S][:ENROLL]
                if idx:  # as apply_rename and enroll teach it
                    keep = bank.pick([[E[i] for i in idx]], people.get(f"P{k}"), owner.values())
                    taught[f"P{k}"] += [(ci, idx[j], 1, E[idx[j]]) for _, j in keep]
    pct = lambda a, b: round(100 * n[a] / max(n[b], 1), 1)
    return {"known_right": pct("known_right", "known"), "known_wrong": pct("known_wrong", "known"), "me_right": pct("me_right", "me"),
            "stranger_named": pct("stranger_named", "stranger"), "purity": round(100 * float(np.mean(purity)), 1),
            "sentences": {k: n[k] for k in ("known", "me", "stranger")}}


def diarist_anon(lab: str) -> bool:
    return lab == "S?" or (lab[:1] == "S" and lab[1:].isdigit())


def worse(res: dict, base: dict) -> list[str]:
    """The rates that moved the wrong way by more than SLACK."""
    out = []
    for k, s in SLACK.items():
        d = round(res[k] - base[k], 1)  # rates in tenths: 4.2 - 3.2 is not past a slack of 1.0
        if (-d if k in ("known_right", "me_right", "purity") else d) > s:
            out.append(f"{k} {base[k]} -> {res[k]}")
    return out


def table(res: dict, base: dict) -> str:
    rows = ["| rate | now | baseline |", "|---|---|---|"]
    rows += [f"| {k} | {res[k]} | {base[k]} |" for k in SLACK]
    return "\n".join(rows) + f"\n\nsentences: {res['sentences']}\n"


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["build", root, out]:
            build(Path(root), Path(out))
        case ["run", pack, *opts]:
            o = dict(zip(opts[::2], opts[1::2]))
            res = run(Path(pack))
            md = "## Speaker eval (AISHELL-1 day)\n\n" + table(res, BASELINE)
            bad = worse(res, BASELINE)
            md += ("\n**Worse than the baseline:** " + "; ".join(bad) + "\n") if bad else ""
            print(md)
            if "--summary" in o:
                with open(o["--summary"], "a") as f:
                    f.write(md)
            if "--out" in o:
                Path(o["--out"]).write_text(json.dumps(res, indent=1))
            raise SystemExit(1 if bad else 0)
        case _:
            raise SystemExit(__doc__)
