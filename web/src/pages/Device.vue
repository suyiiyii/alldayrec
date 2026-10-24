<script setup lang="ts">
import { computed, ref, watch } from "vue";
import AppHeader from "../components/AppHeader.vue";
import LineChart from "../components/LineChart.vue";
import {
  CHARGE,
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
const RANGES: [string, number][] = [
  ["6 小时", 6],
  ["24 小时", 24],
  ["3 天", 72],
  ["7 天", 168],
];
const hours = ref(24);
const all = ref(false); // routine records too (heartbeats, connection parameters, ...)
const log = ref<DeviceLog | null>(null);
const ups = ref<Upgrade[]>([]);
const lo = ref(0),
  hi = ref(0);
const local = (t: number) =>
  new Date(t - new Date(t).getTimezoneOffset() * 60000).toISOString().slice(0, 23);
watch(
  [() => props.name, hours],
  async () => {
    document.title = `${props.name} · 设备日志`;
    hi.value = Date.now();
    lo.value = hi.value - hours.value * 3600_000;
    const q = { device: props.name, since: local(lo.value) };
    [log.value, ups.value] = await Promise.all([
      get<DeviceLog>("/api/device-log", q),
      get<Upgrade[]>("/api/upgrades", q),
    ]);
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
        .join("，"),
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
    name: "固件升级",
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
  [BOOT]: "重启",
  [FAULT]: "崩溃",
  [DFU]: "升级",
  [DISCONNECT]: "断开",
};
const marks = computed(() =>
  dated.value
    .map((e) => ({ t: Date.parse(e.at!), code: rec(e).code & ~UPTIME }))
    .filter((m) => NAMES[m.code])
    .map((m) => ({ t: m.t, label: NAMES[m.code], strong: m.code !== DISCONNECT })),
);
// what the audio card shows: a measure of each minute's audio, or the card's write load (SD_WRITES, every 10 min)
const MEASURES: [string, string, (m: AudioMinute) => number | null][] = [
  ["全 0", "s/分钟", (m) => m.zero_s], // runs of exact zeros >= 10 ms: the mic sent nothing
  ["满幅", "次/分钟", (m) => m.full],
  ["电平", "dBFS", (m) => m.rms],
  ["哼声", "dBFS", (m) => m.hum], // mains hum, 50..250 Hz
  ["到达延迟", "s", (m) => m.lag_s], // its end to the server's receiving it
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
const PHONE: [string, string, (s: RecorderStatus) => number | null | undefined][] = [
  ["信号", "dBm", (s) => s.rssi],
  ["待传", "块", (s) => s.queue_n],
  ["手机电量", "%", (s) => s.phone_battery],
  ["App 内存", "MB", (s) => s.app_mb],
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
const secs = (s: number) => (s < 120 ? `${Math.round(s)} 秒` : `${Math.round(s / 60)} 分钟`);
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
          {{ l }}
        </button>
      </span>
    </div>
    <div v-if="!log" class="muted">加载中…</div>
    <template v-else>
      <section class="card">
        <div class="head">
          <b>电池电压</b
          ><span class="muted">mV，录音设备每 10 分钟记一次（LR-P3 用电池时每分钟）</span>
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
        <div v-else class="muted">这段时间没有录音设备的日志（App 读到设备日志后才有）</div>
        <div v-if="marks.length" class="legend muted">
          <span><i class="solid" />重启 / 崩溃 / 升级</span><span><i class="dashed" />断开</span>
        </div>
      </section>
      <section class="card">
        <div class="head">
          <b>电量</b
          ><span class="muted"
            >%，手机连着时每分钟读一次{{
              charge ? `；${CHARGE[charge.charge!]}（${when(charge.at)}）` : ""
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
        <div v-else class="muted">这段时间手机没有上报电量</div>
      </section>
      <section class="card">
        <div class="head">
          <b>录音</b>
          <span class="ranges">
            <button
              v-for="([l], i) in [...MEASURES, ['写卡忙']]"
              :key="i"
              :class="['tag', { on: measure === i }]"
              @click="measure = i"
            >
              {{ l }}
            </button>
          </span>
        </div>
        <div v-if="sound.minutes" class="muted">
          {{ sound.minutes }} 分钟有音频：<span :class="{ bad: sound.zero >= 1 }"
            >全 0 共 {{ sound.zero }} 秒（{{ sound.zeroMinutes }} 分钟有）</span
          >，满幅 {{ sound.full }} 次<template v-if="sound.lag"
            >；到达延迟中位 {{ secs(sound.lag[0]) }}，最长 {{ secs(sound.lag[1]) }}</template
          >
        </div>
        <LineChart
          v-if="heard.length"
          :points="heard"
          :lo="lo"
          :hi="hi"
          :unit="measure < MEASURES.length ? MEASURES[measure][1] : '%'"
          :gap="measure < MEASURES.length ? 3 * 60000 : 30 * 60000"
          :marks="marks"
        />
        <div v-else class="muted">
          {{
            measure < MEASURES.length
              ? "这段时间没有测过的音频（10-07 之前上传的还没补算）"
              : "这段时间没有写卡统计（SD_WRITES，录音设备每 10 分钟记一次）"
          }}
        </div>
      </section>
      <section class="card">
        <div class="head">
          <b>手机和连接</b>
          <span class="ranges">
            <button
              v-for="([l], i) in PHONE"
              :key="i"
              :class="['tag', { on: phoneMeasure === i }]"
              @click="phoneMeasure = i"
            >
              {{ l }}
            </button>
          </span>
        </div>
        <div v-if="phone" class="muted">
          {{ phoneLine(phone) }}（{{ when(phone.at) }}）<br /><span :class="{ bad: hot(phone) }">{{
            phoneState(phone)
          }}</span>
          · <span :class="{ bad: phone.upload_fails }">{{ queueLine(phone) }}</span>
        </div>
        <LineChart
          v-if="phonePoints.length"
          :points="phonePoints"
          :lo="lo"
          :hi="hi"
          :unit="PHONE[phoneMeasure][1]"
          :gap="10 * 60000"
          :marks="marks"
        />
        <div v-else class="muted">这段时间手机没有上报（App 1.4.6 起才有）</div>
      </section>
      <section class="card">
        <div class="head">
          <b>事件</b>
          <label v-if="routine" class="muted"
            ><input v-model="all" type="checkbox" /> 显示例行记录（{{ routine }} 条）</label
          >
        </div>
        <div v-for="r in rows" :key="r.e.src + r.e.seq" :class="['ev', r.level]">
          <span class="at">{{ r.e.at ? when(r.e.at) : "时间不明" }}</span>
          <span class="src">{{
            r.e.src === "rec" ? "设备" : r.e.src === "upgrade" ? "汇总" : "App"
          }}</span>
          <span
            ><b>{{ r.label || r.name }}</b> <span class="detail">{{ r.text }}</span></span
          >
        </div>
        <div v-if="!rows.length" class="muted">这段时间没有事件</div>
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
