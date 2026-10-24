<script setup lang="ts">
import { computed, onMounted, ref, useTemplateRef, watch } from "vue";
import Icon from "./Icon.vue";
import MonthGrid from "./MonthGrid.vue";
import { calendar, dayLabel, loadCalendar, neighbour, today } from "../lib/calendar";

// < 10月1日 周三 >: the arrows go to the nearest day with sentences, the date opens a month of them
const props = defineProps<{ day: string }>();
const emit = defineEmits<{ pick: [day: string] }>();
const open = ref(false),
  month = ref(props.day.slice(0, 7)),
  root = useTemplateRef<HTMLElement>("root"),
  at = ref({ left: "0px", top: "0px" });
// fixed under the picker, kept inside the screen wherever the picker sits
function toggle() {
  const r = root.value!.getBoundingClientRect(),
    w = Math.min(340, window.innerWidth - 24);
  at.value = {
    left: Math.max(12, Math.min(r.left, window.innerWidth - w - 12)) + "px",
    top: r.bottom + 6 + "px",
  };
  open.value = !open.value;
}
watch(
  () => props.day,
  (d) => (month.value = d.slice(0, 7)),
);
onMounted(() => loadCalendar());

const prev = computed(() => neighbour(props.day, -1));
const next = computed(() => neighbour(props.day, 1) ?? (props.day < today() ? today() : undefined));
const info = computed(() => calendar.value[props.day]);
function shiftMonth(n: number) {
  const [y, m] = month.value.split("-").map(Number);
  const t = new Date(y, m - 1 + n, 1);
  month.value = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}`;
}
function pick(d: string) {
  open.value = false;
  emit("pick", d);
}
</script>

<template>
  <div ref="root" class="picker">
    <button class="arrow" :disabled="!prev" title="前一天" @click="prev && pick(prev)">
      <Icon name="chevron-left" />
    </button>
    <button class="label" @click="toggle">
      {{ day === today() ? "今天" : dayLabel(day) }}
      <span v-if="info" class="sub">{{ info.conversations }} 段对话</span>
    </button>
    <button class="arrow" :disabled="!next" title="后一天" @click="next && pick(next)">
      <Icon name="chevron-right" />
    </button>
    <slot />
    <div v-if="open" class="pop" :style="at">
      <div class="head">
        <button class="arrow" title="上个月" @click="shiftMonth(-1)">
          <Icon name="chevron-left" />
        </button>
        <span>{{ month.replace("-", " 年 ") }} 月</span>
        <button class="arrow" title="下个月" @click="shiftMonth(1)">
          <Icon name="chevron-right" />
        </button>
        <button class="today" @click="pick(today())">今天</button>
      </div>
      <MonthGrid :month="month" :selected="day" @pick="pick" />
    </div>
  </div>
</template>

<style scoped>
.picker {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
}
.arrow {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 36px;
  padding: 0;
}
.arrow:disabled {
  opacity: 0.4;
}
.label {
  font-weight: 600;
  color: var(--fg);
  white-space: nowrap;
}
.sub {
  font-weight: 400;
  font-size: var(--fs-xs);
  color: var(--muted);
  margin-left: 4px;
}
.pop {
  position: fixed;
  z-index: 5;
  width: min(340px, calc(100vw - 24px));
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 10px;
  box-shadow: var(--shadow);
}
.head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-size: var(--fs-sm);
}
.head .today {
  margin-left: auto;
}
@media (max-width: 600px) {
  .sub {
    display: none;
  }
}
</style>
