import { reactive } from "vue";
import type { Row } from "./api";
import { WINDOW, windowAsk } from "./track";

// one sound at a time, on every page: a clip (play) stops the conversation player (track), and it a clip
export const player = reactive({ playing: "", failed: new Set<string>() });
let audio: HTMLAudioElement | null = null;

export function play(id: string, src: string) {
  audio?.pause();
  trackPause();
  if (player.playing === id) {
    player.playing = "";
    return;
  }
  audio = new Audio(src);
  player.playing = id;
  audio.onended = () => (player.playing = "");
  audio.onerror = () => {
    player.failed.add(id);
    player.playing = "";
  };
  void audio.play();
}

export const toggle = (r: Row) =>
  play(
    r.id,
    `/api/audio?${new URLSearchParams({ start: r.start, end: r.end, ...(r.source && { device: r.source }) })}`,
  );

// The conversation page's player: [from, to] of one device (unix ms) as back-to-back /api/audio windows
// (track.ts), each fetched whole (the next while one plays) and played by one <audio>: iOS lets an element play on
// by itself, as the next window after `ended`, only once a tap has started it. at: where it is; follow: the page
// keeps the sentence playing in view (a scroll by hand turns it off, the next seek on)
export const track = reactive({
  from: 0,
  to: 0,
  device: "",
  at: 0,
  playing: false,
  rate: 1,
  failed: false,
  follow: true,
});
// ponytail: a window whose recording starts late (or has gaps between pieces, which the server joins back to back)
// plays early by that much; ask the server for the WAV's own start if that ever shows
const SILENT =
  "data:audio/wav;base64,UklGRkQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YSAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==";
let el: HTMLAudioElement | null = null,
  unlocked = false,
  k = -1, // the window in el
  gen = 0, // the latest seek: a window fetched for an older one is dropped
  miss = 0; // windows in a row without a recording
const got = new Map<number, Promise<string | null>>(); // window → object URL of its WAV; null: none there

function element() {
  if (el) return el;
  const a = (el = new Audio());
  a.ontimeupdate = () => {
    if (k >= 0) track.at = Math.min(track.to, track.from + k * WINDOW + a.currentTime * 1000);
  };
  a.onended = () => k >= 0 && void load(k + 1, 0, gen);
  // paused or played from outside (the lock screen, a call, headphones out); not the pause at a window's end, nor
  // one while the next window is on its way (k -1) or loading
  a.onpause = () => {
    if (k >= 0 && !a.ended && a.readyState > 1) track.playing = false;
  };
  a.onplay = () => (track.playing = true);
  return a;
}

function fetchWindow(w: number) {
  let p = got.get(w);
  if (!p) {
    const ask = windowAsk(track.from, track.to, w);
    const q = ask && { start: ask[0], end: ask[1], ...(track.device && { device: track.device }) };
    p = !q
      ? Promise.resolve(null)
      : fetch(`/api/audio?${new URLSearchParams(q)}`)
          .then((r) => (r.ok ? r.blob() : null))
          .then((b) => b && URL.createObjectURL(b))
          .catch(() => null);
    got.set(w, p);
  }
  return p;
}

function drop(keep: (w: number) => boolean) {
  for (const [w, p] of got)
    if (!keep(w)) {
      got.delete(w);
      void p.then((u) => u && URL.revokeObjectURL(u));
    }
}

async function load(w: number, offset: number, my: number) {
  const a = element();
  drop((j) => j === w || j === w + 1);
  if (!windowAsk(track.from, track.to, w)) {
    // the end
    track.playing = false;
    track.at = track.to;
    return;
  }
  const u = await fetchWindow(w);
  if (my !== gen) return;
  if (!u) {
    // no recording covers this window (404) or it did not come: on to the next, three such in a row is the end
    if (++miss < 3) return load(w + 1, 0, my);
    miss = 0;
    track.playing = false;
    track.failed = true;
    return;
  }
  miss = 0;
  track.failed = false;
  k = w;
  a.src = u;
  // set once it knows its length: Safari ignores a position set before
  if (offset) a.addEventListener("loadedmetadata", () => my === gen && (a.currentTime = offset / 1000), { once: true });
  a.playbackRate = a.defaultPlaybackRate = track.rate; // a new src resets the rate to the default
  if (track.playing) await a.play().catch(() => (track.playing = false));
  void fetchWindow(w + 1);
}

// The player of a conversation's span; the same span again (the page reloaded it) keeps where it is, an open
// conversation's growing end too
export function trackOpen(from: number, to: number, device: string) {
  if (from === track.from && device === track.device) {
    track.to = to;
    return;
  }
  trackClose();
  Object.assign(track, { from, to, device, at: from, failed: false, follow: true });
}

export function trackClose() {
  trackPause();
  gen++;
  k = -1;
  drop(() => false);
  el?.removeAttribute("src");
  el?.load();
  Object.assign(track, { from: 0, to: 0, device: "" });
}

export function trackPause() {
  track.playing = false;
  el?.pause();
}

// To ms, and play from there (or only stand there, paused). Called in a tap: that is where iOS lets it start
export function trackSeek(ms: number, start: boolean) {
  ms = Math.max(track.from, Math.min(ms, track.to - 500));
  audio?.pause();
  player.playing = "";
  track.at = ms;
  track.follow = true;
  const a = element(),
    w = Math.floor((ms - track.from) / WINDOW),
    offset = ms - track.from - w * WINDOW;
  if (!start) {
    trackPause();
    gen++; // a window on its way plays nothing
    if (w === k) a.currentTime = offset / 1000;
    else k = -1;
    return;
  }
  track.playing = true;
  if (w === k) {
    a.currentTime = offset / 1000;
    void a.play().catch(() => (track.playing = false));
    return;
  }
  k = -1; // nothing of the old window plays or moves `at` while the new one comes
  a.pause();
  if (!unlocked) {
    unlocked = true;
    a.src = SILENT;
    void a.play().catch(() => {});
  }
  void load(w, offset, ++gen);
}

export function trackToggle() {
  if (track.playing) trackPause();
  else trackSeek(track.at >= track.to - 1000 ? track.from : track.at, true);
}

const RATES = [1, 1.5, 2];
export function trackRate() {
  track.rate = RATES[(RATES.indexOf(track.rate) + 1) % RATES.length];
  if (el) el.playbackRate = el.defaultPlaybackRate = track.rate;
}
