---
type: how-to
description: "With environmental sound on: read the sound tags on conversations and each day's sound bar on the timeline, filter conversations by music, vehicle or quiet, and tap 不对 (wrong) on a wrong one."
---

# How to read the sound tags

For an account with [environmental sound](../reference/configuration.md#optional-environmental-sound) on, the server guesses a few things from the sound of the recordings: someone speaking, music, being in a vehicle (a car, a subway and a plane are not told apart), quiet. A model guesses them and can be wrong, so the pages word each one 「可能」 (maybe). They say what was heard, never where you were. For an account without environmental sound, none of this shows.

## A conversation's sound

- Under the title of a conversation card on the timeline and of the conversation page: up to 3 sound tags, most minutes first, for example 「🎵 可能有背景音乐 12 分钟」 (maybe music, 12 min), 「🚗 可能在交通工具上 8 分钟」 (maybe in a vehicle, 8 min), 「🤫 可能大半时间安静」 (maybe mostly quiet).
- Short ones are left out: at least 2 minutes, and at least a tenth of the conversation's time with sound data. Speech is not a tag (every conversation has it).

## A day's sound bar

Under each day's heading on the timeline there is an 「环境声音」 (ambient sound) card drawn like a sleep app's sleep stages: four rows for speech, music, vehicle and quiet, lit where those minutes may have it; the thin line under them shows where there is sound data at all. **Blank means no data** (nothing recorded, or not scored yet), not quiet.

Tap a stretch of the bar to see its start, end and what may have been heard:

- in a conversation: 「看这段对话」 (open the conversation) opens its transcript at that moment;
- outside any conversation: 「听这一分钟」 (play this minute) plays a minute of the recording from there (not once the recording has been trimmed).

## Find conversations by sound

At the front of the tag row under the timeline's search box are 「🎵 有音乐」 (music), 「🚗 在交通工具上」 (in a vehicle) and 「🤫 安静」 (quiet): tap one to see only the conversations with that sound tag, tap it again to stop. They work together with words and tags.

## Wrong: tap 不对

Each sound tag on the conversation page, and each event of the stretch picked on the sound bar, has 「不对」 (wrong) beside it. Tapping it keeps that device's stretch of time, the event and the model it came from, and the tag reads struck through; 「撤销」 (undo) deletes the record. The records are only for improving the rules later; what is kept and how to delete it: [Privacy](../explanation/privacy.md#environmental-sound).

An AI agent connected over [MCP](./connect-mcp.md) reads them too: one more line of ambient sound under each conversation, a sound filter on `search`, and the `sounds` tool listing a day's sound by time (see [MCP tools](../reference/mcp.md)).
