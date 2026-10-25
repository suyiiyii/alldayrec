// node --experimental-strip-types fragment.check.ts: isFragment (src/lib/api.ts), what the time line folds away
import assert from "node:assert/strict";
import { isFragment, type Conversation } from "./src/lib/api.ts";

const c = (speech_s?: number, media_share?: number) =>
  ({
    id: "c",
    start: "",
    end: "",
    participants: [],
    sentences: 1,
    speech_s,
    summary: { media_share },
  }) as Conversation;
assert.deepEqual([c(24), c(59.9), c(60), c(1625), c(), c(30, 0.9), c(30, 0.5)].map(isFragment), [
  true,
  true,
  false,
  false,
  false,
  false,
  true,
]);
