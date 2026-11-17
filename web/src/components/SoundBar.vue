<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { get } from "../lib/api";
import { isoLocal } from "../lib/track";
import { t } from "../i18n";
import {
  markWrong,
  SOUND_EVENTS,
  type SoundDay,
  type SoundEvent,
  type SoundRun,
} from "../lib/sounds";
import { toast } from "../lib/toast";

// A day's sound events (GET /api/sounds/day) as a sleep app draws its stages: one row per event, lit by the minute
// where the scores said it, a thin line under the minutes that have scores at all; no scores (no recording, not
// scored yet): blank, never quiet. From the first hour with scores to the last. A tap picks the stretch under it (or
// the nearest within NEAR minutes): what it may be, each with 不对, and its conversation, or its first minute to play.
// One bar per device when the day has more than one. Nothing while there are none (the tenant's sound off: none).
const { day } = defineProps<{ day: string }>();
const NEAR = 10;
const router = useRouter();
const days = ref<SoundDay[]>([]);
const picked = ref<{ device: string; run: SoundRun; at: number } | null>(null);
const audio = ref("");
const busy = ref(false); // one 不对 at a time: a double tap would keep two rows, undoing only one
onMounted(() =>
  get<SoundDay[]>("/api/sounds/day", { date: day }).then(
    (d) => (days.value = d),
    () => {},
  ),
);
const t0 = computed(() => Date.parse(day + "T00:00:00")); // the day's local midnight
const span = computed(() => {
  const rs = days.value.flatMap((d) => d.runs);
  if (!rs.length) return { lo: 0, hi: 60 };
  const lo = Math.floor((Math.min(...rs.map((r) => r.from)) - t0.value) / 3_600_000) * 60;
  const hi = Math.ceil((Math.max(...rs.map((r) => r.to)) - t0.value) / 3_600_000) * 60;
  return { lo, hi: Math.max(hi, lo + 60) };
});
const x = (ms: number) => (ms - t0.value) / 60_000 - span.value.lo; // minutes from the left edge
const W = computed(() => span.value.hi - span.value.lo);
const ROW = 10,
  GAP = 4,
  H = SOUND_EVENTS.length * (ROW + GAP) + 3;
const hours = computed(() => {
  const n = W.value / 60,
    step = n <= 6 ? 1 : n <= 12 ? 3 : 6;
  return Array.from({ length: Math.floor(n / step) + 1 }, (_, i) => span.value.lo + i * step * 60);
});
const hh = (m: number) => String(Math.floor(m / 60) % 24).padStart(2, "0") + ":00";
const clock = (ms: number) => isoLocal(ms).slice(11, 16);

function pick(e: MouseEvent, d: SoundDay) {
  const svg = e.currentTarget as SVGSVGElement,
    box = svg.getBoundingClientRect();
  const at = t0.value + (span.value.lo + ((e.clientX - box.left) / box.width) * W.value) * 60_000;
  const dist = (r: SoundRun) => (at < r.from ? r.from - at : at >= r.to ? at - r.to : 0);
  const run = [...d.runs].sort((a, b) => dist(a) - dist(b))[0];
  audio.value = "";
  picked.value =
    run && dist(run) <= NEAR * 60_000
      ? { device: d.device, run, at: Math.max(run.from, Math.min(at, run.to - 60_000)) }
      : null;
}
const what = computed(() =>
  SOUND_EVENTS.filter((e) => picked.value && e in picked.value.run.events),
);
async function wrong(e: SoundEvent) {
  if (busy.value) return;
  busy.value = true;
  const p = picked.value!;
  try {
    p.run.wrong[e] = await markWrong(p.device, p.run.from, p.run.to, e, p.run.conv, p.run.wrong[e]);
  } catch (err) {
    toast((err as Error).message);
  }
  busy.value = false;
}
function go() {
  const p = picked.value!;
  if (p.run.conv)
    void router.push({
      path: "/c/" + p.run.conv,
      query: { tab: "transcript", at: isoLocal(Math.max(p.at, p.run.from)) },
    });
  else
    audio.value = `/api/audio?${new URLSearchParams({ start: isoLocal(p.at), end: isoLocal(p.at + 60_000), device: p.device })}`;
}
</script>

