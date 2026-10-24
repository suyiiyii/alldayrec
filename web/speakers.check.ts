// node --experimental-strip-types speakers.check.ts: speakerColor and initial (src/lib/speakers.ts)
import assert from "node:assert/strict";
import { initial, speakerColor } from "./src/lib/speakers.ts";

assert.equal(speakerColor("我"), "var(--accent)");
for (const s of ["S2", "S?", "", null, undefined]) assert.equal(speakerColor(s), "var(--muted)");
assert.equal(speakerColor("同事A"), speakerColor("同事A"));
assert.match(speakerColor("同事A"), /^#[0-9a-f]{6}$/);
assert.equal(
  new Set(["妈妈", "同事A", "同事B", "室友", "小王", "Alice"].map(speakerColor)).size > 1,
  true,
);
assert.deepEqual(["同事A", "alice", "S2", "S?", "𠮷野", null].map(initial), [
  "同",
  "A",
  "2",
  "?",
  "𠮷",
  "?",
]);
console.log("speakers ok");
