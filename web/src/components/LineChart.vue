<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { intl } from "../i18n";

// One measure over time on [lo, hi] (ms; the charts of a page share it); points further apart than `gap` ms are not
// joined. Marks are events drawn as hairlines (solid: `strong`). Hover: the nearest point's value and time.
const props = defineProps<{
  points: { t: number; v: number }[];
  lo: number;
  hi: number;
  unit: string;
  gap: number;
  marks?: { t: number; label: string; strong: boolean }[];
}>();
const H = 150,
  L = 44,
  R = 10,
  T = 10,
  B = 22;
const box = ref<HTMLElement>();
const w = ref(600);
const ro = new ResizeObserver(([e]) => (w.value = e.contentRect.width));
onMounted(() => ro.observe(box.value!));
onBeforeUnmount(() => ro.disconnect());

const range = computed(() => {
  const vs = props.points.map((p) => p.v);
  const a = Math.min(...vs),
    b = Math.max(...vs),
    pad = Math.max((b - a) * 0.15, 1);
  return { a, b, lo: a - pad, hi: b + pad };
});
const x = (t: number) => L + ((t - props.lo) / (props.hi - props.lo)) * (w.value - L - R);
const y = (v: number) =>
  T + (1 - (v - range.value.lo) / (range.value.hi - range.value.lo)) * (H - T - B);
const joined = (i: number) => i > 0 && props.points[i].t - props.points[i - 1].t <= props.gap;
const path = computed(() =>
  props.points
    .map((p, i) => `${joined(i) ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`)
    .join(""),
);
const lone = computed(() =>
  props.points.filter((_, i, a) => !joined(i) && (i + 1 === a.length || !joined(i + 1))),
);
const yTicks = computed(() => {
  const { a, b } = range.value;
  return a === b ? [a] : [a, Math.round((a + b) / 2), b];
});
const pad2 = (n: number) => String(n).padStart(2, "0");
const when = (t: number) => {
  const d = new Date(t);
  const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  return props.hi - props.lo >= 86400_000 ? `${d.getMonth() + 1}-${d.getDate()} ${hm}` : hm;
};
const xTicks = computed(() =>
  (w.value < 500 ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1]).map(
    (f) => props.lo + f * (props.hi - props.lo),
  ),
);

const hover = ref<{ t: number; v: number } | null>(null);
function move(e: PointerEvent) {
  const t = props.lo + ((e.offsetX - L) / (w.value - L - R)) * (props.hi - props.lo);
  let best = props.points[0];
  for (const p of props.points) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  hover.value = best ?? null;
}
</script>

<template>
  <div ref="box" class="chart">
    <svg :width="w" :height="H" @pointermove="move" @pointerleave="hover = null">
      <g class="grid">
        <template v-for="v in yTicks" :key="v">
          <line :x1="L" :x2="w - R" :y1="y(v)" :y2="y(v)" />
          <text :x="L - 6" :y="y(v) + 4" text-anchor="end">{{ v }}</text>
        </template>
        <text
          v-for="(t, i) in xTicks"
          :key="t"
          :x="x(t)"
          :y="H - 6"
          :text-anchor="i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'"
        >
          {{ when(t) }}
        </text>
      </g>
      <line
        v-for="m in marks"
        :key="m.t + m.label"
        :class="['mark', { strong: m.strong }]"
        :x1="x(m.t)"
        :x2="x(m.t)"
        :y1="T"
        :y2="H - B"
      >
        <title>{{ m.label }} {{ new Date(m.t).toLocaleString(intl(), { hour12: false }) }}</title>
      </line>
      <path class="line" :d="path" />
      <circle v-for="p in lone" :key="p.t" class="dot" :cx="x(p.t)" :cy="y(p.v)" r="3" />
      <template v-if="hover">
        <line class="cross" :x1="x(hover.t)" :x2="x(hover.t)" :y1="T" :y2="H - B" />
        <circle class="dot ring" :cx="x(hover.t)" :cy="y(hover.v)" r="4" />
      </template>
    </svg>
    <div v-if="hover" class="tip" :style="{ left: Math.min(x(hover.t) + 8, w - 130) + 'px' }">
      <b>{{ hover.v }} {{ unit }}</b>
      <span>{{ new Date(hover.t).toLocaleString(intl(), { hour12: false }) }}</span>
    </div>
  </div>
</template>

<style scoped>
.chart {
  position: relative;
  touch-action: pan-y;
}
svg {
  display: block;
}
.grid line {
  stroke: var(--line);
}
.grid text {
  fill: var(--muted);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}
.line {
  fill: none;
  stroke: var(--accent);
  stroke-width: 2;
  stroke-linejoin: round;
}
.dot {
  fill: var(--accent);
}
.ring {
  stroke: var(--surface);
  stroke-width: 2;
}
.mark {
  stroke: var(--muted);
  stroke-dasharray: 3 3;
}
.mark.strong {
  stroke: var(--warn);
  stroke-dasharray: none;
}
.cross {
  stroke: var(--fg);
  stroke-opacity: 0.3;
}
.tip {
  position: absolute;
  top: 0;
  pointer-events: none;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  font-size: var(--fs-xs);
  display: flex;
  flex-direction: column;
  white-space: nowrap;
}
.tip span {
  color: var(--muted);
}
</style>
