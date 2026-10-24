<script setup lang="ts">
// The due card: today's todos and the 3 latest past due that are not stale (a 待会儿 from last week is not today's
// business); the rest behind one line to /todos, where they can be cleared. A marked todo leaves at once
import { computed } from "vue";
import { type Due } from "../lib/api";
import { open } from "../lib/todos";
import Icon from "./Icon.vue";
import TodoItem from "./TodoItem.vue";

const props = defineProps<{ due: Due[] }>();
const left = computed(() => open(props.due));
const shown = computed(() => [
  ...left.value.filter((t) => !t.age_days),
  ...left.value
    .filter((t) => t.age_days && !t.stale)
    .sort((a, b) => b.due_date.localeCompare(a.due_date))
    .slice(0, 3),
]);
</script>

<template>
  <div v-if="left.length" class="card due">
    <RouterLink class="meta" to="/todos"
      >到期的待办<Icon name="chevron-right" :size="14"
    /></RouterLink>
    <ul>
      <TodoItem v-for="t in shown" :key="t.id" :t="t" />
    </ul>
    <RouterLink v-if="left.length > shown.length" class="rest" to="/todos"
      >还有 {{ left.length - shown.length }} 条更早的待办<Icon name="chevron-right" :size="14"
    /></RouterLink>
  </div>
</template>

<style scoped>
.meta,
.rest {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--fs-xs);
  color: var(--muted);
}
.rest {
  margin-top: var(--space-1);
}
ul {
  margin: var(--space-1) 0 0;
  padding-left: 0;
}
</style>
