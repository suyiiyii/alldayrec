<script setup lang="ts">
import { computed } from "vue";
import { hhmm, minutes, MOSTLY_MEDIA, type Conversation } from "../lib/api";
import { t } from "../i18n";
import { speakerColor, speakerLabel } from "../lib/speakers";

// one conversation in a list: a card on a time line down the left, its dot the colour of its first tag (the same
// topic, the same colour; grey until summarized)
const { c } = defineProps<{ c: Conversation }>();
const media = computed(() => (c.summary?.media_share ?? 0) >= MOSTLY_MEDIA);
const dot = computed(() => speakerColor(c.summary?.tags?.[0]));
</script>

<template>
  <div class="item">
    <span class="dot" :style="{ background: dot }" />
    <RouterLink class="card" :class="{ media }" :to="'/c/' + c.id">
      <div class="meta">
        {{ media ? t("convItem.media") : "" }}{{ hhmm(c.start) }}–{{ hhmm(c.end) }} ·
        {{ t("common.min", { n: minutes(c) }) }} ·
        {{ (c.participants || []).map(speakerLabel).join(t("common.list")) || "—" }}
        <template v-if="c.summary?.todos?.length">
          · {{ t("convItem.todos", c.summary.todos.length) }}</template
        >
      </div>
      <div
        class="title"
        :class="{
          bad: c.summary?.status === 'failed',
          muted: c.summary?.status !== 'done' && c.summary?.status !== 'failed',
        }"
      >
        {{
          c.summary?.status === "done"
            ? c.summary.title || t("convItem.untitled")
            : c.summary?.status === "failed"
              ? t("api.summaryFailed")
              : t("api.summaryPending")
        }}
      </div>
      <template v-if="!media">
        <div v-if="c.summary?.summary" class="gist">{{ c.summary.summary }}</div>
        <div v-if="c.summary?.tags?.length" class="tagline">
          <span v-for="tg in c.summary.tags" :key="tg" class="tag">{{ tg }}</span>
        </div>
      </template>
    </RouterLink>
  </div>
</template>

<style scoped>
/* the line runs through the gap to the next card; the dot sits level with the card's first line */
.item {
  position: relative;
  padding: 0 0 var(--space-3) 22px;
}
.item::before {
  content: "";
  position: absolute;
  left: 5px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--line);
}
.dot {
  position: absolute;
  left: 0;
  top: 20px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  box-shadow: 0 0 0 3px var(--bg);
}
.card {
  margin-bottom: 0;
}
.card.media {
  opacity: 0.5;
}
.meta {
  font-size: var(--fs-xs);
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.title {
  font-weight: 600;
  margin: 2px 0;
}
.gist {
  font-size: var(--fs-sm);
  color: var(--fg-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tagline {
  margin-top: 6px;
}
.tagline .tag {
  cursor: inherit;
  margin-right: 4px;
  min-height: 22px;
  padding: 0 8px;
}
</style>
