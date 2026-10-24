<script setup lang="ts">
import { computed } from "vue";
import { intl, lang, t } from "../i18n";
import Icon from "./Icon.vue";
import TodoItem from "./TodoItem.vue";
import { type Recap } from "../lib/api";
import { shiftDay, today } from "../lib/calendar";
import { open as unmarked } from "../lib/todos";

// the newest daily recap, folded to its headline unless open; a todo marked 完成 or 不对 leaves it at once
const props = defineProps<{ recap: Recap; open?: boolean }>();
const todos = computed(() => unmarked(props.recap.todos));
const when = computed(() =>
  props.recap.date === shiftDay(today(), -1)
    ? t("recapCard.yesterday")
    : t("recapCard.of", {
        day:
          lang() === "zh"
            ? props.recap.date.slice(5)
            : new Date(props.recap.date + "T12:00:00").toLocaleDateString(intl(), {
                month: "short",
                day: "numeric",
              }),
      }),
);
</script>

<template>
  <details class="card recap" :open="open">
    <summary>
      <span class="meta"
        ><Icon class="chev" name="chevron-right" :size="14" />{{ when }} ·
        {{ t("calendar.convs", recap.conversations)
        }}<template v-if="todos.length"> · {{ t("convItem.todos", todos.length) }}</template></span
      >
      <div class="title">{{ recap.headline }}</div>
    </summary>
    <ul>
      <li v-for="(h, i) in recap.highlights" :key="i">
        <RouterLink v-if="h.conv" :to="'/c/' + h.conv"
          >{{ h.text }}<Icon class="go" name="chevron-right" :size="16" /></RouterLink
        ><template v-else>{{ h.text }}</template>
      </li>
    </ul>
    <template v-if="todos.length">
      <div class="sub">{{ t("recapCard.mine") }}</div>
      <ul class="todos">
        <TodoItem v-for="x in todos" :key="x.id ?? x.text" :t="x" />
      </ul>
    </template>
    <template v-if="recap.people.length">
      <div class="sub">{{ t("recapCard.people") }}</div>
      <ul>
        <li v-for="p in recap.people" :key="p.who">
          {{ t("recapCard.person", { who: p.who, note: p.note }) }}
        </li>
      </ul>
    </template>
  </details>
</template>

<style scoped>
/* the browser's ▼ is bigger and blacker than the line it opens: a small chevron that turns instead */
summary {
  cursor: pointer;
  list-style: none;
}
summary::-webkit-details-marker {
  display: none;
}
.meta {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--fs-xs);
  color: var(--muted);
}
.chev {
  margin-left: -2px;
}
@media (prefers-reduced-motion: no-preference) {
  .chev {
    transition: transform 0.15s;
  }
}
details[open] .chev {
  transform: rotate(90deg);
}
.title {
  font-weight: 600;
  line-height: 1.4;
  margin: 2px 0;
}
/* the day's points read as text; a grey chevron at the row's end says which open a conversation */
ul a {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  color: inherit;
}
.go {
  margin: 4px 0 0 auto;
  color: var(--muted);
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
