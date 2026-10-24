<script setup lang="ts">
// The sentence a summary item rests on (its at, null when the model's time matched no sentence: then nothing): ▶ plays
// about 10 s of the recording from it, right here, ■ while it plays; its time opens 原文 there (jump)
import { computed } from "vue";
import { t } from "../i18n";
import { hhmmss, type Conversation } from "../lib/api";
import { play, player } from "../lib/audio";
import { isoLocal } from "../lib/track";
import Icon from "./Icon.vue";

const { at, c } = defineProps<{ at?: string | null; c: Conversation }>();
defineEmits<{ jump: [at: string] }>();
const key = computed(() => `${c.id}|${at}`);
function hear() {
  const end = Math.min(Date.parse(at!) + 10_000, Date.parse(c.end));
  play(
    key.value,
    `/api/audio?${new URLSearchParams({ start: at!, end: isoLocal(end), ...(c.device && { device: c.device }) })}`,
  );
}
</script>

<template>
  <span v-if="at" class="at"
    ><button
      class="play"
      :disabled="player.failed.has(key)"
      :title="t('conv.hearQuote')"
      :aria-label="t('conv.hearQuote')"
      @click="hear"
    >
      <Icon
        :name="player.failed.has(key) ? 'x' : player.playing === key ? 'square' : 'play'"
        :size="14"
      /></button
    ><a :href="'#' + at" :title="t('conv.toTranscript')" @click.prevent="$emit('jump', at)">{{
      hhmmss(at)
    }}</a></span
  >
</template>

<style scoped>
.at {
  display: inline-flex;
  align-items: center;
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
/* the glyph is small, the button around it a 32 px square */
.play {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  min-height: 32px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--accent);
}
.play:disabled {
  color: var(--muted);
}
a {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  padding-right: var(--space-1);
}
</style>
