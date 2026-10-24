<script setup lang="ts">
import { computed } from "vue";
import { intl, lang, t } from "../i18n";
import { calendar, today } from "../lib/calendar";

// One month, Monday first: each day with sentences shows its conversation count, shaded by speech minutes;
// days without any are not clickable.
const props = defineProps<{ month: string; selected?: string; big?: boolean }>();
const emit = defineEmits<{ pick: [day: string] }>();

const cells = computed(() => {
  const [y, m] = props.month.split("-").map(Number);
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;
  const n = new Date(y, m, 0).getDate();
  return [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length: n }, (_, i) => {
      const day = `${props.month}-${String(i + 1).padStart(2, "0")}`;
      return { day, n: i + 1, info: calendar.value[day] };
    }),
  ];
});
// 0 (none) .. 1 (two hours or more of speech)
const heat = (min: number) => Math.min(1, Math.log1p(min) / Math.log1p(120));
const now = today();
// Monday first: 一..日, or the language's short names (2024-01-01 was a Monday)
const weekdays = computed(() =>
  lang() === "zh"
    ? [..."一二三四五六日"]
    : Array.from({ length: 7 }, (_, i) =>
        new Date(2024, 0, 1 + i).toLocaleDateString(intl(), { weekday: "short" }),
      ),
);
</script>

<template>
  <div class="grid" :class="{ big }">
    <div v-for="(w, i) in weekdays" :key="i" class="wd">{{ w }}</div>
    <template v-for="(c, i) in cells" :key="i">
      <div v-if="!c" />
      <button
        v-else
        class="cell"
        :class="{ on: c.day === selected, today: c.day === now, empty: !c.info }"
        :disabled="!c.info"
        :style="c.info ? { '--heat': heat(c.info.speech_min) } : {}"
        :title="
          c.info
            ? t('calendar.convs', c.info.conversations) +
              ' · ' +
              t('common.min', { n: Math.round(c.info.speech_min) })
            : ''
        "
        @click="emit('pick', c.day)"
      >
        <span class="n">{{ c.n }}</span>
        <span v-if="c.info?.conversations" class="count">{{ c.info.conversations }}</span>
        <span v-if="big && c.info" class="min">{{
          t("monthGrid.min", { n: Math.round(c.info.speech_min) })
        }}</span>
      </button>
    </template>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 3px;
}
.wd {
  font-size: var(--fs-xs);
  color: var(--muted);
  text-align: center;
}
.cell {
  position: relative;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 2px 0 0;
  min-height: 40px;
  display: flex;
  flex-direction: column;
  align-items: center;
  color: var(--fg);
  background: color-mix(in srgb, var(--accent) calc(var(--heat, 0) * 22%), var(--surface));
}
.cell.empty {
  color: var(--muted);
  background: var(--surface);
  cursor: default;
}
.cell.today .n {
  font-weight: 700;
  color: var(--accent);
}
.cell.on {
  border-color: var(--accent);
}
.n {
  font-size: var(--fs-sm);
  line-height: 1.3;
}
.count {
  font-size: var(--fs-xs);
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.big .cell {
  min-height: 58px;
}
.min {
  font-size: var(--fs-xs);
  color: var(--muted);
}
</style>
