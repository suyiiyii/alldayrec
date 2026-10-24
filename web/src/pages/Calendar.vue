<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import MonthGrid from "../components/MonthGrid.vue";
import { calendar, loadCalendar } from "../lib/calendar";

// Every month with sentences, newest first: a day's conversations and speech minutes, a tap opens its conversations
const router = useRouter();
onMounted(() => loadCalendar(true));
const months = computed(() => {
  const by: Record<string, { conversations: number; dictations: number; min: number }> = {};
  for (const [d, x] of Object.entries(calendar.value)) {
    const m = (by[d.slice(0, 7)] ??= { conversations: 0, dictations: 0, min: 0 });
    m.conversations += x.conversations;
    m.dictations += x.dictations;
    m.min += x.speech_min;
  }
  return Object.entries(by).sort((a, b) => (a[0] < b[0] ? 1 : -1));
});
const open = (day: string) => router.push({ path: "/conversations", query: { day } });
</script>

<template>
  <AppHeader />
  <main>
    <section v-for="[m, t] in months" :key="m" class="month card">
      <h2>
        {{ m.replace("-", " 年 ") }} 月
        <span
          >{{ t.conversations }} 段对话 · {{ t.dictations }} 段口述 ·
          {{ (t.min / 60).toFixed(1) }} 小时</span
        >
      </h2>
      <MonthGrid :month="m" big @pick="open" />
    </section>
    <div v-if="!months.length" class="muted">加载中…</div>
  </main>
</template>

<style scoped>
.month {
  margin: 12px 0 24px;
}
h2 {
  margin: 0 0 8px;
}
h2 span {
  font-weight: 400;
  font-size: var(--fs-xs);
  color: var(--muted);
  margin-left: 8px;
}
</style>
