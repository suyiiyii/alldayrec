// node --experimental-strip-types headerFollow.check.ts: the day picker following the finger (src/lib/headerFollow.ts)
import assert from "node:assert/strict";
import { createRenderer, defineComponent } from "vue";
import { useHeaderFollow } from "./src/lib/headerFollow.ts";

const on: Record<string, () => void> = {};
const frames = new Map<number, (t: number) => void>();
let id = 0,
  now = 0;
const motion = { matches: false }; // prefers-reduced-motion: reduce
const win: Record<string, unknown> = {
  scrollY: 0,
  innerHeight: 800,
  addEventListener: (k: string, fn: () => void) => (on[k] = fn),
  removeEventListener() {},
  requestAnimationFrame: (fn: (t: number) => void) => (frames.set(++id, fn), id),
  cancelAnimationFrame: (n: number) => frames.delete(n),
  matchMedia: () => motion,
  setTimeout,
  clearTimeout,
};
const page = { scrollHeight: 5000 }; // the bottom: scrollY 4200
Object.assign(globalThis, { window: win, document: { documentElement: page } });
function tick() {
  now += 16;
  const due = [...frames];
  frames.clear();
  for (const [, fn] of due) fn(now);
}
const run = () => {
  for (let i = 0; i < 100 && frames.size; i++) tick();
};

let size = 40,
  start = 40, // from(): the live page's, its own size (shown in the first 40 px)
  hidden = -1,
  past = 0;
let bar!: ReturnType<typeof useHeaderFollow>;
createRenderer<object, object>({
  patchProp() {},
  insert() {},
  remove() {},
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  setText() {},
  setElementText() {},
  parentNode: () => null,
  nextSibling: () => null,
})
  .createApp(
    defineComponent({
      setup() {
        bar = useHeaderFollow(
          () => size,
          () => start,
          (h, p) => ((hidden = h), (past = p)),
        );
        return () => null;
      },
    }),
  )
  .mount({});
const to = (y: number) => {
  win.scrollY = y;
  on.scroll();
  tick();
  return hidden;
};
const rest = () => {
  on.scrollend();
  run();
  return hidden;
};

assert.equal(hidden, 0, "painted shown");
assert.equal(to(30), 0, "near the top it stays");
assert.equal(to(60), 20, "past from(): up as far as the page went");
assert.equal(past, 20);
assert.equal(to(100), 40, "no further than its size");
assert.equal(to(90), 30, "up 10: back 10");
on.scrollend();
tick();
tick();
assert.ok(hidden > 30 && hidden < 40, "settles over frames, not at once");
run();
assert.equal(hidden, 40, "at rest more than half gone: all of it");
assert.equal(to(60), 10);
assert.equal(rest(), 0, "at rest more than half showing: all of it");
assert.equal(to(0), 0);
assert.equal(to(75), 35, "past 35");
assert.equal(rest(), 0, "at rest within its size of from(): shown, not 35 px up");

assert.equal(to(4200), 40, "down to the bottom");
page.scrollHeight = 4980; // a caption redone shorter: the browser pulls scrollY back
assert.equal(to(4180), 40, "the page shrinking at the bottom is not a scroll up");
page.scrollHeight = 5000;
assert.equal(to(4200), 40);
assert.equal(to(4260), 40, "iOS bounce past the bottom");
assert.equal(to(4200), 40, "and back from it is not a scroll up");
assert.equal(to(4180), 20);
assert.equal(rest(), 0);
assert.equal(to(4200), 20);
assert.equal(rest(), 0, "half: shown");
assert.equal(to(4260), 0, "past the bottom is no scroll down either");
assert.equal(to(4200), 0);
assert.equal(to(-30), 0, "past the top: shown");
assert.equal(to(4200), 40);

assert.equal(to(4170), 10);
on.scrollend();
tick();
tick();
const mid = hidden;
assert.ok(mid > 0 && mid < 10);
const after = to(4180);
assert.ok(
  after > 10 && after <= mid + 10,
  "a scroll while it settles goes on from where it got to",
);
run();
assert.equal(hidden, after, "and stops the settling");
assert.equal(rest(), 0);
assert.equal(to(4200), 20);
assert.equal(rest(), 0, "half: shown");
assert.equal(to(4150), 0);
assert.equal(to(4200), 40);

assert.equal(bar.show(), true, "a tap brings it back");
run();
assert.equal(hidden, 0);
assert.equal(bar.show(), false, "shown already: the tap is the link's");
page.scrollHeight = 5100; // a new caption, the live page follows it
assert.equal(to(4300), 0, "shown by a tap: kept while the page follows down");
assert.equal(to(4290), 0, "until a scroll up");
assert.equal(to(4300), 10, "then it follows again");

motion.matches = true;
assert.equal(to(4300), 10);
on.scrollend();
assert.equal(hidden, 0, "reduced motion: at once");
motion.matches = false;

size = 0;
assert.equal(to(1000), 0, "size 0 (a wide header): never");
size = 40;

// 120 ms without a scroll (iOS has no scrollend), and not under a finger
to(900);
assert.equal(to(1000), 40);
assert.equal(to(990), 30);
await new Promise((r) => setTimeout(r, 150));
run();
assert.equal(hidden, 40, "settled 120 ms after the last scroll");
on.touchstart();
assert.equal(to(980), 30);
on.scrollend(); // Chrome, the page's end reached under the finger
run();
await new Promise((r) => setTimeout(r, 150));
run();
assert.equal(hidden, 30, "a finger holding the page: no settling under it");
on.touchend();
await new Promise((r) => setTimeout(r, 150));
run();
assert.equal(hidden, 40, "settled 120 ms after the finger left");
