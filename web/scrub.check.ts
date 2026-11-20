// node --experimental-strip-types scrub.check.ts: the fast-scroll rail's layout and lookups (src/lib/scrub.ts)
import assert from "node:assert/strict";
import { atTarget, convWeights, layout, ownerOf, runsOf, stopAt, timeWeights } from "./src/lib/scrub.ts";

assert.deepEqual(layout([1, 1, 2]), [0, 0.25, 0.5, 1]);
assert.deepEqual(layout([0, 0]), [0, 0, 1]); // no weight at all: nothing divides by zero
assert.deepEqual(layout([]), [1]);

const cum = layout([1, 1, 2]);
assert.deepEqual(
  [0, 0.1, 0.25, 0.49, 0.5, 0.99, 1].map((f) => stopAt(cum, f)),
  [0, 0, 1, 1, 2, 2, 2],
);
assert.equal(stopAt(cum, -1), 0); // past either end: the first or the last
assert.equal(stopAt(cum, 2), 2);
assert.equal(stopAt(layout([5]), 0.7), 0);

// a 300-sentence meeting dwarfs a 2-line chat, the chat still gets some rail
const [big, small] = convWeights([300, 2, 20, 10, 40]);
assert.ok(big > 10 * small && small > 2);
assert.deepEqual(convWeights([]), []);

// 0 s, 10 s, 10.2 s, end 70 s: by the time to the next stop, a second at least
assert.deepEqual(timeWeights([0, 10_000, 10_200], 70_000), [10_000, 1000, 59_800]);

// 3000 stops: a lookup is a binary search, so dragging stays cheap
const many = layout(Array.from({ length: 3000 }, (_, i) => 1 + (i % 7)));
for (let i = 0; i < 3000; i += 97) assert.equal(stopAt(many, (many[i] + many[i + 1]) / 2), i);
// a row's conversation: its device's, or one with no device; a row in the gap between two is in none (the rail gives
// those their own stop, not the next conversation's)
const T = (h: string) => `2026-11-16T${h}`;
const convs = [
  { id: "b", start: T("14:02"), end: T("14:32"), device: "D1" },
  { id: "a", start: T("09:00"), end: T("09:30"), device: "D1" },
  { id: "x", start: T("10:00"), end: T("10:10"), device: null },
];
const own = ownerOf(convs);
assert.equal(own({ start: T("09:10"), source: "D1" })?.id, "a");
assert.equal(own({ start: T("14:32"), source: "D1" })?.id, "b"); // its end included
assert.equal(own({ start: T("13:00"), source: "D1" }), undefined); // the gap: not 14:02's
assert.equal(own({ start: T("01:03"), source: "D1" }), undefined); // before the first
assert.equal(own({ start: T("09:10"), source: "D2" }), undefined); // another device's
assert.equal(own({ start: T("10:05"), source: "D2" })?.id, "x"); // one with no device: any row's
assert.equal(own({ start: T("10:05") })?.id, "x");
assert.equal(own({ start: T("09:10") }), undefined);
assert.equal(ownerOf([])({ start: T("09:00"), source: "D1" }), undefined);
// the live page's stops (runsOf over its timeline, with ownerOf): devices A and B in two conversations at once, their
// media rows interleaved in one fold. Each keeps its own stop with all its rows; B's first row is folded, so its stop
// points at the fold. Words in no conversation in between: a stop of their own; the one going on: its own
const R = (h: string, source: string) => ({ start: T(h), source });
const ab = ownerOf([
  { id: "A", start: T("10:00"), end: T("10:30"), device: "A" },
  { id: "B", start: T("10:03"), end: T("10:40"), device: "B" },
]);
const fold = [R("10:05", "A"), R("10:06", "B"), R("10:07", "A"), R("10:08", "B")];
const runs = runsOf(
  [
    { k: "row", r: R("10:01", "A") },
    { k: "fold", rows: fold },
    { k: "row", r: R("10:09", "B") },
    { k: "conv" },
    { k: "row", r: R("10:50", "A") }, // after A's end, A not going: in none
    { k: "gap" },
    { k: "row", r: R("11:00", "B") }, // B going since 10:55
    { k: "row", r: R("11:01", "A") },
  ],
  ab,
  (r) => (r.source === "B" ? T("10:55") : undefined),
);
assert.deepEqual(
  runs.map((x) => [x.key[0] === "l" ? "loose" : x.key, x.target.slice(11), x.rows.length]),
  [
    ["cA", "10:01", 3],
    ["cB", "10:05", 3], // B: not lost in A's fold, aimed at the fold (its 10:06 row has no element there)
    ["loose", "10:50", 1],
    ["gB", "11:00", 1],
    ["loose", "11:01", 1],
  ],
);
assert.equal(runs[1].c?.id, "B");
assert.notEqual(runs[2].key, runs[4].key); // two runs in none, apart: two stops
// ?at= on the live page, a report on the sentence being said (10:59:01.5) right after a short draft (10:59:00): the
// rows come first, the sentence being said a moment later on the socket. Not the draft meanwhile, however near
const at = "2026-11-16T10:59:01.500",
  rows = ["2026-11-16T10:58:00.000", "2026-11-16T10:59:00.000"];
assert.equal(atTarget(rows, at, false), null); // wait
assert.equal(atTarget([...rows, at], at, false), at); // it came: there, at once
assert.equal(atTarget(rows, at, true), "2026-11-16T10:59:00.000"); // settled without it (a draft since redone): the nearest
assert.equal(atTarget([], at, true), null);
console.log("scrub ok");