<template>
  <div v-if="days.length" class="card soundbar">
    <div class="head">
      <span>{{ t("sound.title") }}</span
      ><span class="muted">{{ t("sound.hint") }}</span>
    </div>
    <div v-for="d in days" :key="d.device" class="dev">
      <div v-if="days.length > 1" class="name muted">{{ d.device }}</div>
      <div class="grid">
        <div class="labels" :style="{ height: H + 'px' }">
          <span v-for="e in SOUND_EVENTS" :key="e" :style="{ height: ROW + GAP + 'px' }">{{
            t(`sound.ev.${e}`)
          }}</span>
        </div>
        <div class="plot">
          <svg
            :viewBox="`0 0 ${W} ${H}`"
            preserveAspectRatio="none"
            :height="H"
            role="img"
            :aria-label="t('sound.title')"
            @click="pick($event, d)"
          >
            <template v-for="r in d.runs" :key="r.from">
              <rect
                class="cov"
                :x="x(r.from)"
                :y="H - 2"
                :width="(r.to - r.from) / 60_000"
                height="2"
              />
              <template v-for="(e, i) in SOUND_EVENTS" :key="e">
                <rect
                  v-if="e in r.events"
                  :class="[e, { off: r.wrong[e] }]"
                  :x="x(r.from)"
                  :y="i * (ROW + GAP)"
                  :width="(r.to - r.from) / 60_000"
                  :height="ROW"
                />
              </template>
            </template>
            <line
              v-if="picked?.device === d.device"
              class="at"
              :x1="x(picked.at)"
              :x2="x(picked.at)"
              y1="0"
              :y2="H"
              vector-effect="non-scaling-stroke"
            />
          </svg>
          <div class="ticks">
            <span v-for="m in hours" :key="m" :style="{ left: ((m - span.lo) / W) * 100 + '%' }">{{
              hh(m)
            }}</span>
          </div>
        </div>
      </div>
    </div>
    <div v-if="picked" class="pick">
      <div>
        <b>{{ clock(picked.run.from) }}–{{ clock(picked.run.to) }}</b>
        {{ what.length ? t("sound.maybe") : t("sound.nothing") }}
      </div>
      <div class="evs">
        <span v-for="e in what" :key="e" class="snd" :class="{ off: picked.run.wrong[e] }">
          <span class="w">{{ t(`sound.ev.${e}`) }}</span>
          <button
            class="btn-ghost no"
            :aria-pressed="!!picked.run.wrong[e]"
            :disabled="busy"
            @click="wrong(e)"
          >
            {{ picked.run.wrong[e] ? t("sound.undo") : t("sound.wrong") }}
          </button>
        </span>
      </div>
      <button class="btn" @click="go">
        {{ picked.run.conv ? t("sound.open") : t("sound.play") }}
      </button>
      <audio v-if="audio" :src="audio" controls autoplay />
    </div>
  </div>
</template>

<style scoped>
.soundbar {
  --speech: var(--accent);
  --music: #b7791f;
  --vehicle: #4c5fd5;
  --quiet: #8a90a2;
}
@media (prefers-color-scheme: dark) {
  .soundbar {
    --music: #e2b25b;
    --vehicle: #8c9bff;
    --quiet: #7c8296;
  }
}
.head {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: baseline;
  font-weight: 600;
  margin-bottom: var(--space-2);
}
.head .muted {
  font-weight: 400;
  font-size: var(--fs-xs);
}
.name {
  font-size: var(--fs-xs);
  margin-top: var(--space-2);
}
.grid {
  display: flex;
  gap: var(--space-2);
}
.labels {
  display: flex;
  flex-direction: column;
  font-size: 10px;
  line-height: 10px;
  color: var(--muted);
  white-space: nowrap;
}
.plot {
  flex: 1;
  min-width: 0;
}
svg {
  width: 100%;
  display: block;
  cursor: pointer;
}
.cov {
  fill: var(--line);
}
.speech {
  fill: var(--speech);
}
.music {
  fill: var(--music);
}
.vehicle {
  fill: var(--vehicle);
}
.quiet {
  fill: var(--quiet);
}
rect.off {
  opacity: 0.25;
}
.at {
  stroke: var(--fg);
  stroke-width: 1.5;
}
.ticks {
  position: relative;
  height: 16px;
  font-size: 10px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.ticks span {
  position: absolute;
  transform: translateX(-50%);
}
.ticks span:first-child {
  transform: none;
}
.ticks span:last-child {
  transform: translateX(-100%);
}
.pick {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
  font-size: var(--fs-sm);
}
.pick .btn {
  align-self: flex-start;
}
.pick audio {
  width: 100%;
}
.evs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.snd {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  font-size: var(--fs-xs);
  color: var(--fg-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  padding: 0 var(--space-3);
}
.off .w {
  text-decoration: line-through;
  color: var(--muted);
}
.no {
  font-size: var(--fs-xs);
  min-height: 28px;
  padding: 0 0 0 var(--space-2);
  color: var(--accent);
}
</style>
