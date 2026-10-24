<script setup lang="ts">
// 待办: my todos due today or before in three groups: 今天, 本周 (past due, not stale), 更早 (stale: greyed, cleared
// at once with 全部标为已处理). A marked one leaves at once; the toast's 撤销 brings it back
import { computed, onMounted, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Icon from "../components/Icon.vue";
import Skeleton from "../components/Skeleton.vue";
import TodoItem from "../components/TodoItem.vue";
import { get, type Due } from "../lib/api";
import { useMutation } from "../lib/mutation";
import { markWithUndo, open } from "../lib/todos";

const due = ref<Due[] | null>(null);
const { busy, error, detail, run } = useMutation();
onMounted(() =>
  run(async () => {
    due.value = await get<Due[]>("/api/todos/due");
  }),
);
const latest = (ts: Due[]) => [...ts].sort((a, b) => b.due_date.localeCompare(a.due_date));
const groups = computed(() => {
  const ts = open(due.value ?? []);
  return [
    { key: "today", title: "今天", items: ts.filter((t) => !t.age_days) },
    { key: "week", title: "本周", items: latest(ts.filter((t) => t.age_days && !t.stale)) },
    { key: "old", title: "更早", items: latest(ts.filter((t) => t.stale)) },
  ].filter((g) => g.items.length);
});
const clear = (ts: Due[]) =>
  run(() =>
    markWithUndo(
      ts.map((t) => t.id),
      "done",
      `已把 ${ts.length} 条标为已处理`,
    ),
  );
</script>

<template>
  <AppHeader />
  <main>
    <RouterLink class="back" to="/"><Icon name="chevron-left" :size="18" />今天</RouterLink>
    <h1>待办</h1>
    <p v-if="error" class="bad" role="alert" :title="detail">{{ error }}</p>
    <Skeleton v-if="!due && !error" />
    <div v-else-if="due && !groups.length" class="card empty">
      <Icon name="check" :size="48" />
      <p>没有到期的待办</p>
    </div>
    <section v-for="g in groups" :key="g.key" class="card" :class="g.key">
      <div class="head">
        <h2>
          {{ g.title }}<span class="n">{{ g.items.length }}</span>
        </h2>
        <button v-if="g.key === 'old'" class="btn" :disabled="busy" @click="clear(g.items)">
          全部标为已处理
        </button>
      </div>
      <p v-if="g.key === 'old'" class="hint">过期一周以上，说「待会儿」「明天」的过期三天以上</p>
      <ul>
        <TodoItem v-for="t in g.items" :key="t.id" :t="t" />
      </ul>
    </section>
  </main>
</template>

<style scoped>
.back {
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  margin-left: -4px;
}
h1 {
  margin: var(--space-1) 0 var(--space-4);
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
h2 {
  font-size: var(--fs-md);
  margin: 0;
}
.n {
  margin-left: var(--space-2);
  font-size: var(--fs-sm);
  font-weight: 400;
  color: var(--muted);
}
.hint {
  margin: var(--space-1) 0 0;
  font-size: var(--fs-xs);
  color: var(--muted);
}
ul {
  margin: var(--space-2) 0 0;
  padding-left: 0;
}
/* stale: the todos greyed */
.old :deep(.what) {
  color: var(--fg-2);
}
</style>
