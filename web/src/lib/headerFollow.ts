import { onMounted, onUnmounted } from "vue";

// The day picker over the top of the page follows the finger, as a phone's own toolbars do: each scroll moves it as far
// as the page moved, pixel for pixel (down: up out of sight, up: back). hidden: how far it is up, 0..size(). from(): the
// scrollY where it may start to go; it goes no further than the page has scrolled past that (past), so near the top it
// stays. Once the page rests (scrollend, or 120 ms without a scroll: iOS has no scrollend; not under a finger) it settles
// fully shown if more than half of it shows or it is within its size of from(), else fully hidden: 180 ms ease-out, at
// once with prefers-reduced-motion. paint(hidden, past) puts it there with a transform (no layout: a changing height
// would move the page and, where the live page follows the captions, pull scrollY back), once a frame at most.
// show(): back now and kept while the page scrolls down by itself (the live page following the captions) until the next
// scroll up; false if it was not hidden. repaint(): paint again where it is (something to move with it came up).
const REST_MS = 120,
  SETTLE_MS = 180;
export function useHeaderFollow(
  size: () => number,
  from: () => number,
  paint: (hidden: number, past: number) => void,
) {
  let hidden = 0,
    y = 0,
    held = false,
    touching = false,
    frame = 0,
    anim = 0,
    timer = 0;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const bottom = () => document.documentElement.scrollHeight - window.innerHeight;
  const scrolled = () => Math.max(0, Math.min(window.scrollY, bottom())); // iOS bounces past both ends: no scroll
  function put(h: number) {
    const past = y - from();
    hidden = Math.min(Math.max(h, 0), size(), Math.max(past, 0));
    paint(hidden, past);
  }
  function follow() {
    frame = 0;
    const now = scrolled();
    // up but still at the bottom: the page got shorter under it (a caption redone shorter), no finger moved
    const dy = now < y && now >= bottom() - 1 ? 0 : now - y;
    y = now;
    if (dy < 0) held = false;
    if (!dy || held) return put(hidden);
    window.cancelAnimationFrame(anim);
    put(hidden + dy);
  }
  function glide(to: number) {
    window.cancelAnimationFrame(anim);
    const a = hidden;
    if (a === to || reduce.matches) return put(to);
    let t0 = 0;
    const tick = (t: number) => {
      t0 ||= t;
      const k = Math.min(1, (t - t0) / SETTLE_MS);
      put(a + (to - a) * (1 - (1 - k) ** 3));
      anim = k < 1 ? window.requestAnimationFrame(tick) : 0;
    };
    anim = window.requestAnimationFrame(tick);
  }
  function settle() {
    window.clearTimeout(timer);
    if (frame) {
      window.cancelAnimationFrame(frame);
      follow();
    }
    if (touching || held) return;
    const h = size();
    glide(hidden > h / 2 && y - from() >= h ? h : 0);
  }
  const rest = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(settle, REST_MS);
  };
  const onScroll = () => {
    frame ||= window.requestAnimationFrame(follow);
    rest();
  };
  const down = () => (touching = true);
  const up = () => {
    touching = false;
    rest();
  };
  const on = { passive: true };
  onMounted(() => {
    y = scrolled();
    put(0);
    window.addEventListener("scroll", onScroll, on);
    window.addEventListener("scrollend", settle);
    window.addEventListener("touchstart", down, on);
    window.addEventListener("touchend", up, on);
    window.addEventListener("touchcancel", up, on);
  });
  onUnmounted(() => {
    window.removeEventListener("scroll", onScroll);
    window.removeEventListener("scrollend", settle);
    window.removeEventListener("touchstart", down);
    window.removeEventListener("touchend", up);
    window.removeEventListener("touchcancel", up);
    window.cancelAnimationFrame(frame);
    window.cancelAnimationFrame(anim);
    window.clearTimeout(timer);
  });
  return {
    show() {
      if (!hidden) return false;
      held = true;
      glide(0);
      return true;
    },
    repaint: () => put(hidden),
  };
}
