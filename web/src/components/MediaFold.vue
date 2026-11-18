<script setup lang="ts">
import { ref } from "vue";
import { t } from "../i18n";
import { hhmmss, type Row } from "../lib/api";
import Icon from "./Icon.vue";
import TranscriptRow from "./TranscriptRow.vue";

// A run of media rows as one grey line; opens to the rows, still greyed. seek, caption, menu, who: TranscriptRow's.
// kind (tenant media_view): media (外放) or broadcast (广播/提示音) named on the line; live: its 改回现场 beside it
defineProps<{
  rows: Row[];
  kind?: string;
  live?: (rows: Row[]) => void;
  hit?: string;
  seek?: (r: Row) => void;
  caption?: boolean;
  menu?: (r: Row, x: number, y: number) => void;
  who?: (speaker: string, r: Row) => void;
}>();
const open = ref(false);
</script>

<template>
  <div class="head">
    <button class="fold" :data-start="rows[0].start" :aria-expanded="open" @click="open = !open">
      <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="16" />
      {{
        t(
          kind ? `mediaFold.${kind}` : "mediaFold.line",
          { from: hhmmss(rows[0].start), to: hhmmss(rows.at(-1)!.end), n: rows.length },
          rows.length,
        )
      }}
    </button>
    <button v-if="live" class="live" @click="live(rows)">{{ t("mediaFold.live") }}</button>
  </div>
  <template v-if="open">
    <TranscriptRow
      v-for="r in rows"
      :key="r.id"
      :row="r"
      media
      :hit="hit === r.start"
      :seek="seek"
      :caption="caption"
      :menu="menu"
      :who="who"
    />
  </template>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  border-bottom: 1px solid var(--line);
}
.fold {
  display: block;
  flex: 1;
  min-width: 0;
  text-align: left;
  border: 0;
  border-radius: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  font-size: var(--fs-sm);
  padding: var(--space-2) 0;
  cursor: pointer;
}
.live {
  flex: none;
  font-size: var(--fs-sm);
  min-height: 32px;
  padding: 0 var(--space-2);
}
</style>
