<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { layout, stopAt } from "../lib/scrub";

// A fast-scroll rail like a phone gallery's, in the gutter right of its parent (the list): one stop per conversation (the
// live page) or per sentence (a conversation's 原文), each taking rail by its weight (lib/scrub.ts). A thin line until
// the reader scrolls by hand or a pointer comes near; then a handle where the page is, to drag (or click, or step with
// the arrow keys) to any stop, a label beside it saying where. It reaches from under what avoid names that covers its
// top (the header, a stuck summary card) to above what covers its bottom (the tab bar, the player); hidden on a page
// shorter than two screens. go: the page brings the stop's row (its data-start) to the top.
export interface Stop {
  start: string;
  w: number;
  group: number; // the stops of one group draw as one segment
  time: string;
  title?: string;
  sub?: string;
}
const props = defineProps<{ stops: Stop[]; label: string; avoid: string; go: (s: Stop) => void }>();

const cum = computed(() => layout(props.stops.map((s) => s.w)));
const segs = computed(() => {
  const out: { i: number; top: number; h: number }[] = [];
  props.stops.forEach((s, i) => {
    const last = out.at(-1);
    if (last && props.stops[last.i].group === s.group) last.h = cum.value[i + 1] - last.top;
    else out.push({ i, top: cum.value[i], h: cum.value[i + 1] - cum.value[i] });
  });
  return out;
});

const root = ref<HTMLElement | null>(null);
const box = ref({ x: 0, w: 0, top: 0, h: 0, long: false }); // where the rail is, in the viewport
const cur = ref(0), // the stop the page is at (dragging: the finger's)
  fingerY = ref(0),
  awake = ref(false),
  dragging = ref(false),
  hover = ref(false),
  keyed = ref(false); // stepped with the keys: the label shows until focus leaves
const curSeg = computed(() => {
  let k = 0;
  while (k + 1 < segs.value.length && segs.value[k + 1].i <= cur.value) k++;
  return segs.value[k];
});
const thumbY = computed(() =>
  dragging.value ? fingerY.value - box.value.top : cum.value[cur.value] * box.value.h,
);
const tip = computed(() => (dragging.value || keyed.value ? props.stops[cur.value] : undefined));

// the stops' rows, looked up once (thousands of sentences: one query each per frame would be a lot)
let els = new Map<string, Element | null>();
watch(
  () => props.stops,
  () => {
    els = new Map();
    schedule();
  },
);
function elOf(start: string) {
  let el = els.get(start);
  if (!el?.isConnected)
    els.set(start, (el = document.querySelector(`[data-start="${CSS.escape(start)}"]`)));
  return el;
}

