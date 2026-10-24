import { reactive } from "vue";

// One short message at the bottom of the screen (components/Toast.vue in App.vue), gone after a few seconds;
// with undo, a 撤销 button that calls it. A new one replaces the one showing
export const toasted = reactive<{ msg: string; undo?: () => void; n: number }>({ msg: "", n: 0 });
let timer = 0;

export function toast(msg: string, opts: { undo?: () => void } = {}) {
  clearTimeout(timer);
  Object.assign(toasted, { msg, undo: opts.undo, n: toasted.n + 1 });
  timer = window.setTimeout(() => (toasted.msg = ""), opts.undo ? 5000 : 3000);
}
export const dismiss = () => {
  clearTimeout(timer);
  toasted.msg = "";
};
