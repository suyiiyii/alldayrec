<script setup lang="ts">
import { ref } from "vue";
import { t } from "../i18n";
import { hhmmss, type Row } from "../lib/api";
import Icon from "./Icon.vue";
import TranscriptRow from "./TranscriptRow.vue";

// A run of media rows as one grey line; opens to the rows, still greyed. seek: TranscriptRow's
defineProps<{ rows: Row[]; hit?: string; seek?: (r: Row) => void }>();
const open = ref(false);
</script>

<template>
  <button class="fold" @click="open = !open">
    <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="16" />
    {{
      t(
        "mediaFold.line",
        { from: hhmmss(rows[0].start), to: hhmmss(rows.at(-1)!.end), n: rows.length },
        rows.length,
      )
    }}
  </button>
  <template v-if="open">
    <TranscriptRow
      v-for="r in rows"
      :key="r.id"
      :row="r"
      media
      :hit="hit === r.start"
      :seek="seek"
    />
  </template>
</template>

<style scoped>
.fold {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  font-size: var(--fs-sm);
  padding: var(--space-2) 0;
  cursor: pointer;
}
</style>
