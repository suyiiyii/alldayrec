---
type: explanation
---

# Limitations and roadmap

## Current limitations

- **No import button.** Recordings you already have can only be imported through the HTTP API; see [Import recordings you already have](../how-to/import-recordings.md).
- **v0.1.0's UI is in Chinese only.** Since then the web UI comes in Chinese and English (switch it on the 「我的」 (Me) page) and the app follows the system language since 1.5.1.
- **You build the iOS app yourself.** The app isn't on the App Store; an app installed with a free Apple ID has to be installed again after 7 days.
- **The LLM and the default ASR run in the cloud.** Running fully local needs `ENGINE=local` (worse than Qwen on far-field audio, see [ASR benchmark](./asr-benchmark.md)) plus an LLM server you run yourself.
- **v0.1.0 can't undo marks or merges.** A to-do's 「完成 / 不对」 (done / wrong) marks and a speaker merge can be undone on main (tap **撤销** (Undo) in the notice), but not yet in v0.1.0.
- **Short conversations get no summary.** A conversation with under 60 seconds of speech doesn't go to the LLM; its first sentence becomes the title.

## Roadmap

- **Import**: import recording files directly from the app or the CLI.
- **Desktop split view**: the list on the left, the details on the right on wide screens.
- **People cards**: add what you talked about lately to the existing people cards.
- **Player polish**: keep improving the conversation page's bottom player and follow-along highlight.


The roadmap changes; the latest discussion is in [GitHub issues](https://github.com/suyiiyii/alldayrec/issues).
