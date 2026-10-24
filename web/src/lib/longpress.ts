// A long press (a finger held LONG_MS within SLOP px) or a right click opens a menu at (x, y): v-on="useLongPress(…)".
// A press that moves (a scroll: the browser also cancels the pointer) or lifts first is a tap or a scroll, nothing
// more. The click a long press ends in is eaten wherever it lands (the row, its ▶, the backdrop of the menu that came
// up under the finger); iOS may send none, so the next press stops waiting for it (as lib/dismiss.ts). Android says
// contextmenu for a long press too: whichever comes first opens the menu, once. A right click on text selected in the
// row keeps the browser's own menu (to copy the selection)
const LONG_MS = 500,
  SLOP = 8;

export function useLongPress(open: (x: number, y: number) => void) {
  let timer = 0,
    x0 = 0,
    y0 = 0,
    fired = false;
  const stop = () => {
    clearTimeout(timer);
    timer = 0;
    removeEventListener("scroll", stop, true);
  };
  const eat = (e: Event) => {
    e.preventDefault();
    e.stopImmediatePropagation();
    fed();
  };
  const fed = () => {
    removeEventListener("click", eat, true);
    removeEventListener("pointerdown", fed, true);
  };
  const fire = () => {
    stop();
    fired = true;
    addEventListener("click", eat, true);
    addEventListener("pointerdown", fed, true);
    navigator.vibrate?.(10);
    open(x0, y0);
  };
  return {
    pointerdown(e: PointerEvent) {
      fired = false;
      if (e.pointerType === "mouse") return; // a mouse right-clicks
      [x0, y0] = [e.clientX, e.clientY];
      timer = window.setTimeout(fire, LONG_MS);
      addEventListener("scroll", stop, { capture: true, passive: true });
    },
    pointermove(e: PointerEvent) {
      if (timer && Math.hypot(e.clientX - x0, e.clientY - y0) > SLOP) stop();
    },
    pointerup: stop,
    pointercancel: stop,
    contextmenu(e: MouseEvent) {
      if (fired) return e.preventDefault();
      if (timer) {
        e.preventDefault();
        return fire();
      }
      if (getSelection()?.toString()) return;
      e.preventDefault();
      open(e.clientX, e.clientY);
    },
  };
}
