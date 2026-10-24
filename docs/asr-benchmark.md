---
type: explanation
---

# ASR benchmark: local engines vs cloud

> 中文摘要：2026-10-23 在 5 段真实录音（共 1.77 小时）上，比较了能在 CPU 上跑的开源语音识别模型和几家云端引擎。
> 最好的本地模型 X-ASR（`ENGINE=local` 用的就是它）和 SenseVoice-Small 句内字错率约 19.6，默认的云端千问是 13.5，差约 6 个点，和千问之前用的阶跃 SSE（18.2）相当。
> 速度不是问题：X-ASR 在 4 核上 RTF 0.024，一天 5 小时的语音约 7 分钟算完；N100、树莓派 5（推算）约 17 分钟、25–32 分钟。
> 带大模型解码器的模型（Qwen3-ASR-0.6B）在 CPU 上不划算：不更准，算力约 10 倍；sherpa-onnx 要用 2.0.0 及以上。
> 参考答案（GT）偏向部分厂商，数字适合排序、不宜当绝对值；方法、注意事项和在自己的录音上复跑的办法见下文。

Measured on 2026-10-23. The question: can an open-weight model on a CPU replace the cloud engine of the final
transcript? Everything ran locally on five private recordings; no audio or ground truth is published, only the numbers.

## Key findings

- **Local is about 6 CER points behind the default cloud engine.** Mean in-sentence CER over the 5 cases:
  X-ASR-zh-en int8 **19.7**, SenseVoice-Small 2024 int8 **19.5**, against Bailian qwen-audio-3.1 (`ENGINE=qwen`) 13.5,
  Volcengine 12.1 and StepFun SSE 18.2 (the cloud engine used before qwen). So local is roughly StepFun-SSE class.
- **Speed is not the problem.** X-ASR runs at RTF 0.024 on 4 threads of a 16-core desktop (Zen 3) and 0.012 on an
  Apple M4: 7 min and 3.5 min of decoding for a day with 5 h of speech. An N100 or a Raspberry Pi 5 (extrapolated) needs
  about 17 min and 25–32 min.
- **LLM-decoder models did not pay off on CPU.** Qwen3-ASR-0.6B: 20.9 CER at 10× the compute and 1.2 GB RAM after load.
  Fun-ASR-Nano-2512 could not be evaluated (setup failure, see the notes).
- **The sherpa-onnx version matters.** On the home case 2.0.0 lifts Qwen3-ASR by about 2 points over 1.13.8 and partly fixes
  Fun-ASR-Nano; CTC and transducer models barely move. The server pins `sherpa-onnx>=2.0.0`.
- **`ENGINE=local` uses X-ASR** (Apache-2.0, 168 MB on disk from a ~136 MB tarball, about 330 MB RAM, token times and
  cased punctuation). SenseVoice-Small ties on the mean and is best on the most trustworthy case, but has a custom
  licence and weaker punctuation.

## Method

**Cases.** Five real-life recordings with ground truth, 1.77 h of audio in total (6358 s, about 21 min per case on
average), of which 4789 s is speech:

| Case | What it is | Where its GT came from |
|---|---|---|
| home | at home | human-adjudicated from Doubao, StepFun and Volcengine candidates (the most trustworthy) |
| meeting | a meeting | Doubao |
| dji | outdoor walk, DJI wireless mic | Doubao |
| phone | the phone's own mic | Doubao |
| evening | an evening recording | StepFun |

**GT bias, read before the numbers.** Meeting, dji and phone GT came from Doubao, which favours Volcengine and Doubao.
Evening GT came from StepFun, which favours StepFun SSE (its 7.6 there). Home was adjudicated by a person, but from
Doubao, StepFun and Volcengine candidates. Every engine outside that set, which includes every local model, is
slightly penalised everywhere. On home alone: SenseVoice 16.5, Paraformer 19.0, X-ASR 19.7, Qwen3-ASR 20.6, against
qwen 11.6 and Volcengine 7.3.

**What the model sees.** Each case is cut in pauses into blocks of at most 5 min, the same blocks as an earlier cloud
comparison. Inside a block the server's own loose VAD (`segmenter.Segmenter`, `SQUEEZE_VAD` 0.3) finds the speech, padded
0.2 s, merged across gaps under 0.5 s and cut to at most 20 s at the quietest 100 ms. One decode per segment; that is
what `ENGINE=local` does inside a window.

**Runtime.** sherpa-onnx int8 builds on CPU, version 1.13.8 on the desktop (the newest wheel on the mirror used) and
2.0.0 on the M4.

