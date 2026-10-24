<script setup lang="ts">
// 完成 / 不对 on one todo: a feedback line naming it (TODO_MARK, then its text); a marked todo leaves the due list
// and the recaps. 完成 is the round tick, 不对 sits in the ⋯ menu (few clicks per todo: Bee's users turned off
// review that asked for one on every line). No undo: the server keeps feedback lines, it has no way to drop one
import { ref } from "vue";
import { useMutation } from "../lib/mutation";
import { post, TODO_MARK, type Feedback } from "../lib/api";
import { toast } from "../lib/toast";
import Icon from "./Icon.vue";

const props = defineProps<{ conv: string; text: string }>();
const emit = defineEmits<{ marked: [feedback: Feedback[]] }>();
const { busy, error, detail, run } = useMutation();
const done = ref(false),
  menu = ref<HTMLDetailsElement>(),
  left = ref(false); // the menu opens rightwards from a ⋯ in the screen's left half, else leftwards: on screen either way
const half = () => window.innerWidth / 2;
async function mark(m: keyof typeof TODO_MARK) {
  menu.value!.open = false;
  if (m === "done") done.value = true;
  await run(async () => {
    emit(
      "marked",
      await post<Feedback[]>(`/api/conversation/${props.conv}/feedback`, {
        text: TODO_MARK[m] + props.text,
      }),
    );
    toast(m === "done" ? "已完成" : "已标为不对");
  });
  if (error.value) done.value = false;
}
</script>

<template>
  <span class="marks">
    <button
      class="check"
      :class="{ on: done }"
      :disabled="busy"
      title="完成"
      aria-label="标为完成"
      @click="mark('done')"
    >
      <Icon name="check" :size="16" />
    </button>
    <details ref="menu" class="more">
      <summary
        title="更多"
        aria-label="更多"
        @click="left = menu!.getBoundingClientRect().left < half()"
      >
        <Icon name="more-horizontal" :size="18" />
      </summary>
      <div class="menu" :class="{ left }">
        <button class="btn-ghost" :disabled="busy" @click="mark('wrong')">不对，不是待办</button>
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
.menu button {
  color: var(--err);
}
/* an error wraps inside the page instead of running off a phone's edge */
.marks .bad {
  white-space: normal;
  overflow-wrap: anywhere;
}
</style>
