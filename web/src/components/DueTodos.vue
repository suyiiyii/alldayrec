<script setup lang="ts">
import TodoMarks from "./TodoMarks.vue";
import { type Due } from "../lib/api";
import { today } from "../lib/calendar";

// my todos due today or before; one marked 完成 or 不对 is closed (the page drops it here and from the recap)
defineProps<{ due: Due[] }>();
defineEmits<{ closed: [conv: string, text: string] }>();
</script>

<template>
  <div v-if="due.length" class="card due">
    <div class="meta">到期的待办 · {{ due.length }} 条</div>
    <ul>
      <li v-for="t in due" :key="t.conv + t.text">
        <TodoMarks :conv="t.conv" :text="t.text" @marked="$emit('closed', t.conv, t.text)" />
        <RouterLink :to="'/c/' + t.conv + (t.at ? '#' + t.at : '')">{{ t.text }}</RouterLink
        ><span class="muted">
          · {{ t.due_date === today() ? "今天" : "已过期 " + t.due_date.slice(5)
          }}{{ t.to ? " · 答应" + t.to : "" }}</span
        >
      </li>
    </ul>
  </div>
</template>

<style scoped>
.meta {
  font-size: var(--fs-xs);
  color: var(--muted);
}
/* each line starts with its tick box, no bullet */
ul {
  list-style: none;
  margin: 6px 0 0;
  padding-left: 0;
  font-size: var(--fs-sm);
}
</style>
