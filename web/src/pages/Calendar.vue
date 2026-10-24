<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import Icon from "../components/Icon.vue";
import MonthGrid from "../components/MonthGrid.vue";
import Skeleton from "../components/Skeleton.vue";
import { errorTitle } from "../lib/api";
import { calendar, loadCalendar } from "../lib/calendar";

// Every month with sentences, newest first: a day's conversations and speech minutes, a tap opens its conversations.
// loaded: an instance with nothing recorded yet has no months, which is not loading
const router = useRouter();
const loaded = ref(false),
  failed = ref<{ msg: string; title: string } | null>(null);
onMounted(() =>
  loadCalendar(true).then(
    () => (loaded.value = true),
    (e) => (failed.value = { msg: (e as Error).message, title: errorTitle(e) }),
  ),
);
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
    <div v-if="failed" class="empty" :title="failed.title">
      <Icon name="alert-circle" :size="48" />{{ failed.msg }}
    </div>
    <Skeleton v-else-if="!loaded" />
    <div v-else-if="!months.length" class="empty">
      <Icon name="calendar" :size="48" />
      <div>还没有录音。连上手机和录音设备后，每天录了多少会显示在这里</div>
      <RouterLink class="btn btn-primary" to="/connect">连接手机</RouterLink>
    </div>
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