**CER.** Text is NFKC-normalised, lowercased, letters and digits only.
- *In-sentence CER* counts hypothesis characters whose time falls inside a GT sentence ±0.5 s, against all GT text.
  It ignores text an engine invents in silence or in stretches the GT skipped.
- *Whole CER* compares all text of the blocks with all GT text, no timing involved.
- The scorer reproduces the earlier cloud comparison exactly (qwen in-sentence 11.6 / 5.6 / 14.9 / 23.0 / 12.2,
  StepFun SSE whole 14.4 / 14.1 / 21.8 / 39.9 / 7.9), so local and cloud rows are on the same scale.
- Models without token times get their characters spread evenly over the segment, which costs them slightly in
  in-sentence CER; whole CER is unaffected.

**Mixed rows.** The share of hypothesis rows spanning two GT speakers, rows split at 。？！ and pauses over 0.5 s. A
row that spans two people gets one speaker label, so this bounds speaker accuracy for text-only engines.

**Speed and memory.**
- Desktop: `nice -n 19`, pinned to 4 physical cores, `num_threads=4`; 1-thread runs on one core. At times two jobs ran
  at once on different CCDs.
- M4: home case only, on a machine busy with other work (load 3–9).
- RTF = decode seconds / speech seconds decoded. Per second of recorded audio it is ×0.75.
- "RSS after load" is the process RSS right after creating the recognizer. Run peaks are higher: they include about
  0.4 GB of decoded case audio held by the benchmark, plus decoder state (Qwen3-ASR 3.4 GB, Fun-ASR-Nano 3.2 GB, the
  others 1.3–2.0 GB). Load time is with a warm page cache.

## Results

Per-case order: home / meeting / dji / phone / evening. CER and mixed rows in %.

| Engine (int8, sherpa-onnx, CPU) | In-sentence CER per case | **Mean** | Whole CER | Mixed rows | RTF 4 thr, desktop | RTF 1 thr, desktop | RTF 4 / 1 thr, M4 | Load s | RSS after load | Disk |
|---|---|---|---|---|---|---|---|---|---|---|
| **X-ASR-zh-en** zipformer, punct, 2026-06-03 | 19.7 / 8.6 / 25.3 / 31.2 / 13.7 | **19.7** | **21.9** | 11.2 | 0.024 | 0.045 | 0.012 / 0.015 | 2.2 | 328 MB | 168 MB |
| **SenseVoice-Small** 2024-07-17 | **16.5** / 9.8 / 24.9 / 30.0 / 16.1 | **19.5** | 24.3 | 18.2 | 0.023 | 0.074 | 0.012 / 0.032 | 1.1 | 356 MB | 229 MB |
| Qwen3-ASR-0.6B (LLM decoder) | 20.6 / 9.8 / 28.2 / 29.9 / 15.8 | 20.9 | 24.1 | 9.3 | 0.229 | 0.587 | 0.133 / – | 3.8 | 1214 MB | 942 MB |
| Fun-ASR-Nano encoder + CTC head | 20.3 / 11.4 / 27.0 / 33.7 / 17.5 | 22.0 | 27.2 | 30.9 (no punct) | 0.026 | – | – | 1.2 | 391 MB | 253 MB |
| SenseVoice 2025-09-09 (Cantonese fine-tune, no punct) | 20.9 / 10.8 / 30.9 / 36.5 / 16.9 | 23.2 | 26.1 | 31.6 | 0.020 | – | – | 1.4 | – | 227 MB |
| FireRedASR2 CTC | 20.3 / 13.8 / 27.8 / 38.3 / 17.5 | 23.5 | 28.2 | 30.6 (no punct) | 0.210 | – | – | 2.0 | 949 MB | 742 MB |
| Paraformer-large zh 2024-03-09 (no punctuation model applied) | 19.0 / 11.1 / **46.9** / 30.3 / 18.0 | 25.1 | 27.4 | 33.9 | 0.017 | – | – | 1.7 | 452 MB | 217 MB |
| Fun-ASR-Nano-2512 (LLM decoder): **setup failure, not a model result** | 845 / 132 / 231 / 354 / 301 | (372) | (427) | – | 0.345 | – | – | 6.0 | 1221 MB | 963 MB |
| *FireRedASR2-AED fp32 on a **GPU** (earlier run, rescored)* | 18.2 / 8.8 / 24.7 / 33.7 / 17.8 | 20.6 | 23.7 | 9.5 | GPU: 5.6–30× real time, 5.5 GB VRAM | | | | | |

