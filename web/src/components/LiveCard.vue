<script setup lang="ts">
import { computed, ref } from "vue";
import Avatar from "./Avatar.vue";
import Icon from "./Icon.vue";
import LivePanel from "./LivePanel.vue";
import type { Row } from "../lib/api";
import { t } from "../i18n";
import { today } from "../lib/calendar";
import { useLive } from "../lib/live";

// 时间线's first card while a conversation goes on (shown only then): the sentence being said last on one line, from the
// live page's data, the live analysis of its device under it, and 全屏 to the live page
const { rows, partials, status, lives } = useLive(ref(today()));
// the sentence being said in a conversation going on (a device's from its conversation's start): a partial if there is
// one, else the newest row
const line = computed(() => {
  const from = new Map(
    (status.value?.devices ?? [])
      .filter((d) => d.recording && d.conversation)
      .map((d) => [d.device, d.conversation!.start]),
  );
  const newest = (rs: Row[]) =>
    rs
      .filter((r) => {
        const f = from.get(r.source ?? "");
        return f !== undefined && r.start >= f;
      })
      .reduce<Row | null>((a, r) => (a && a.start > r.start ? a : r), null);
  return newest(Object.values(partials.value)) ?? newest(rows.value);
});
const isNow = (r: Row) => Object.values(partials.value).includes(r);
</script>

<template>
  <section class="card live">
    <div class="head">
      <span class="pulse rec" />{{ t("liveCard.going") }}
      <RouterLink class="more" to="/live"
        >{{ t("liveCard.full") }}<Icon name="chevron-right" :size="16"
      /></RouterLink>
    </div>
    <!-- keyed by its start: a partial growing, and the draft row it ends as, stay one line; the next sentence rolls in -->
    <RouterLink class="cap" to="/live" :aria-label="t('liveCard.captions')">
      <Transition name="roll">
        <p
          v-if="line"
          :key="line.source + line.start"
          class="line"
          :class="{ draft: line.state === 'draft', now: isNow(line) }"
          :title="line.state === 'draft' ? t('liveCard.draft') : undefined"
        >
          <Avatar v-if="line.speaker" :name="line.speaker" :size="20" /><span v-else class="who"
            ><span class="pulse"
          /></span>
          <span class="text">{{ line.text }}</span>
        </p>
        <p v-else class="line muted">{{ t("liveCard.soon") }}</p>
      </Transition>
    </RouterLink>
    <LivePanel class="foot" mini :status="status" :lives="lives" :device="line?.source" />
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 600;
}
.rec {
  background: var(--err);
}
.more {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  font-size: var(--fs-sm);
  font-weight: 400;
}
/* the caption: one line; a new sentence pushes the old one up and out */
.cap {
  display: block;
  position: relative;
  overflow: hidden;
  height: 24px;
  margin: var(--space-1) 0 var(--space-3);
  color: var(--fg);
}
.line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: 24px;
  margin: 0;
}
.who {
  flex: none;
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.text {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.draft .text {
  color: var(--fg-2);
}
.now .text {
  color: var(--muted);
}
.roll-enter-active,
.roll-leave-active {
  transition: transform 200ms ease;
}
.roll-leave-active {
  position: absolute;
  inset: 0 0 auto;
}
.roll-enter-from {
  transform: translateY(100%);
}
.roll-leave-to {
  transform: translateY(-100%);
}
@media (prefers-reduced-motion: reduce) {
  .roll-enter-active,
  .roll-leave-active {
    transition: none;
  }
}
.foot {
  border-top: 1px solid var(--line);
  padding-top: var(--space-3);
}
</style>
