---
type: explanation
---

# Limitations and roadmap

## Current limitations

- **You build the iOS app yourself.** The app isn't on the App Store; an app installed with a free Apple ID has to be installed again after 7 days.
- **The LLM and the default ASR run in the cloud.** Running fully local needs `ENGINE=local` (worse than Qwen on far-field audio, see [ASR benchmark](./asr-benchmark.md)) plus an LLM server you run yourself.
- **Short conversations get no summary.** A conversation with under 60 seconds of speech doesn't go to the LLM, and its first sentence becomes the title, unless 「立即总结」 (Summarize now) was tapped while it was going on.

## Roadmap

- **Import from the command line**: import recording files you already have with one command.
- **Desktop split view**: the list on the left, the details on the right on wide screens.
- **People cards**: add what you talked about lately to the existing people cards.
- **Player polish**: keep improving the conversation page's bottom player and follow-along highlight.


The roadmap changes; the latest discussion is in [GitHub issues](https://github.com/suyiiyii/alldayrec/issues).
