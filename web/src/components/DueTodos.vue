<script setup lang="ts">
// The due card (时间线's): 3 at most, today's first, then the latest past due that are not stale (a 待会儿 from last
// week is not today's business); the rest behind one line to /todos, where they can be cleared. A marked todo leaves
// at once
import { computed } from "vue";
import { type Due } from "../lib/api";
import { open } from "../lib/todos";
import { t } from "../i18n";
import Icon from "./Icon.vue";
import TodoItem from "./TodoItem.vue";

const props = defineProps<{ due: Due[] }>();
const left = computed(() => open(props.due));
const shown = computed(() =>
  [
    ...left.value.filter((x) => !x.age_days),
    ...left.value
      .filter((x) => x.age_days && !x.stale)
      .sort((a, b) => b.due_date.localeCompare(a.due_date)),
  ].slice(0, 3),
);
</script>

<template>
  <div v-if="left.length" class="card due">
    <RouterLink class="head" to="/todos"
      >{{ t("dueTodos.title") }}<Icon name="chevron-right" :size="16"
    /></RouterLink>
    <ul>
      <TodoItem v-for="x in shown" :key="x.id" :t="x" />
    </ul>
    <RouterLink v-if="left.length > shown.length" class="rest" to="/todos"
      >{{ t("dueTodos.more", left.length - shown.length) }}<Icon name="chevron-right" :size="16"
    /></RouterLink>
  </div>
</template>

<style scoped>
/* the head and the way to the rest look like the live card's (a card about now, as that one is) */
.head,
.rest {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.head {
  font-weight: 600;
  color: var(--fg);
}
.head svg {
  color: var(--muted);
}
.rest {
  min-height: 36px;
  margin-top: var(--space-1);
  font-size: var(--fs-sm);
}
ul {
  margin: var(--space-1) 0 0;
  padding-left: 0;
}
</style>
