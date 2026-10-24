#!/bin/sh
# Score every case's saved hypotheses against its ground truth. Run after changing the pipeline:
#   set -a; . .env; set +a
#   ASR_PAD=0.4 uv run --extra local python -m alldayrec.replay_case <case> local-window && ./eval_all.sh
set -e
cd "$(dirname "$0")"
GT=${CASES_DIR:?set CASES_DIR to the directory of ground-truth cases}
for case in "$GT"/*/; do
  [ -f "$case/gt.jsonl" ] || continue
  until_arg=""
  [ -f "$case/gt.until" ] && until_arg="--gt-until $(cat "$case/gt.until")"   # untrusted tail, if any
  for hyp in "$case"/hyp/*.jsonl; do
    [ -f "$hyp" ] || continue
    .venv/bin/python -m alldayrec.score_case --gt "$case/gt.jsonl" --hyp "$hyp" --label "$(basename "$case") / $(basename "$hyp" .jsonl)" $until_arg | sed -n 1,5p
  done
done
