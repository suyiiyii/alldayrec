<script setup lang="ts">
// The same todo said several times (server db.sources): 提到 N 次, each time it was said opening the transcript there
import { t } from "../i18n";
import { hhmm, type Source } from "../lib/api";

defineProps<{ sources: Source[] }>();
const when = (s: Source) => (s.at ?? s.start).slice(5, 10) + " " + hhmm(s.at ?? s.start);
const to = (s: Source) => ({
  path: `/c/${s.conv}`,
  query: s.at ? { tab: "transcript", at: s.at } : {},
});
</script>

<template>
  <details class="sources">
    <summary>{{ t("todoItem.said", sources.length) }}</summary>
    <ul>
      <li v-for="s in sources" :key="s.id">
        <RouterLink :to="to(s)"
          >{{ when(s)
          }}<template v-if="s.quote"> {{ t("todoItem.quote", { q: s.quote }) }}</template
          ><template v-else-if="s.title"> {{ s.title }}</template></RouterLink
        >
      </li>
    </ul>
  </details>
</template>

<style scoped>
.sources {
  font-size: var(--fs-xs);
  color: var(--muted);
}
summary {
  cursor: pointer;
  min-height: 28px;
  display: flex;
  align-items: center;
}
ul {
  margin: 0;
  padding-left: var(--space-3);
}
li {
  list-style: none;
  min-height: 28px;
  display: flex;
  align-items: center;
  overflow-wrap: anywhere;
}
</style>
