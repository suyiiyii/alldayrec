// node --experimental-strip-types web/pcm.check.ts: the microphone to the voice socket's PCM (src/lib/pcm.ts): 48 and
// 44.1 kHz to 16 kHz keep the length and the level across any split into calls; blocks of 0.1 s, the rest on flush.
import assert from "node:assert/strict";
import { BLOCK, RATE, blocks, resampler } from "./src/lib/pcm.ts";

const sine = (rate: number, s: number, f = 440, a = 0.5) =>
  Float32Array.from({ length: rate * s }, (_, i) => a * Math.sin((2 * Math.PI * f * i) / rate));
const rms = (x: Int16Array) => Math.sqrt(x.reduce((s, v) => s + v * v, 0) / x.length) / 32767;

for (const rate of [48000, 44100]) {
  const x = sine(rate, 2);
  for (const cut of [128, 2048, 4096, 1000]) {
    const r = resampler(rate),
      parts: Int16Array[] = [];
    for (let i = 0; i < x.length; i += cut) parts.push(r(x.subarray(i, i + cut)));
    const y = Int16Array.from(parts.flatMap((p) => [...p]));
    assert.ok(
      Math.abs(y.length - 2 * RATE) <= 1,
      `${rate} in ${cut}s: ${y.length} samples for 2 s`,
    );
    assert.ok(
      Math.abs(rms(y) - 0.5 / Math.SQRT2) < 0.01,
      `${rate}: a 440 Hz tone keeps its level (${rms(y)})`,
    );
  }
}
const loud = resampler(48000)(new Float32Array(48).fill(1.5));
assert.deepEqual([...new Set(loud)], [32767], "clipped, not wrapped");
const hiss = rms(resampler(48000)(sine(48000, 1, 15000))); // above 8 kHz: aliases into speech unless it is filtered
assert.ok(hiss < 0.5 / Math.SQRT2 / 2, `15 kHz comes down at least 6 dB (${hiss})`);

const sent: Int16Array[] = [],
  b = blocks((x) => sent.push(x));
b.push(new Int16Array(BLOCK - 1).fill(1));
assert.equal(sent.length, 0);
b.push(new Int16Array(BLOCK * 2 + 5).fill(2));
assert.deepEqual(
  sent.map((x) => x.length),
  [BLOCK, BLOCK, BLOCK],
);
assert.equal(sent[0][BLOCK - 1], 2, "carried over into the next block");
b.flush();
assert.deepEqual(
  sent.map((x) => x.length),
  [BLOCK, BLOCK, BLOCK, 4],
);
b.flush();
assert.equal(sent.length, 4, "nothing left: nothing sent");
console.log("pcm ok");
