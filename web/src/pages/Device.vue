<script setup lang="ts">
import { computed, ref, watch } from "vue";
import AppHeader from "../components/AppHeader.vue";
import LineChart from "../components/LineChart.vue";
import Skeleton from "../components/Skeleton.vue";
import { t } from "../i18n";
import {
  chargeText,
  get,
  hot,
  phoneLine,
  phoneState,
  queueLine,
  type AudioMinute,
  type DeviceEvent,
  type DeviceLog,
  type RecorderStatus,
  type Upgrade,
} from "../lib/api";
import {
  BOOT,
  DFU,
  DISCONNECT,
  FAULT,
  UPTIME,
  cardBusy,
  describe,
  millivolts,
  upgradeText,
  type Rec,
  type Said,
} from "../lib/recorderLog";

// One device's history: its battery (the mV its recorder logs every 10 min, the % its phone reads every minute), how
// its audio sounded and arrived (a point a minute, the server's measure of each piece: levels only), and what
// happened, the recorder's own event log and the app's, newest first, with each firmware update in a line.
const props = defineProps<{ name: string }>();
const RANGES: [() => string, number][] = [
  [() => t("common.hours", { n: 6 }), 6],
  [() => t("common.hours", { n: 24 }), 24],
  [() => t("device.days", { n: 3 }, 3), 72],
  [() => t("device.days", { n: 7 }, 7), 168],
];
const hours = ref(24);
const all = ref(false); // routine records too (heartbeats, connection parameters, ...)
const log = ref<DeviceLog | null>(null);
const ups = ref<Upgrade[]>([]);
const lo = ref(0),
  hi = ref(0);
const local = (t: number) =>
  new Date(t - new Date(t).getTimezoneOffset() * 60000).toISOString().slice(0, 23);
let seq = 0;
watch(
  [() => props.name, hours],
  async () => {
    document.title = `${props.name} · ${t("title.device")}`;
    const my = ++seq;
    hi.value = Date.now();
    lo.value = hi.value - hours.value * 3600_000;
    const q = { device: props.name, since: local(lo.value) };
    const got = await Promise.all([
      get<DeviceLog>("/api/device-log", q),
      get<Upgrade[]>("/api/upgrades", q),
    ]);
    if (my === seq) [log.value, ups.value] = got; // a range picked meanwhile: its answer, not this older one
  },
  { immediate: true },
);

const rec = (e: DeviceEvent) => e.data as unknown as Rec;
function said(e: DeviceEvent): Said {
  if (e.src === "rec") return describe(rec(e));
  const { kind, text, level, ...rest } = e.data as { kind?: string; text?: string; level?: string };
  return {
    name: kind ?? "app",
    label: "",
    text:
      text ??
      Object.entries(rest)
        .map(([k, v]) => `${k} ${JSON.stringify(v)}`)
        .join(t("device.sep")),
    level: level === "error" ? "bad" : level === "warn" ? "warn" : "",
    routine: kind === "foreground" || kind === "background", // the app in front or not, tens a day
  };
}
const rows = computed(() => {
  const ev = log.value?.events ?? [];
  // a record its phone could not date sorts right after the one before it in the recorder's own order
  const key = new Map<DeviceEvent, string>();
  let last = "";
  for (const e of ev.filter((e) => e.src === "rec").sort((a, b) => a.seq - b.seq))
    key.set(e, (last = e.at ?? last));
  // an update's line just above its start
  const up = ups.value.map((u, i) => ({
    e: { src: "upgrade", seq: Number.MAX_SAFE_INTEGER - i, at: u.at, data: {} },
    key: u.at,
    name: t("device.upgrade"),
    label: "",
    text: upgradeText(u),
    level: (u.ok ? "" : u.ok == null ? "warn" : "bad") as Said["level"],
    routine: false,
  }));
  return ev
    .map((e) => ({ e, key: key.get(e) ?? e.at ?? "", ...said(e) }))
    .concat(up)
    .filter((r) => all.value || !r.routine)
    .sort((a, b) => (a.key !== b.key ? (a.key < b.key ? 1 : -1) : b.e.seq - a.e.seq));
});
const routine = computed(() => (log.value?.events ?? []).filter((e) => said(e).routine).length);

