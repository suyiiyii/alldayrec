<script setup lang="ts">
// 完成 / 不对 on one todo by its id (lib/todos.ts), each with 撤销 in its toast. 完成 is the round tick (ticked: a tap
// opens it again), 不对 sits in the ⋯ menu, 撤销 there once marked (few clicks per todo: Bee's users turned off
// review that asked for one on every line)
import { computed, ref } from "vue";
import { t } from "../i18n";
import { type Mark } from "../lib/api";
import { useDismiss } from "../lib/dismiss";
import { useMutation } from "../lib/mutation";
import { markOf, markWithUndo, setMarks } from "../lib/todos";
import { toast } from "../lib/toast";
import Icon from "./Icon.vue";

const props = defineProps<{ id: string; mark?: Mark | null }>();
const { busy, error, detail, run } = useMutation();
const now = computed(() => markOf(props));
const menu = ref<HTMLDetailsElement>(),
  opened = ref(false),
  left = ref(false); // the menu opens rightwards from a ⋯ in the screen's left half, else leftwards: on screen either way
useDismiss(opened, menu);
const half = () => window.innerWidth / 2;
async function mark(m: Mark | null) {
  opened.value = false;
  await run(() =>
    m
      ? markWithUndo(
          [props.id],
          m,
          t(m === "done" ? "todoMarks.doneToast" : "todoMarks.wrongToast"),
        )
      : setMarks([props.id], null).then(() => toast(t("common.undone"))),
  );
}
defineExpose({ mark }); // TodoItem's swipe
</script>

<template>
  <span class="marks">
    <button
      class="check"
      :class="{ on: now === 'done', wrong: now === 'wrong' }"
      :disabled="busy"
      :title="now ? t('common.undo') : t('todoMarks.done')"
      :aria-label="now ? t('common.undo') : t('todoMarks.markDone')"
      @click="mark(now ? null : 'done')"
    >
      <Icon :name="now === 'wrong' ? 'x' : 'check'" :size="16" />
    </button>
    <details ref="menu" class="more" :open="opened" @toggle="opened = menu!.open">
      <summary
        :title="t('todoMarks.more')"
        :aria-label="t('todoMarks.more')"
        @click="left = menu!.getBoundingClientRect().left < half()"
      >
        <Icon name="more-horizontal" :size="18" />
      </summary>
      <div class="menu" :class="{ left }">
        <button v-if="now" class="btn-ghost" :disabled="busy" @click="mark(null)">
          {{ t("common.undo") }}
        </button>
        <button v-else class="btn-ghost wrong" :disabled="busy" @click="mark('wrong')">
          {{ t("todoMarks.notTodo") }}
        </button>
      </div>
    </details>
    <span v-if="error" class="bad" role="alert" :title="detail">{{ error }}</span>
  </span>
</template>

<style scoped>
.marks {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  vertical-align: middle;
  margin-left: 6px;
}
.check {
  color: transparent; /* the tick shows on hover and once ticked */
}
.check:hover:not(:disabled),
.check.on {
  border-color: var(--accent);
}
.check:hover:not(:disabled) {
  color: var(--accent);
}
.check.on {
  color: var(--surface);
}
.check.wrong {
  color: var(--muted);
}
.more {
  position: relative;
}
.more summary {
  list-style: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  cursor: pointer;
}
.more summary::-webkit-details-marker {
  display: none;
}
.more[open] summary {
  background: var(--accent-soft);
  color: var(--accent);
}
.menu {
  position: absolute;
  right: 0;
  left: auto;
  top: 100%;
  z-index: 3;
  margin-top: var(--space-1);
  padding: var(--space-1);
  background: var(--surface);
  border-radius: var(--radius-sm);
  box-shadow:
    0 0 0 1px var(--line),
    0 4px 12px rgb(0 0 0 / 0.12);
  white-space: nowrap;
}
.menu.left {
  right: auto;
  left: 0;
}
.menu button.wrong {
  color: var(--err);
}
/* an error wraps inside the page instead of running off a phone's edge */
.marks .bad {
  white-space: normal;
  overflow-wrap: anywhere;
}
</style>