Cloud references, same blocks and same scorer:

| Cloud engine | In-sentence CER per case | **Mean** | Whole CER | Mixed rows | Price per hour of audio |
|---|---|---|---|---|---|
| Volcengine Seed-ASR 2.0 (earlier run; whole CER not computed) | 7.3 / 4.8 / 13.1 / 23.0 / 12.2 | 12.1 | – | – | – |
| **Bailian qwen-audio-3.1 file transcription** (`ENGINE=qwen`, the default) | 11.6 / 5.6 / 14.9 / 23.0 / 12.2 | 13.5 | 16.7 | 0.9 | ~¥0.05 |
| Tencent Cloud 16k_zh_en_2.0 | 11.5 / 6.3 / 16.8 / 19.0 / 14.9 | 13.7 | 16.8 | 2.0 | ~¥0.8 |
| Bailian fun-asr | 18.0 / 7.6 / 20.5 / 17.2 / 12.8 | 15.2 | 19.0 | 1.6 | ~¥0.49 |
| StepFun SSE (the cloud engine used before qwen) | 13.6 / 7.7 / 22.4 / 39.8 / 7.6 | 18.2 | 19.6 | 2.3 | ¥0.15 |
| StepFun file mode, 16 kHz Opus upload | 19.8 / 10.5 / 24.6 / 43.7 / 9.7 | 21.7 | 23.1 | 1.7 | ¥0.15 |

Notes:
- **X-ASR and SenseVoice tie on in-sentence CER** (19.7 vs 19.5). X-ASR is better on whole CER (21.9 vs 24.3), meeting
  and evening; SenseVoice on home and phone. X-ASR's punctuation splits rows better (mixed 11.2 % vs 18.2 %), it is
  faster on one thread (0.045 vs 0.074) and it is Apache-2.0; SenseVoice has a custom licence.
- **Qwen3-ASR-0.6B, the only LLM-decoder model that worked, is no better** (20.9 / 24.1) at 10× the compute and 3.4× the
  RAM. Its sherpa build gives no token times, which costs it a little in-sentence but not whole.
- **Fun-ASR-Nano-2512** (sherpa-onnx 1.13.8, default prompt, greedy) is **a setup failure, not a model result.** On home it
  produced 16,306 characters against the GT's 1,406, mostly Latin letters and repeat loops. sherpa's own bundled clean
  Mandarin clips looped the same way ("to be used to be used …"), so the runtime or the file pairing is at fault, not the
  weights. Its CTC head alone is sane (22.0). Published results rank it top for far-field audio; worth a retry.
- **sherpa-onnx 2.0.0 cross-check** (M4, home case only; the main table is 1.13.8):

  | Model | In-sentence CER, 1.13.8 → 2.0.0 | Whole CER, 1.13.8 → 2.0.0 |
  |---|---|---|
  | SenseVoice 2024 | 16.5 → 16.1 | 21.1 → 20.9 |
  | X-ASR | 19.7 → 19.3 | 23.2 → 22.2 |
  | Qwen3-ASR-0.6B | 20.6 → **18.4** | 24.1 → 22.4 |
  | Fun-ASR-Nano | 845 → **45.8** | → 55.9 |

  Fun-ASR-Nano still loops on 2.0.0 (2,368 characters for 1,406, of which 763 in repeat runs). The LLM decoders gain
  most from the newer runtime, so pin ≥ 2.0.0.
- **Paraformer** is worst on the outdoor dji walk (46.9), and this build returns no token times.
- **Package-name traps.** sherpa's `sense-voice … 2025-09-09` build is a Cantonese fine-tune with no punctuation (the
  row above); its `paraformer-zh … 2025-10-07` build is WSChuan, a Sichuanese fine-tune (30.9 mean, dropped). Use
  `sense-voice … 2024-07-17` and the 2024-03-09 Paraformer.
- **Token times.** Real per-token times from X-ASR, SenseVoice 2024, FireRed CTC and the Nano CTC head; none from
  Qwen3-ASR or the Paraformer 2024-03-09 build.
- No traditional or Cantonese characters appeared in any local output.
- **`score_case.py`'s own CER** (60 s windows) ranks the engines the same way: qwen 29.4, StepFun SSE 31.7, X-ASR 34.6,
  FireRed-AED 35.7, SenseVoice 36.3, Qwen3-ASR 37.1, Paraformer 40.0. Its level is inflated (meeting is 48–55 for every
  engine because its GT drops unintelligible and overlapping stretches); use it for ranking only.

