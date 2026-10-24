<script setup lang="ts">
import { computed } from "vue";
import TodoItem from "./TodoItem.vue";
import { type Recap } from "../lib/api";
import { shiftDay, today } from "../lib/calendar";
import { open as unmarked } from "../lib/todos";

// the newest daily recap, folded to its headline unless open; a todo marked 完成 or 不对 leaves it at once
const props = defineProps<{ recap: Recap; open?: boolean }>();
const todos = computed(() => unmarked(props.recap.todos));
</script>

<template>
  <details class="card recap" :open="open">
    <summary>
      <span class="meta"
        >{{ recap.date === shiftDay(today(), -1) ? "昨天" : recap.date.slice(5) }}的回顾 ·
        {{ recap.conversations }} 段对话<template v-if="todos.length">
          · {{ todos.length }} 条待办</template
        ></span
      >
      <div class="title">{{ recap.headline }}</div>
    </summary>
    <ul>
      <li v-for="(h, i) in recap.highlights" :key="i">
        <RouterLink v-if="h.conv" :to="'/c/' + h.conv">{{ h.text }}</RouterLink
        ><template v-else>{{ h.text }}</template>
      </li>
    </ul>
    <template v-if="todos.length">
      <div class="sub">我要做的</div>
      <ul class="todos">
        <TodoItem v-for="t in todos" :key="t.id ?? t.text" :t="t" />
      </ul>
    </template>
    <template v-if="recap.people.length">
      <div class="sub">聊过的人</div>
      <ul>
        <li v-for="p in recap.people" :key="p.who">{{ p.who }}：{{ p.note }}</li>
      </ul>
    </template>
  </details>
</template>

<style scoped>
summary {
  cursor: pointer;
}
.meta {
  font-size: var(--fs-xs);
  color: var(--muted);
}
.title {
  font-weight: 600;
  margin: 2px 0;
}
ul {
  margin: 6px 0 0;
  padding-left: 20px;
  font-size: var(--fs-sm);
}
/* the todos start with their tick box, no bullet */
.todos {
  list-style: none;
  padding-left: 0;
}
.sub {
  font-size: var(--fs-xs);
  color: var(--muted);
  margin-top: 8px;
}
</style>