const dated = computed(() => (log.value?.events ?? []).filter((e) => e.src === "rec" && e.at));
const mv = computed(() =>
  dated.value
    .map((e) => ({ t: Date.parse(e.at!), v: millivolts(rec(e)) }))
    .filter((p) => p.v > 0)
    .sort((a, b) => a.t - b.t),
);
const pct = computed(() =>
  (log.value?.status ?? [])
    .filter((s) => s.battery != null)
    .map((s) => ({ t: Date.parse(s.at), v: s.battery! })),
);
// what it runs on, as its phone last read it
const charge = computed(() => (log.value?.status ?? []).filter((s) => s.charge).pop());
const NAMES: Record<number, string> = {
  [BOOT]: "device.mark.boot",
  [FAULT]: "device.mark.fault",
  [DFU]: "device.mark.dfu",
  [DISCONNECT]: "device.mark.disconnect",
};
const marks = computed(() =>
  dated.value
    .map((e) => ({ t: Date.parse(e.at!), code: rec(e).code & ~UPTIME }))
    .filter((m) => NAMES[m.code])
    .map((m) => ({ t: m.t, label: t(NAMES[m.code]), strong: m.code !== DISCONNECT })),
);
// what the audio card shows: a measure of each minute's audio, or the card's write load (SD_WRITES, every 10 min);
// [its name's key, its unit, its value]
const MEASURES: [string, () => string, (m: AudioMinute) => number | null][] = [
  ["device.m.zero", () => t("device.unit.sPerMin"), (m) => m.zero_s], // runs of exact zeros >= 10 ms: the mic sent nothing
  ["device.m.full", () => t("device.unit.perMin"), (m) => m.full],
  ["device.m.rms", () => "dBFS", (m) => m.rms],
  ["device.m.hum", () => "dBFS", (m) => m.hum], // mains hum, 50..250 Hz
  ["device.m.lag", () => "s", (m) => m.lag_s], // its end to the server's receiving it
];
const measure = ref(0); // MEASURES.length: the card's write load
const points = (ps: { t: number; v: number | null }[]) =>
  ps.filter((p): p is { t: number; v: number } => p.v != null).sort((a, b) => a.t - b.t);
const heard = computed(() =>
  measure.value < MEASURES.length
    ? points(
        (log.value?.audio ?? []).map((m) => ({
          t: Date.parse(m.t),
          v: MEASURES[measure.value][2](m),
        })),
      )
    : points(dated.value.map((e) => ({ t: Date.parse(e.at!), v: cardBusy(rec(e)) }))),
);
const sound = computed(() => {
  const a = log.value?.audio ?? [];
  const lags = a.flatMap((m) => (m.lag_s == null ? [] : [m.lag_s])).sort((x, y) => x - y);
  return {
    minutes: a.length,
    zero: Math.round(a.reduce((s, m) => s + m.zero_s, 0) * 10) / 10,
    zeroMinutes: a.filter((m) => m.zero_s > 0).length,
    full: a.reduce((s, m) => s + m.full, 0),
    lag: lags.length ? [lags[lags.length >> 1], lags[lags.length - 1]] : null,
  };
});
// its phone and the link, from the status its phone sends every minute (App 1.4.6 on): the newest one says what
// the phone runs and how its uploads go, the chart one measure over time
const PHONE: [string, () => string, (s: RecorderStatus) => number | null | undefined][] = [
  ["device.p.rssi", () => "dBm", (s) => s.rssi],
  ["device.p.queue", () => t("device.unit.pieces"), (s) => s.queue_n],
  ["device.p.battery", () => "%", (s) => s.phone_battery],
  ["device.p.memory", () => "MB", (s) => s.app_mb],
];
const phoneMeasure = ref(0);
const phone = computed(() => (log.value?.status ?? []).filter((s) => s.app).pop());
const phonePoints = computed(() =>
  points(
    (log.value?.status ?? []).map((s) => ({
      t: Date.parse(s.at),
      v: PHONE[phoneMeasure.value][2](s) ?? null,
    })),
  ),
);
const secs = (s: number) =>
  s < 120 ? t("common.sec", { n: Math.round(s) }) : t("common.min", { n: Math.round(s / 60) });
const when = (at: string) => (hours.value >= 24 ? `${at.slice(5, 10)} ` : "") + at.slice(11, 19);
</script>

