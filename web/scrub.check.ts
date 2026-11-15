// node --experimental-strip-types scrub.check.ts: the fast-scroll rail's layout and lookups (src/lib/scrub.ts)
import assert from "node:assert/strict";
import { convWeights, layout, stopAt, timeWeights } from "./src/lib/scrub.ts";

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
console.log("scrub ok");
