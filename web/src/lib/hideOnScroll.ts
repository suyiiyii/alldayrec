import { onMounted, onUnmounted, ref, type Ref } from "vue";

// The day picker as a browser's toolbar: scrolling down the page (scrollY growing) past EDGE takes it away over the top
// edge, any scroll back up brings it back, near the top it always shows. box: the element whose first row it is, inside
// the sticky one; on: whether that row is pinned now. lift: how far the sticky one goes up (0: shown), the row's bottom.
// show(): back at once and kept until the next scroll up (the live page following the captions scrolls down by itself).
// The box moves, the layout does not: a collapsing height would shift the page under the reader and, at its bottom
// (where the live page follows the captions), clamp scrollY back, which reads as a scroll up and shows it again.
const EDGE = 24;
export function useHideOnScroll(box: Ref<HTMLElement | null>, on: () => boolean) {
  const lift = ref(0);
  let last = 0,
    held = false;
  function onScroll() {
    const y = window.scrollY;
    // iOS bouncing back from past the bottom is no scroll up; a few px either way is jitter
    if (
      y >= EDGE &&
      (y > document.documentElement.scrollHeight - innerHeight || Math.abs(y - last) < 4)
    )
      return;
    if (y < last || y < EDGE) held = false;
    const row = box.value?.firstElementChild as HTMLElement | null | undefined; // offsetTop: in the sticky one
    lift.value =
      y >= EDGE && y > last && !held && row && on() ? row.offsetTop + row.offsetHeight : 0;
    last = y;
  }
  onMounted(() => window.addEventListener("scroll", onScroll, { passive: true }));
  onUnmounted(() => window.removeEventListener("scroll", onScroll));
  return {
    lift,
    show() {
      lift.value = 0;
      held = true;
    },
  };
}
