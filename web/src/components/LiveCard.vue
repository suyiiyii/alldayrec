<script setup lang="ts">
import { computed, ref } from "vue";
import Avatar from "./Avatar.vue";
import Icon from "./Icon.vue";
import LivePanel from "./LivePanel.vue";
import type { Row } from "../lib/api";
import { today } from "../lib/calendar";
import { useLive } from "../lib/live";

// Today's first card while a conversation goes on (Today shows it only then): its newest captions as they are said,
// from the live page's data, the live analysis's headline, and 全屏 to the live page
const LINES = 3;
const { rows, partials, convs, status, lives } = useLive(ref(today()));
// the newest sentences of the conversations going on (a device's from its conversation's start), the one being said last
const lines = computed(() => {
  const from = new Map(
    (status.value?.devices ?? [])
      .filter((d) => d.recording && d.conversation)
      .map((d) => [d.device, d.conversation!.start]),
  );
  const said = (r: Row) => {
    const f = from.get(r.source ?? "");
    return f !== undefined && r.start >= f;
  };
  return [...rows.value, ...Object.values(partials.value)]
    .filter(said)
    .sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0))
    .slice(-LINES);
});
const isNow = (r: Row) => Object.values(partials.value).includes(r);
</script>

<template>
  <section class="card live">
    <div class="head">
      <span class="pulse rec" />进行中的对话
      <RouterLink class="more" to="/live">全屏<Icon name="chevron-right" :size="16" /></RouterLink>
    </div>
    <RouterLink class="lines" to="/live" aria-label="实时字幕">
      <p
        v-for="r in lines"
        :key="r.id"
        class="line"
        :class="{ draft: r.state === 'draft', now: isNow(r) }"
        :title="r.state === 'draft' ? '实时初稿，稍后定稿' : undefined"
      >
        <Avatar v-if="r.speaker" :name="r.speaker" :size="20" /><span v-else class="who"
          ><span class="pulse"
        /></span>
        <span class="text">{{ r.text }}</span>
      </p>
      <p v-if="!lines.length" class="line muted">字幕马上出来…</p>
    </RouterLink>
    <LivePanel class="foot" mini :status="status" :lives="lives" :convs="convs" />
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
/* the captions: one or two lines each, the newest at the bottom */
.lines {
  display: block;
  color: var(--fg);
  margin: var(--space-1) 0 var(--space-3);
}
.line {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin: var(--space-2) 0 0;
  line-height: 1.5;
}
.line .avatar,
.who {
  margin-top: 2px;
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
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.draft .text {
  color: var(--fg-2);
}
.now .text {
  color: var(--muted);
}
.foot {
  border-top: 1px solid var(--line);
  padding-top: var(--space-3);
}
</style>
