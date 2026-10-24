<script setup lang="ts">
// The conversation player (lib/audio.ts track) as a bar on the screen's bottom: play / pause, where it is on a
// slider, the time in and the length, the speed (1× → 1.5× → 2×)
import { ref } from "vue";
import { t } from "../i18n";
import { track, trackRate, trackSeek, trackToggle } from "../lib/audio";
import { clock } from "../lib/track";
import Icon from "./Icon.vue";

const drag = ref<number | null>(null); // the slider while a finger moves it: it seeks on letting go
const value = (e: Event) => +(e.target as HTMLInputElement).value;
function seek(e: Event) {
  drag.value = null;
  trackSeek(value(e), track.playing);
}
</script>

<template>
  <div class="player" role="region" :aria-label="t('playerBar.player')">
    <div class="in">
      <button
        class="pp"
        :aria-label="track.playing ? t('playerBar.pause') : t('playerBar.play')"
        @click="trackToggle"
      >
        <Icon :name="track.playing ? 'pause' : 'play'" :size="22" />
      </button>
      <span class="t">{{ clock((drag ?? track.at) - track.from) }}</span>
      <input
        type="range"
        :min="track.from"
        :max="track.to"
        step="1000"
        :value="drag ?? track.at"
        :aria-label="t('playerBar.position')"
        @input="drag = value($event)"
        @change="seek"
      />
      <span class="t">{{ clock(track.to - track.from) }}</span>
      <button class="rate" :title="t('playerBar.speed')" @click="trackRate">
        {{ track.rate }}×
      </button>
    </div>
    <p v-if="track.failed" class="bad">{{ t("playerBar.failed") }}</p>
  </div>
</template>

<style scoped>
.player {
  position: fixed;
  left: 0;
  right: 0;
  bottom: var(--tabbar); /* above the bottom tabs (AppHeader), which cover the safe area then */
  z-index: 3;
  background: var(--surface);
  border-top: 1px solid var(--line);
  box-shadow: 0 -1px 3px rgb(0 0 0 / 0.06);
  padding-bottom: max(0px, calc(env(safe-area-inset-bottom) - var(--tabbar)));
}
.in {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
}
.pp {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  min-height: 44px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: var(--accent);
  color: var(--surface);
}
.t {
  flex: none;
  min-width: 36px;
  color: var(--muted);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
  text-align: center;
}
input[type="range"] {
  flex: 1;
  min-width: 0;
  accent-color: var(--accent);
  border: 0;
  background: none;
}
.rate {
  flex: none;
  min-width: 52px;
  padding: 0 var(--space-2);
  font-variant-numeric: tabular-nums;
}
.bad {
  margin: 0 auto;
  max-width: 860px;
  padding: 0 var(--space-4) var(--space-2);
  font-size: var(--fs-sm);
}
@media (max-width: 600px) {
  .in {
    padding: var(--space-2) var(--space-3);
  }
}
</style>
