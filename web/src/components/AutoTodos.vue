<script setup lang="ts">
// 自动整理: the todos closed automatically (server todo_auto.py), anyone's, folded under the open ones in three groups:
// done, dropped, stale. Each shows why and the sentence it rests on (a tap opens the transcript there, highlighted), and
// 撤销 opens it again; changed tells the page to load its lists again
import { computed } from "vue";
import { t } from "../i18n";
import { hhmm, type TodoView } from "../lib/api";
import { useMutation } from "../lib/mutation";
import { speakerLabel } from "../lib/speakers";
import { toast } from "../lib/toast";
import { autoUndone, undoAuto } from "../lib/todos";
import Icon from "./Icon.vue";
import TodoSources from "./TodoSources.vue";

const props = defineProps<{ items: TodoView[] }>();
const emit = defineEmits<{ changed: [] }>();
const { busy, run } = useMutation();
const KINDS = ["done", "dropped", "stale"] as const;
const shown = computed(() => props.items.filter((x) => x.auto && !autoUndone(x.id, x.auto)));
const groups = computed(() =>
  KINDS.map((k) => ({ k, items: shown.value.filter((x) => x.auto!.kind === k) })).filter(
    (g) => g.items.length,
  ),
);
// the sentence: when, who, what
const said = ({ auto: a }: TodoView) =>
  [
    a!.at!.slice(5, 10) + " " + hhmm(a!.at!),
    a!.speaker && speakerLabel(a!.speaker),
    a!.quote && t("todoItem.quote", { q: a!.quote }),
  ]
    .filter(Boolean)
    .join(" ");
const to = (x: TodoView) => ({
  path: `/c/${x.auto!.conv}`,
  query: x.auto!.at ? { tab: "transcript", at: x.auto!.at } : {},
});
const undo = (x: TodoView) =>
  run(async () => {
    await undoAuto(x.id!, x.auto!);
    toast(t("common.undone"));
    emit("changed");
  });
</script>

<template>
  <details v-if="shown.length" class="card auto">
    <summary>
      <Icon class="chev" name="chevron-right" :size="16" />
      <h2>
        {{ t("autoTodos.title") }}<span class="n">{{ shown.length }}</span>
      </h2>
    </summary>
    <section v-for="g in groups" :key="g.k">
      <h3>
        {{ t(`autoTodos.${g.k}`) }}<span class="n">{{ g.items.length }}</span>
      </h3>
      <ul>
        <li v-for="x in g.items" :key="x.id">
          <div class="row">
            <div class="body">
              <div class="what">
                {{ x.text
                }}<span v-if="x.owner && x.owner !== '我'" class="who">
                  · {{ speakerLabel(x.owner) }}</span
                >
              </div>
              <div v-if="x.auto!.reason" class="why">{{ x.auto!.reason }}</div>
              <RouterLink v-if="x.auto!.conv && x.auto!.at" class="quote" :to="to(x)">{{
                said(x)
              }}</RouterLink>
            </div>
            <button class="btn-ghost" :disabled="busy" @click="undo(x)">
              {{ t("common.undo") }}
            </button>
          </div>
          <TodoSources v-if="x.sources?.length" :sources="x.sources" />
        </li>
      </ul>
    </section>
  </details>
</template>

<style scoped>
summary {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  cursor: pointer;
  list-style: none;
  min-height: 36px;
}
summary::-webkit-details-marker {
  display: none;
}
.chev {
  color: var(--muted);
  transition: transform 0.15s;
}
details[open] .chev {
  transform: rotate(90deg);
}
h2 {
  font-size: var(--fs-md);
  margin: 0;
}
h3 {
  font-size: var(--fs-sm);
  margin: var(--space-3) 0 0;
  color: var(--fg-2);
}
.n {
  margin-left: var(--space-2);
  font-size: var(--fs-sm);
  font-weight: 400;
  color: var(--muted);
}
ul {
  margin: var(--space-1) 0 0;
  padding-left: 0;
}
li {
  list-style: none;
  padding: var(--space-2) 0;
}
li + li {
  border-top: 1px solid var(--line);
}
.row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}
.body {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-sm);
}
.what {
  color: var(--fg-2);
}
.who,
.why {
  color: var(--muted);
  font-size: var(--fs-xs);
}
.quote {
  display: block;
  font-size: var(--fs-xs);
  overflow-wrap: anywhere;
  min-height: 24px;
}
.btn-ghost {
  flex: none;
  min-height: 32px;
}
</style>
