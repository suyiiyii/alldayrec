// node --experimental-strip-types web/todos.check.ts: an automatic mark undone on a page stays hidden there; a later
// one on the same todo (a newer sentence closed it again) shows, it is another mark; so does another todo's on the
// same sentence.
import assert from "node:assert/strict";
import type { AutoMark } from "./src/lib/api.ts";
import { autoUndone, undoAuto } from "./src/lib/todos.ts";

const calls: string[] = [];
globalThis.fetch = async (url, init) => {
  calls.push(`${init?.method} ${String(url)}`);
  return new Response(null, { status: 200 });
};
const mark = (at: string, judged: string): AutoMark => ({
  kind: "done",
  reason: "r",
  conv: "c1",
  at,
  sentence: `s-${at}`,
  judged_at: judged,
});
const first = mark("2026-11-03T10:00:01.000", "2026-11-03T11:00:00");
await undoAuto("c0:t1", first);
assert.deepEqual(calls, ["DELETE /api/todos/c0%3At1/auto"]);
assert.ok(autoUndone("c0:t1", first), "undone: hidden at once");
assert.ok(
  !autoUndone("c0:t1", mark("2026-11-05T10:00:01.000", "2026-11-05T11:00:00")),
  "closed again later: shown",
);
assert.ok(!autoUndone("c0:t2", { ...first }), "another todo closed by the same sentence: shown");
assert.ok(!autoUndone("c0:t1", null));
console.log("todos ok");
