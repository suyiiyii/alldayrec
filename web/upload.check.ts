// node --experimental-strip-types upload.check.ts: what /record does with an upload's answer (src/lib/upload.ts, #328)
import assert from "node:assert/strict";
import { verdict } from "./src/lib/upload.ts";

assert.equal(verdict(200, { seconds: 1 }), "ok");
// only the server's own "not audio" drops the piece
assert.equal(verdict(400, { detail: "could not decode" }), "bad");
assert.equal(verdict(400, null), "refused", "a proxy's 400 (HTML, no JSON): kept");
assert.equal(verdict(400, { detail: "something else" }), "refused");
assert.equal(verdict(422, { detail: [{ msg: "field required" }] }), "refused");
assert.equal(verdict(404, null), "refused");
assert.equal(verdict(413, null), "refused");
assert.equal(verdict(401, { detail: "could not decode" }), "auth");
assert.equal(verdict(403, null), "auth");
for (const s of [408, 429, 500, 502, 503, 504]) assert.equal(verdict(s, null), "retry", String(s));
console.log("upload ok");
