---
type: how-to
description: "Keep the names, companies, products and jargon you use under Me → Terms: speech recognition listens for them, and summaries and answers fix words that sound like them."
---

# How to add terms

Transcripts often turn rare names, product names and jargon into words that sound alike. Put those words in the term list: speech recognition listens for them, and summaries and **Ask AI** spell a word that sounds like one of them the way the list does.

## Add, change and remove terms

1. Open **Me → Terms** (`/terms`) in the web app.
2. Type a term in the box and press Enter, or tap **Add**.
3. To change a term, tap it, edit it in the box, and press Enter or tap **Save**. Press Esc to cancel.
4. To remove a term, tap the **×** next to it.

A term is at most 30 characters. A change takes effect at once: the next audio sent for recognition and the next summary use it.

People you named on the People page (the names in the voiceprint bank, except 我) are added by themselves and shown under **Names added for you**. You do not need to add them.

## How each engine uses the list

When there are more terms than an engine takes, the most recently used come first: a term you typed counts from when you added or changed it, and a name from the last conversation the person was in.

| Engine | Sent as | Limit |
|---|---|---|
| `qwen` (final), `qwen-stream` (drafts) | Bailian's inline hotwords, `vocabulary`, each at weight 4 | 2000 terms; a term with Chinese characters up to 15 characters, an English one up to 7 words; longer ones are left out |
| `volc` | Volcengine's inline hotwords, `corpus.context` | 5000 terms; each up to 9 characters; longer ones are left out |
| `stepfun-sse`, `stepfun` | StepFun's `hotwords` | 500 terms |
| `stepfun-file`, `local` | Not sent | — |

With `stepfun`, speakers and sentence breaks come from the file-mode recognition service, which gets no terms. The text comes from the SSE call, which does.

With every engine, the prompts for summaries, the live analysis when a conversation ends, and **Ask AI** carry the list (up to 200 terms).

## Read the list from an AI agent

An AI agent connected over MCP can read the list with the `terms` tool (read-only). See [MCP tools](../reference/mcp.md).
