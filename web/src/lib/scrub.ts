// The fast-scroll rail's arithmetic (components/Scrubber.vue draws it). No Vue here: scrub.check.ts runs it under
// plain node. A rail is a list of stops, top to bottom, each taking a share of its height by weight.

// where each stop begins on the rail, 0..1, and 1 at the end (n + 1 numbers)
export function layout(ws: number[]) {
  const all = ws.reduce((a, b) => a + b, 0) || 1,
    out = [0];
  for (const w of ws) out.push(out[out.length - 1] + w / all);
  out[out.length - 1] = 1;
  return out;
}

// the stop at f (0..1) on a layout: the last one beginning at or before it
export function stopAt(cum: number[], f: number) {
  let lo = 0,
    hi = cum.length - 1; // the stops: cum has one more
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (cum[mid] <= f) lo = mid;
    else hi = mid;
  }
  return lo;
}

// the live page: a conversation's share by its sentences, a quarter of the mean added so a two-line chat among hundreds
// still takes enough rail to land on
export function convWeights(sentences: number[]) {
  const mean = sentences.reduce((a, b) => a + b, 0) / (sentences.length || 1);
  return sentences.map((n) => Math.max(1, n) + mean / 4);
}

// the conversation page: a stop's share by its time until the next one starts (the last: until end), so the rail reads
// as a clock; at least a second each
export function timeWeights(starts: number[], end: number) {
  return starts.map((s, i) => Math.max(1000, (starts[i + 1] ?? end) - s));
}
