// node --experimental-strip-types hideOnScroll.check.ts: the day picker's hide on scroll (src/lib/hideOnScroll.ts)
import assert from "node:assert/strict";
import { createRenderer, defineComponent, ref } from "vue";
import { useHideOnScroll } from "./src/lib/hideOnScroll.ts";

let onScroll = () => {};
const win = {
  scrollY: 0,
  innerHeight: 800,
  addEventListener: (_: string, fn: () => void) => (onScroll = fn),
  removeEventListener() {},
};
const page = { scrollHeight: 5000 }; // the bottom: scrollY 4200
Object.assign(globalThis, { window: win, innerHeight: 800, document: { documentElement: page } });
const row = { offsetTop: 4, offsetHeight: 36 }; // the picker in the sticky header
let pinned = true;
let hide!: ReturnType<typeof useHideOnScroll>;
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
        hide = useHideOnScroll(
          ref({ firstElementChild: row } as unknown as HTMLElement),
          () => pinned,
        );
        return () => null;
      },
    }),
  )
  .mount({});
const to = (y: number) => {
  win.scrollY = y;
  onScroll();
  return hide.lift.value;
};

assert.equal(to(20), 0, "near the top it shows");
assert.equal(to(300), 40, "down: up by the row's bottom");
assert.equal(to(302), 40, "a few px is jitter");
assert.equal(to(250), 0, "any scroll up shows it");
assert.equal(to(4200), 40, "down to the bottom");
assert.equal(to(4260), 40, "iOS bounce past the bottom is not a scroll up");
assert.equal(to(4200), 40);
hide.show();
page.scrollHeight = 5100; // a new caption, the live page follows it
assert.equal(to(4300), 0, "shown by a tap: kept while the page follows down");
assert.equal(to(4000), 0);
assert.equal(to(4100), 40, "until a scroll up");
assert.equal(to(10), 0);
pinned = false;
assert.equal(to(600), 0, "not pinned (a wide header, a phone's timeline bar): never");
