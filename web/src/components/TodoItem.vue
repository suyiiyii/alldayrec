<script setup lang="ts">
// One of my todos in two lines: what, then where it came from (the sentence, whom it is to, the conversation, the
// time said); a tap opens the transcript at that sentence. On a phone a swipe left of 60 px marks it done
import { computed, ref } from "vue";
import { t as tr } from "../i18n";
import { hhmm, type TodoView } from "../lib/api";
import { speakerLabel } from "../lib/speakers";
import Icon from "./Icon.vue";
import TodoMarks from "./TodoMarks.vue";

const { t } = defineProps<{ t: TodoView }>();
const cut = (s: string, n: number) => ([...s].length > n ? [...s].slice(0, n).join("") + "…" : s);
const who = (names: string[]) => {
  const two = names.slice(0, 2).map(speakerLabel).join(tr("common.list"));
  return names.length > 2
    ? tr("todoItem.andMore", { names: two, n: names.length, more: names.length - 2 })
    : two;
};
const context = computed(() =>
  [
    t.quote && tr("todoItem.quote", { q: cut(t.quote, 40) }),
    t.with?.length && tr(t.to ? "todoItem.promised" : "todoItem.with", { who: who(t.with) }),
    t.title ? cut(t.title, 12) : t.start && tr("todoItem.conv", { t: hhmm(t.start) }),
    t.at && hhmm(t.at),
  ]
    .filter(Boolean)
    .join(" · "),
);
const when = computed(() =>
  t.age_days ? tr("todoItem.overdue", { n: t.age_days }, t.age_days) : t.due,
);
const to = computed(
  () => `/c/${t.conv}` + (t.at ? "?" + new URLSearchParams({ tab: "transcript", at: t.at }) : ""),
);

// the swipe: touch only (a mouse drags text), a mostly vertical move is the page's scroll
const marks = ref<InstanceType<typeof TodoMarks>>();
const dx = ref(0);
let x0 = NaN,
  y0 = 0,
  swiped = false;
function down(e: PointerEvent) {
  if (e.pointerType !== "touch" || !t.id) return;
  [x0, y0, swiped] = [e.clientX, e.clientY, false];
}
function move(e: PointerEvent) {
  if (Number.isNaN(x0)) return;
  const x = e.clientX - x0;
  if (!dx.value && Math.abs(e.clientY - y0) > Math.abs(x)) x0 = NaN;
  else dx.value = Math.min(0, x);
}
function up() {
  if (dx.value <= -60) {
    swiped = true;
    void marks.value?.mark("done");
  }
  cancel();
}
const cancel = () => ([x0, dx.value] = [NaN, 0]);
function guard(e: MouseEvent) {
  if (swiped) e.preventDefault(); // the swipe's own click: no opening
  swiped = false;
}
</script>

<template>
  <li class="todo-item" :class="{ swiping: dx }">
    <span v-if="dx" class="behind"><Icon name="check" :size="18" />{{ tr("todoItem.done") }}</span>
    <div
      class="slide"
      :style="dx ? { transform: `translateX(${dx}px)` } : undefined"
      @pointerdown="down"
      @pointermove="move"
      @pointerup="up"
      @pointercancel="cancel"
    >
      <TodoMarks v-if="t.id" :id="t.id" ref="marks" :mark="t.mark" />
      <RouterLink class="body" :to="to" @click.capture="guard">
        <div class="what">
          {{ t.short ?? t.text }}<span v-if="when" class="when"> · {{ when }}</span>
        </div>
        <div v-if="context" class="ctx">{{ context }}</div>
      </RouterLink>
    </div>
  </li>
</template>

<style scoped>
.todo-item {
  position: relative;
  list-style: none;
}
.todo-item.swiping {
  overflow: hidden; /* the row slides under the card's edge (not always: the ⋯ menu hangs below the row) */
}
.todo-item + .todo-item {
  border-top: 1px solid var(--line);
}
/* what a swipe uncovers */
.behind {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-1);
  padding-right: var(--space-4);
  background: var(--ok-soft);
  color: var(--ok);
  font-size: var(--fs-sm);
}
.slide {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  background: var(--surface);
  touch-action: pan-y; /* a sideways move is the swipe's, not the browser's */
}
.slide > .marks {
  margin: -2px 0 0;
}
.body {
  flex: 1;
  min-width: 0;
  color: var(--fg);
  font-size: var(--fs-sm);
}
.when {
  color: var(--muted);
  font-size: var(--fs-xs);
}
.ctx {
  font-size: var(--fs-xs);
  color: var(--muted);
  overflow-wrap: anywhere;
}
</style>
