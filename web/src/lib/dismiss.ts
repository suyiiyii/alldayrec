import { onUnmounted, watch, type Ref } from "vue";

// A ⋯ menu that is open closes on Escape or on a tap outside it. The tap only closes it, as a scrim would: a tap meant
// to put a menu away must not also tick a todo or follow a link under it
export function useDismiss(open: Ref<boolean>, root: Readonly<Ref<HTMLElement | null | undefined>>) {
  const away = (e: Event) => {
    if (e instanceof KeyboardEvent ? e.key !== "Escape" : root.value?.contains(e.target as Node)) return;
    if (e.type === "click") {
      e.preventDefault();
      e.stopPropagation();
    }
    open.value = false;
  };
  const off = () => {
    removeEventListener("click", away, true);
    removeEventListener("keydown", away, true);
  };
  watch(open, (o) => {
    off();
    if (o) {
      addEventListener("click", away, true);
      addEventListener("keydown", away, true);
    }
  });
  onUnmounted(off);
}