<template>
  <AppHeader />
  <main>
    <div class="top">
      <h2>{{ name }}</h2>
      <span class="ranges">
        <button
          v-for="[l, h] in RANGES"
          :key="h"
          :class="['tag', { on: hours === h }]"
          @click="hours = h"
        >
          {{ l() }}
        </button>
      </span>
    </div>
    <Skeleton v-if="!log" />
    <template v-else>
      <section class="card">
        <div class="head">
          <b>{{ t("device.voltage") }}</b
          ><span class="muted">{{ t("device.voltageSub") }}</span>
        </div>
        <LineChart
          v-if="mv.length"
          :points="mv"
          :lo="lo"
          :hi="hi"
          unit="mV"
          :gap="30 * 60000"
          :marks="marks"
        />
        <div v-else class="muted">{{ t("device.noRecLog") }}</div>
        <div v-if="marks.length" class="legend muted">
          <span><i class="solid" />{{ t("device.legendStrong") }}</span
          ><span><i class="dashed" />{{ t("device.mark.disconnect") }}</span>
        </div>
      </section>
      <section class="card">
        <div class="head">
          <b>{{ t("device.battery") }}</b
          ><span class="muted"
            >{{ t("device.batterySub")
            }}{{
              charge
                ? t("device.chargeAt", { charge: chargeText(charge.charge!), at: when(charge.at) })
                : ""
            }}</span
          >
        </div>
        <LineChart
          v-if="pct.length"
          :points="pct"
          :lo="lo"
          :hi="hi"
          unit="%"
          :gap="10 * 60000"
          :marks="marks"
        />
        <div v-else class="muted">{{ t("device.noBattery") }}</div>
      </section>
      <section class="card">
        <div class="head">
          <b>{{ t("device.audio") }}</b>
          <span class="ranges">
            <button
              v-for="([l], i) in [...MEASURES, ['device.m.busy']]"
              :key="i"
              :class="['tag', { on: measure === i }]"
              @click="measure = i"
            >
              {{ t(l as string) }}
            </button>
          </span>
        </div>
        <div v-if="sound.minutes" class="muted">
          {{ t("device.soundMinutes", { n: sound.minutes })
          }}<span :class="{ bad: sound.zero >= 1 }">{{
            t("device.soundZero", { s: sound.zero, n: sound.zeroMinutes })
          }}</span
          >{{ t("device.soundFull", { n: sound.full }, sound.full)
          }}<template v-if="sound.lag">{{
            t("device.soundLag", { mid: secs(sound.lag[0]), max: secs(sound.lag[1]) })
          }}</template>
        </div>
        <LineChart
          v-if="heard.length"
          :points="heard"
          :lo="lo"
          :hi="hi"
          :unit="measure < MEASURES.length ? MEASURES[measure][1]() : '%'"
          :gap="measure < MEASURES.length ? 3 * 60000 : 30 * 60000"
          :marks="marks"
        />
        <div v-else class="muted">
          {{ measure < MEASURES.length ? t("device.noAudio") : t("device.noCardWrites") }}
        </div>
      </section>
      <section class="card">
        <div class="head">
          <b>{{ t("device.phone") }}</b>
          <span class="ranges">
            <button
              v-for="([l], i) in PHONE"
              :key="i"
              :class="['tag', { on: phoneMeasure === i }]"
              @click="phoneMeasure = i"
            >
              {{ t(l) }}
            </button>
          </span>
        </div>
        <div v-if="phone" class="muted">
          {{ t("device.phoneAt", { line: phoneLine(phone), at: when(phone.at) }) }}<br /><span
            :class="{ bad: hot(phone) }"
            >{{ phoneState(phone) }}</span
          >
          · <span :class="{ bad: phone.upload_fails }">{{ queueLine(phone) }}</span>
        </div>
        <LineChart
          v-if="phonePoints.length"
          :points="phonePoints"
          :lo="lo"
          :hi="hi"
          :unit="PHONE[phoneMeasure][1]()"
          :gap="10 * 60000"
          :marks="marks"
        />
        <div v-else class="muted">{{ t("device.noPhone") }}</div>
      </section>
      <section class="card">
        <div class="head">
          <b>{{ t("device.events") }}</b>
          <label v-if="routine" class="muted"
            ><input v-model="all" type="checkbox" />
            {{ t("device.showRoutine", { n: routine }) }}</label
          >
        </div>
        <div v-for="r in rows" :key="r.e.src + r.e.seq" :class="['ev', r.level]">
          <span class="at">{{ r.e.at ? when(r.e.at) : t("device.noTime") }}</span>
          <span class="src">{{
            r.e.src === "rec"
              ? t("device.srcRec")
              : r.e.src === "upgrade"
                ? t("device.srcUpgrade")
                : "App"
          }}</span>
          <span
            ><b>{{ r.label || r.name }}</b> <span class="detail">{{ r.text }}</span></span
          >
        </div>
        <div v-if="!rows.length" class="muted">{{ t("device.noEvents") }}</div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
}
h2 {
  margin: 0;
}
.ranges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.card {
  margin: 12px 0;
  font-size: var(--fs-sm);
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}
.legend {
  display: flex;
  gap: 16px;
  font-size: var(--fs-xs);
}
.legend i {
  display: inline-block;
  width: 14px;
  vertical-align: middle;
  margin-right: 4px;
  border-top: 1px solid var(--warn);
}
.legend i.dashed {
  border-top: 1px dashed var(--muted);
}
.ev {
  display: grid;
  grid-template-columns: auto 2.5em 1fr;
  align-items: baseline;
  gap: 2px 10px;
  padding: 3px 0;
  border-top: 1px solid var(--line);
  font-variant-numeric: tabular-nums;
}
.ev:lang(en) {
  grid-template-columns: auto 3.6em 1fr; /* Device, Update, App */
}
.at,
.src,
.detail {
  color: var(--muted);
}
.ev > span:last-child {
  min-width: 0;
  overflow-wrap: anywhere; /* a crash's class and frames are one long word */
}
.ev.warn b {
  color: var(--warn);
}
.ev.bad b,
.ev.bad .detail {
  color: var(--err);
}
</style>