## Speakers with a text-only engine

No open model that runs on CPU also returns speakers, so every local engine leaves speakers to the server's own
voiceprint diarist. Measured on Volcengine sentences (same text, different labels; mean of the 5 cases):

| Labels | 我 vs others | who is who | purity |
|---|---|---|---|
| online labels as the page first shows them | 84.3 | 75.8 | 91.6 |
| relabel at close **without engine speaker ids** (what a local engine gets) | 86.7 | 77.4 | 94.7 |
| relabel at close with engine speaker ids, guarded | 87.2 | 82.4 | 89.9 |

Engine ids add 0.5 pt on "is it me" and 5 pt on who-is-who, at a cost of 4.8 pt purity: the diarist does nearly all
the work, and a local engine only has to deliver text and sentence times. The open risk is sentence granularity:
local rows span two speakers 9–18 % of the time when split at 。？！ (cloud 0.9–2.3 %), and 2–5 % when also split at ， and
pauses over 0.3 s (cloud 0.1–0.3 %). That is why `ENGINE=local` cuts at both, using token times. End-to-end speaker
accuracy with real local sentence cuts was not measured in this run (see the last section for how).

## Load on small hardware

Assumption: one heavy user records 3–5 h of speech a day; a day that imports old recordings can reach ~21 h. On the
desktop the server's own VAD and voiceprint work, with cloud ASR, averaged about 0.15 core and 1.0 GB RSS.

CPU minutes per day = speech hours × 60 × RTF (4-thread wall time). Desktop and M4 are measured. **N100 and Pi 5 are
extrapolated, not measured**: no published sherpa-onnx CPU RTF was found for either, so the desktop's 4-thread RTF was
scaled by Geekbench 6 multi-core (desktop on 4 threads ≈ 3.2 × single-core 2226 ≈ 7100; N100 3129 → ×2.3; Pi 5
1604–2059 → ×3.4–4.4). The M4 beat the desktop per thread by 2.3–3.0×, more than Geekbench predicts, so the ARM
numbers are probably pessimistic; treat them as ±50 %. LLM decoders also stream about 0.75 GB of weights per token, and
on the N100 and Pi 5 memory bandwidth may bind first, so their Qwen3-ASR rows are optimistic.

| Box | Engine | RTF (4 thr) | Per 5 h of speech | Per 21 h import day | Average load | RAM for ASR |
|---|---|---|---|---|---|---|
| 16-core desktop (Zen 3), 4 cores, measured | X-ASR / SenseVoice | 0.024 / 0.023 | 7 min | 30 min | < 0.03 core | ~0.35 GB |
| Apple M4, measured | X-ASR / SenseVoice | 0.012 / 0.012 | 3.5 min | 15 min | < 0.02 core | ~0.35 GB |
| Intel N100 mini PC, extrapolated | X-ASR / SenseVoice | ~0.055 | ~17 min | ~70 min | ~0.05 core | ~0.35 GB |
| Raspberry Pi 5, extrapolated | X-ASR / SenseVoice | ~0.08–0.11 | ~25–32 min | ~1.7–2.3 h | ~0.1 core | ~0.35 GB |
| 16-core desktop, 4 cores, measured | Qwen3-ASR-0.6B | 0.229 | 69 min | 4.8 h | 0.2 core | 1.2 GB (peak 3.4) |
| Apple M4, measured | Qwen3-ASR-0.6B | 0.133 | 40 min | 2.8 h | 0.1 core | 1.2 GB (peak 2.5) |
| Intel N100, extrapolated | Qwen3-ASR-0.6B | ~0.53 | ~2.6 h | ~11 h | ~0.45 core | 1.2–3.4 GB |
| Raspberry Pi 5, extrapolated | Qwen3-ASR-0.6B | ~0.8–1.0 | ~4–5 h | 17–21 h, cannot catch up the same day | ~0.7–0.9 core | does not fit a 4 GB Pi beside the server |

- With 1 thread for ASR (leaving the other cores to the server), X-ASR's RTF is 0.045 on the desktop and 0.015 on the
  M4, about 0.08 on an N100 and 0.10–0.13 on a Pi 5: still at least 8× real time.
- A final piece is at most 300 s; X-ASR on an N100 needs about 15 s of CPU for it, so latency barely changes.
- On small boxes the server's existing VAD and voiceprint work is likely to outweigh ASR; that baseline was not
  measured on ARM or the N100. LLM-decoder ASR only makes sense on M4- or desktop-class CPUs.

