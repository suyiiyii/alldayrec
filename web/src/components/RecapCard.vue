<script setup lang="ts">
import TodoMarks from "./TodoMarks.vue";
import { type Recap } from "../lib/api";
import { shiftDay, today } from "../lib/calendar";

// the newest daily recap, folded to its headline unless open; a todo marked 完成 or 不对 is closed like a due one
defineProps<{ recap: Recap; open?: boolean }>();
defineEmits<{ closed: [conv: string, text: string] }>();
</script>

<template>
  <details class="card recap" :open="open">
    <summary>
      <span class="meta"
        >{{ recap.date === shiftDay(today(), -1) ? "昨天" : recap.date.slice(5) }}的回顾 ·
        {{ recap.conversations }} 段对话<template v-if="recap.todos.length">
          · {{ recap.todos.length }} 条待办</template
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
    <template v-if="recap.todos.length">
      <div class="sub">我要做的</div>
      <ul class="todos">
        <li v-for="t in recap.todos" :key="t.conv + t.text">
          <TodoMarks
            v-if="t.short"
            :conv="t.conv"
            :text="t.text"
            @marked="$emit('closed', t.conv, t.text)"
          />
          <RouterLink :to="'/c/' + t.conv">{{ t.short ?? t.text }}</RouterLink
          ><span v-if="t.to || t.due" class="muted">
            · {{ [t.to && "答应" + t.to, t.due].filter(Boolean).join(" · ") }}</span
          >
        </li>
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
