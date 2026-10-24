// node --experimental-strip-types track.check.ts: the conversation player's windows and lookups (src/lib/track.ts)
import assert from "node:assert/strict";
import { clock, isoLocal, rowAt, talkShare, WINDOW, windowAsk, windowLen, windowStart } from "./src/lib/track.ts";

const from = Date.parse("2026-10-24T09:30:00");
assert.equal(isoLocal(from), "2026-10-24T09:30:00.000");
assert.equal(isoLocal(Date.parse("2026-10-24T23:59:59.250")), "2026-10-24T23:59:59.250");

// windows from the anchor: 10 s, 30 s, then 110 s each, back to back
assert.deepEqual([0, 1, 2, 3].map(windowStart), [0, 10_000, 40_000, 150_000]);
assert.deepEqual([0, 1, 2, 3].map(windowLen), [10_000, 30_000, WINDOW, WINDOW]);
// 180 s: 10 + 30 + 110 and a 30 s tail, each asked 0.2 s inside its ends, none over the server's 120 s
const to = from + 180_000;
assert.deepEqual(windowAsk(from, to, 0), ["2026-10-24T09:30:00.200", "2026-10-24T09:30:09.800"]);
assert.deepEqual(windowAsk(from, to, 1), ["2026-10-24T09:30:10.200", "2026-10-24T09:30:39.800"]);
assert.deepEqual(windowAsk(from, to, 2), ["2026-10-24T09:30:40.200", "2026-10-24T09:32:29.800"]);
assert.deepEqual(windowAsk(from, to, 3), ["2026-10-24T09:32:30.200", "2026-10-24T09:32:59.800"]);
assert.equal(windowAsk(from, to, 4), null);
assert.equal(windowAsk(from, from + 40_000 + 300, 2), null); // a 0.3 s tail is not asked for
assert.ok(WINDOW - 400 <= 120_000);

const starts = [10, 20, 30];
assert.deepEqual([5, 10, 15, 30, 99].map((ms) => rowAt(starts, ms)), [-1, 0, 0, 2, 2]);
assert.equal(rowAt([], 5), -1);

const T = (s: number) => `2026-10-24T09:30:${String(s).padStart(2, "0")}`;
assert.deepEqual(
  talkShare([
    { speaker: "我", start: T(0), end: T(1) },
    { speaker: "同事A", start: T(1), end: T(4) },
    { start: T(4), end: T(9) }, // no speaker: not counted
  ]),
  [
    { who: "同事A", share: 0.75 },
    { who: "我", share: 0.25 },
  ],
);
assert.deepEqual(talkShare([]), []);

assert.deepEqual([0, 83_400, 600_000, 3_723_000].map(clock), ["0:00", "1:23", "10:00", "1:02:03"]);
console.log("track ok");
