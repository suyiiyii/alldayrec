import { onUnmounted, watch, type Ref } from "vue";

// A ⋯ menu that is open closes on Escape or on a tap outside it. The tap only closes it, as a scrim would: a tap meant
// to put a menu away must not also tick a todo or follow a link under it. The tap is caught as it begins (pointerdown):
// iOS sends no click for a tap on plain text to a listener on the window; its click, when one comes, is eaten, and the
// next tap's beginning stops that (a touch that became a scroll has none)
export function useDismiss(open: Ref<boolean>, root: Readonly<Ref<HTMLElement | null | undefined>>) {
  const eat = (e: Event) => {
    e.preventDefault();
    e.stopPropagation();
    fed();
  };
  const fed = () => {
    removeEventListener("click", eat, true);
    removeEventListener("pointerdown", fed, true);
  };
  const away = (e: Event) => {
    if (e instanceof KeyboardEvent ? e.key !== "Escape" : root.value?.contains(e.target as Node)) return;
    open.value = false;
    if (e.type === "pointerdown") {
      addEventListener("click", eat, true);
      addEventListener("pointerdown", fed, true); // added while this pointerdown is dispatched: from the next one on
    }
  };
  const off = () => {
    removeEventListener("pointerdown", away, true);
    removeEventListener("keydown", away, true);
  };
  watch(open, (o) => {
    off();
    if (o) {
      addEventListener("pointerdown", away, true);
      addEventListener("keydown", away, true);
    }
  });
  onUnmounted(() => {
    off();
    fed();
  });
}