**GPU** was not re-measured; one user doesn't need one. On an RTX 2060 Super 8 GB, FireRedASR2-AED fp32 ran 5.6–30×
real time in about 5.5 GB VRAM (the GPU row above), and an earlier test on an 11.7 min podcast took 83–98 s with
Fun-ASR-Nano (about 4 GB VRAM), 166 s with Qwen3-ASR-1.7B (degraded in its second half) and 8 s with SenseVoice.

## Models

Benchmarked (release dates of the models; published CERs use different normalisation, so don't compare them here):
- [X-ASR-zh-en](https://github.com/Gilgamesh-J/X-ASR) zipformer transducer, 2026-05-23, Apache-2.0, 0.16B; offline and
  streaming sherpa builds with punctuation.
- [SenseVoice-Small](https://huggingface.co/FunAudioLLM/SenseVoiceSmall), 2024-07, custom
  [FunASR Model License](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) v1.1, 234M.
- [Qwen3-ASR-0.6B](https://huggingface.co/Qwen/Qwen3-ASR-0.6B), 2026-01-29, Apache-2.0.
- [Fun-ASR-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512), 2025-12, Apache-2.0, 0.8B (and its CTC
  head, sherpa `sense-voice-funasr-nano-int8-2025-12-17`).
- [FireRedASR2](https://github.com/FireRedTeam/FireRedASR2S), 2026-02, Apache-2.0 (CTC int8 on CPU, AED on GPU).
- Paraformer-large zh, 2023, Apache-2.0 (sherpa build 2024-03-09).

Skipped: Whisper large-v3-turbo (weak on Chinese, mixes simplified and traditional output, hallucinates on silence);
Dolphin base/small (published KeSpeech 10.9 vs Qwen3-ASR-0.6B 7.1); GLM-ASR-Nano-2512 (4.5 GB; an earlier test collapsed
on an 11.7 min file); MOSS-Transcribe-Diarize (gives speakers, GPU-only in practice); VibeVoice-ASR (gives speakers,
published AISHELL-4 WER 21–27); 7–9B audio LLMs (GPU only). sherpa-onnx model pages:
<https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html>,
<https://k2-fsa.github.io/sherpa/onnx/funasr-nano/pretrained.html>.

## Run it on your own recordings

The scorer and replay tools are in `server/` (run `uv sync` there first). Lay out your cases like this:

```
$CASES_DIR/
  <case>/
    meta.json          # for replay_case.py: {"audio": "<file>", "device": "<name>", "pieces": [{"offset_s": 0, "wall": "<ISO time>"}, ...]}
    gt.jsonl           # ground truth, one sentence per line: start, end (wall-clock ISO), speaker, text
    gt.until           # optional: one ISO time; GT from there on is untrusted and dropped
    hyp/<name>.jsonl   # hypotheses, same shape as gt.jsonl
```

In `gt.jsonl` the wearer is `我`; give every other person their own label. Hypothesis speakers may be `我` / `他人` or
anonymous labels (`S1`, `SPEAKER_00`, …).

- `uv run python score_case.py --gt <case>/gt.jsonl --hyp <case>/hyp/<name>.jsonl [--gt-until <ISO>]` scores one hypothesis: 我-vs-others
  accuracy, who-is-who accuracy after a one-to-one label mapping, coverage, mixed rows, count ratio, CER per 60 s window
  (a different, higher scale than the tables above) and latency when present.
- `uv run python replay_case.py <case> engine-pieces` replays a case's audio through the server's final layer as it runs now
  (squeeze, lift, `PROVIDERS[ENGINE]`, diarist) and writes `<case>/hyp/engine-pieces.jsonl`; `HYP_NAME` renames it.
  Load the server's `.env` first (`set -a; . .env; set +a`); it needs your voiceprint bank (`OWNER_BANK`, an
  `owner-redimnet2.npz`). Run it with `ENGINE=local` to get the end-to-end speaker numbers this benchmark did not
  measure. Its case loader currently also requires `STEPFUN_API_KEY` to be set.
- `CASES_DIR=/path/to/cases ./eval_all.sh` scores every `hyp/*.jsonl` of every case against its `gt.jsonl`, honouring
  `gt.until`.

The in-sentence and whole CER of the tables came from a separate benchmark scorer that is not in the repository;
`score_case.py`'s 60 s-window CER gives the same ranking.
