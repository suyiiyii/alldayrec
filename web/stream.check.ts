// node --experimental-strip-types web/stream.check.ts
import { readFileSync } from "node:fs";
import { RecordStream, bootRate, frameAt } from "./src/lib/stream.ts";

const fixture = JSON.parse(readFileSync(new URL("../fixtures/record-stream.json", import.meta.url), "utf8"));
const v = fixture.mirror;
const bytes = Uint8Array.from(Buffer.from(v.hex, "hex"));
const pieces: [number, number][] = [];
const check = (ok: boolean, what: string) => { if (!ok) throw new Error(what + ": " + JSON.stringify(pieces)); };
const s = new RecordStream((frames, start) => pieces.push([start, frames.length]), v.liveFrames);
s.follow(v.file, 0);
for (const d of v.deliveries) {
  check(s.put(v.file, d.start, bytes.subarray(d.start, d.end)) === d.hole, "shared delivery hole");
}
check(JSON.stringify(pieces) === JSON.stringify(v.pieces.slice(0, -1)), "shared dated open runs");
s.cut();
check(JSON.stringify(pieces) === JSON.stringify(v.pieces), "shared final run");
// Record.vue follows the open file from LIST's size, a record boundary: here 36, the second 'A' record (frames
// 10–19). LIVE bytes past the boundary arrive first and leave a hole there; READ from it fills the rest.
const at = 36;
pieces.length = 0;
const late = new RecordStream((frames, start) => pieces.push([start, frames.length]), v.liveFrames);
late.follow(v.file, at);
check(late.put(v.file, 100, bytes.subarray(100)) === at, "late follow hole");
check(late.put(v.file, at, bytes.subarray(at, 100)) === null && late.end() === bytes.length, "late follow filled");
late.cut();
// frames 10–29 are one run, dated back from the 'T' at frame 20; 40–49 come after the lost frames 30–39
check(JSON.stringify(pieces) === JSON.stringify([[999900, 20], [1000200, 10]]), "late follow runs");
// a board's frame clock ~74 ppm fast: the boot's rate learned from its 'T' pairs (late, another boot's, too close
// ones left out), the file's run dated by it; too few pairs: 10 ms a frame
const rate = fixture.rate;
for (const c of rate.cases) {
  check(Math.abs(bootRate(c.syncs) - c.rate) < 1e-12, `${c.what}: rate ${bootRate(c.syncs)}`);
  for (const [q, t] of c.at) check(frameAt(c.syncs, q) === t, `${c.what}: at ${q} ${frameAt(c.syncs, q)}`);
}
pieces.length = 0;
const fast = new RecordStream((frames, start) => pieces.push([start, frames.length]), v.liveFrames);
fast.follow(rate.file, 0);
fast.put(rate.file, 0, Uint8Array.from(Buffer.from(rate.hex, "hex")));
fast.cut();
check(JSON.stringify(pieces) === JSON.stringify(rate.runs), "run dated at the boot's rate");
// RecordStream has no persistent mirror, undated archive or boot-reuse API (see fixture.notes).
console.log("stream check ok");