function measure() {
  const p = root.value?.parentElement;
  if (!p) return;
  const r = p.getBoundingClientRect(),
    vw = document.documentElement.clientWidth,
    w = Math.min(24, vw - r.right),
    cx = r.right + w / 2;
  let top = 0,
    bottom = innerHeight;
  for (const el of document.querySelectorAll(props.avoid)) {
    const e = el.getBoundingClientRect();
    if (!e.height || e.left > cx || e.right < cx) continue;
    if (e.top < innerHeight / 2) top = Math.max(top, e.bottom);
    else bottom = Math.min(bottom, e.top);
  }
  top += 8;
  box.value = {
    x: r.right,
    w,
    top,
    h: bottom - 8 - top,
    long: document.documentElement.scrollHeight > 2 * innerHeight,
  };
}
// the last stop whose row starts above the reading line, a third down the rail: a binary search, rows in order
function current() {
  const line = box.value.top + box.value.h / 3;
  let lo = 0,
    hi = props.stops.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    const el = elOf(props.stops[mid].start);
    if (!el || el.getBoundingClientRect().top <= line) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

// at most once a frame, whatever fires: a drag jumps the page, a scroll moves the handle (and the rail, as the header
// comes and goes)
let raf = 0,
  went = -1;
const schedule = () => void (raf ||= requestAnimationFrame(frame));
// and once more after the page rests: the live page's header settles by itself then (lib/headerFollow.ts, 120 + 180 ms)
let rest: ReturnType<typeof setTimeout> | undefined;
function scrolled() {
  schedule();
  clearTimeout(rest);
  rest = setTimeout(schedule, 400);
}
function frame() {
  raf = 0;
  if (dragging.value) {
    const i = stopAt(cum.value, (fingerY.value - box.value.top) / box.value.h);
    cur.value = i;
    if (i !== went) props.go(props.stops[(went = i)]);
  } else {
    measure();
    if (!keyed.value && props.stops.length) cur.value = current();
  }
}

let sleep: ReturnType<typeof setTimeout> | undefined;
function wake() {
  awake.value = true;
  clearTimeout(sleep);
  sleep = setTimeout(() => (awake.value = dragging.value || hover.value || keyed.value), 1500);
}

function down(e: PointerEvent) {
  if (e.button > 0) return;
  (e.currentTarget as Element).setPointerCapture(e.pointerId);
  measure(); // then held while dragging: a header coming back as the page jumps moves no stop under the finger
  dragging.value = true;
  keyed.value = false;
  went = -1;
  move(e);
}
function move(e: PointerEvent) {
  if (!dragging.value) return;
  fingerY.value = Math.max(box.value.top, Math.min(e.clientY, box.value.top + box.value.h));
  wake();
  schedule();
}
function up() {
  dragging.value = false;
  wake();
  scrolled();
}
function key(e: KeyboardEvent) {
  const n = props.stops.length,
    page = Math.max(1, Math.round(n / 10));
  const to = {
    ArrowDown: cur.value + 1,
    ArrowRight: cur.value + 1,
    ArrowUp: cur.value - 1,
    ArrowLeft: cur.value - 1,
    PageDown: cur.value + page,
    PageUp: cur.value - page,
    Home: 0,
    End: n - 1,
  }[e.key];
  if (to === undefined) return;
  e.preventDefault();
  cur.value = Math.max(0, Math.min(n - 1, to));
  keyed.value = true;
  wake();
  props.go(props.stops[cur.value]);
}
function blur() {
  keyed.value = false;
  wake();
}
function near(on: boolean, e: PointerEvent) {
  if (e.pointerType !== "mouse") return;
  hover.value = on;
  wake();
}

let sizer: ResizeObserver | undefined;
onMounted(() => {
  addEventListener("scroll", scrolled, { passive: true });
  addEventListener("resize", schedule);
  // a scroll by hand wakes it; the live page following new captions does not
  addEventListener("wheel", wake, { passive: true });
  addEventListener("touchmove", wake, { passive: true });
  const p = root.value?.parentElement;
  if (p) (sizer = new ResizeObserver(schedule)).observe(p);
  schedule();
});
onUnmounted(() => {
  removeEventListener("scroll", scrolled);
  removeEventListener("resize", schedule);
  removeEventListener("wheel", wake);
  removeEventListener("touchmove", wake);
  sizer?.disconnect();
  cancelAnimationFrame(raf);
  clearTimeout(sleep);
  clearTimeout(rest);
});
</script>

<template>
  <div
    v-show="stops.length > 1 && box.long && box.h >= 120"
    ref="root"
    class="scrub"
    :class="{ awake: awake || dragging, dragging }"
    :style="{ left: box.x + 'px', top: box.top + 'px', width: box.w + 'px', height: box.h + 'px' }"
    role="slider"
    tabindex="0"
    aria-orientation="vertical"
    :aria-label="label"
    aria-valuemin="0"
    :aria-valuemax="stops.length - 1"
    :aria-valuenow="cur"
    :aria-valuetext="[stops[cur]?.time, stops[cur]?.title].filter(Boolean).join(' ')"
    @pointerdown="down"
    @pointermove="move"
    @pointerup="up"
    @pointercancel="up"
    @lostpointercapture="up"
    @pointerenter="near(true, $event)"
    @pointerleave="near(false, $event)"
    @keydown="key"
    @focus="wake"
    @blur="blur"
  >
    <div class="line">
      <span
        v-for="s in segs"
        :key="s.i"
        :style="{ top: s.top * 100 + '%', height: `max(1px, calc(${s.h * 100}% - 1px))` }"
      ></span>
      <span
        v-if="curSeg"
        class="on"
        :style="{ top: curSeg.top * 100 + '%', height: `max(2px, calc(${curSeg.h * 100}% - 1px))` }"
      ></span>
    </div>
    <div class="thumb" :style="{ transform: `translateY(${thumbY}px)` }"></div>
    <div v-if="tip" class="tip" :style="{ top: thumbY + 'px' }" aria-hidden="true">
      <b>{{ tip.time }}</b>
      <span v-if="tip.title" class="title">{{ tip.title }}</span>
      <span v-if="tip.sub" class="sub">{{ tip.sub }}</span>
    </div>
  </div>
</template>

<style scoped>
.scrub {
  position: fixed;
  z-index: 2;
  touch-action: none; /* a drag here moves the rail, not the page */
  user-select: none;
  -webkit-user-select: none;
  cursor: pointer;
  outline: none;
}
.line {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 50%;
  width: 3px;
  transform: translateX(50%);
  transition: width 0.15s;
}
.line span {
  position: absolute;
  left: 0;
  right: 0;
  border-radius: 2px;
  background: var(--muted);
  opacity: 0.3;
}
.line .on {
  background: var(--accent);
  opacity: 0;
}
.awake .line,
.scrub:focus-visible .line {
  width: 6px;
}
.awake .line span {
  opacity: 0.45;
}
.awake .line .on,
.scrub:focus-visible .line .on {
  opacity: 1;
}
.scrub:focus-visible .line {
  box-shadow: 0 0 0 2px var(--accent);
  border-radius: 3px;
}
/* the handle: shown awake, over the captions' edge for a finger to take; its middle where the page is */
.thumb {
  position: absolute;
  right: 0;
  top: -20px;
  width: 22px;
  height: 40px;
  border-radius: 11px;
  background: var(--surface);
  box-shadow: 0 1px 6px rgb(0 0 0 / 0.25);
  border: 1px solid var(--line);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s;
}
.thumb::before,
.thumb::after {
  content: "";
  position: absolute;
  left: 6px;
  right: 6px;
  height: 2px;
  border-radius: 1px;
  background: var(--muted);
  top: 15px;
}
.thumb::after {
  top: 21px;
}
.awake .thumb {
  opacity: 1;
  pointer-events: auto;
}
.tip {
  position: absolute;
  right: calc(100% + 16px);
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: max-content;
  max-width: min(70vw, 320px);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--fg);
  box-shadow: 0 4px 16px rgb(0 0 0 / 0.18);
  font-size: var(--fs-sm);
  pointer-events: none;
}
.tip b {
  color: var(--accent);
}
.tip .title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tip .sub {
  color: var(--muted);
  font-size: var(--fs-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (prefers-reduced-motion: reduce) {
  .line,
  .thumb {
    transition: none;
  }
}
</style>
