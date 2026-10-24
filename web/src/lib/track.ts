// The conversation player's arithmetic (lib/audio.ts plays it). No Vue here: track.check.ts runs it under plain
// node. Times are unix ms; the API's are local wall-clock strings without a zone, parsed with Date.parse
export const WINDOW = 110_000; // one /api/audio request: the server cuts at most 120 s (routes_audio.py cut())
// The windows from where play starts: a short first one so sound comes within a second (a 110 s WAV is 3.5 MB,
// seconds on 4G), a middle one that can arrive while it plays, then full ones
const FIRST = [10_000, 30_000];

// where window k begins, in ms after the anchor (where play started)
export const windowStart = (k: number) =>
  k < FIRST.length ? FIRST.slice(0, k).reduce((a, b) => a + b, 0) : 40_000 + (k - FIRST.length) * WINDOW;
export const windowLen = (k: number) => (k < FIRST.length ? FIRST[k] : WINDOW);

// a time as the API writes it: local, no zone, to the ms (2026-10-24T09:30:12.200)
export const isoLocal = (ms: number) =>
  new Date(ms - new Date(ms).getTimezoneOffset() * 60_000).toISOString().slice(0, 23);

// Window k of [from, to] (from: the anchor) as the start and end to ask /api/audio for: 0.2 s inside each end,
// because the server adds 0.2 s on each side, so the WAV that comes back is the window exactly and back-to-back
// windows neither overlap nor leave a gap. null past the end (or for a tail too short to ask for)
export function windowAsk(from: number, to: number, k: number): [string, string] | null {
  const a = from + windowStart(k),
    b = Math.min(a + windowLen(k), to);
  return b - a > 400 ? [isoLocal(a + 200), isoLocal(b - 200)] : null;
}

// the last of the rows (their starts, in order) started by ms: its index, -1 before the first
export function rowAt(starts: number[], ms: number) {
  let lo = 0,
    hi = starts.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (starts[mid] <= ms) lo = mid + 1;
    else hi = mid;
  }
  return lo - 1;
}

// each speaker's part of the speech (the rows' end − start), largest first
export function talkShare(rows: { speaker?: string; start: string; end: string }[]) {
  const by = new Map<string, number>();
  for (const r of rows)
    if (r.speaker) by.set(r.speaker, (by.get(r.speaker) ?? 0) + Date.parse(r.end) - Date.parse(r.start));
  const all = [...by.values()].reduce((a, b) => a + b, 0);
  return [...by]
    .map(([who, ms]) => ({ who, share: all ? ms / all : 0 }))
    .sort((a, b) => b.share - a.share);
}

// 83 s as 1:23, an hour and more as 1:02:03
export function clock(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000)),
    mm = String(Math.floor(s / 60) % 60),
    ss = String(s % 60).padStart(2, "0");
  return s >= 3600 ? `${Math.floor(s / 3600)}:${mm.padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}
