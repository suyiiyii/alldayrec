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

// the live page: the closed conversation a row is in, as its convOf says (its device's, or one with no device; start ≤
// the row's start ≤ end), by a binary search per device: a day's thousands of rows against hundreds of conversations,
// again at every new caption
export function ownerOf<C extends { start: string; end: string; device?: string | null }>(
  convs: C[],
) {
  const by = new Map<string, C[]>();
  for (const c of convs) {
    const l = by.get(c.device ?? "");
    if (l) l.push(c);
    else by.set(c.device ?? "", [c]);
  }
  for (const l of by.values())
    l.sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0));
  const of = (l: C[] | undefined, at: string) => {
    let lo = 0,
      hi = l?.length ?? 0;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (l![mid].start <= at) lo = mid + 1;
      else hi = mid;
    }
    const c = l?.[lo - 1];
    return c && at <= c.end ? c : undefined;
  };
  return (r: { start: string; source?: string }) =>
    (r.source ? of(by.get(r.source), r.start) : undefined) ?? of(by.get(""), r.start);
}
