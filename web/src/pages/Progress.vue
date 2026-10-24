<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Listen from "../components/Listen.vue";
import {
  CHARGE,
  build,
  get,
  holdLeft,
  hot,
  phoneLine,
  phoneState,
  queueLine,
  type Calls,
  type CallSum,
  type DeviceStatus,
  type RecorderStatus,
  type Status,
  type Upgrade,
} from "../lib/api";
import { upgradeText } from "../lib/recorderLog";
import { useSocket } from "../lib/socket";

// What the server is doing per device: how far its audio has arrived, how far it is finalized, what is left and
// when it should be done; refreshed by the status the socket sends every 2 s.
const status = ref<Status | null>(null);
onMounted(async () => (status.value = await get<Status>("/api/status")));
useSocket(
  (m) => {
    if (m.type === "status") status.value = m;
  },
  async () => (status.value = await get<Status>("/api/status")),
);

// what transcription and the AI calls did today, and the money left for them; every minute
const calls = ref<Calls | null>(null);
const today = () => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
};
const loadCalls = async () => (calls.value = await get<Calls>("/api/calls", { since: today() }));
let tick = 0;
onMounted(() => {
  loadCalls();
  tick = window.setInterval(loadCalls, 60_000);
});
onUnmounted(() => clearInterval(tick));
const KINDS: Record<string, string> = {
  final: "定稿",
  draft: "初稿",
  summary: "摘要",
  hints: "认人提示",
  profile: "人物档案",
  recap: "每日回顾",
  live: "实时分析",
  ask: "问 AI",
};
const secs = (ms: number | null) => (ms == null ? "—" : `${(ms / 1000).toFixed(1)} s`);
const callLine = (c: CallSum) => {
  const failed = Object.entries(c.failed);
  const nf = failed.reduce((s, [, n]) => s + n, 0);
  return [
    `${c.n} 次`,
    nf ? `失败 ${nf}（${failed.map(([w, n]) => `${w} ×${n}`).join("，")}）` : "",
    `耗时中位 ${secs(c.ms50)}，慢的 ${secs(c.ms90)}`,
    c.sent_h
      ? `送出 ${c.sent_h} 小时${c.engine === "volc" ? `（约 ${Math.round(c.sent_h * 450)} AFP）` : ""}`
      : "",
    c.prompt_tokens ? `token 入 ${c.prompt_tokens} 出 ${c.completion_tokens}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
};
const balance = computed(() => {
  const b = calls.value?.balance ?? status.value?.balance;
  if (!b) return null;
  const rate = b.per_day == null ? "" : `，近 7 天每天约 ¥${b.per_day}`;
  const left = b.days_left == null ? "" : `，约 ${b.days_left} 天后用完`;
  return {
    text: `¥${b.cny}（${b.at.slice(11, 16)} 查）${rate}${left}`,
    low: b.days_left != null && b.days_left < 3,
  };
});

const devices = computed(() =>
  [...(status.value?.devices ?? [])].sort((a, b) =>
    (a.received_to ?? "") < (b.received_to ?? "") ? 1 : -1,
  ),
);
// each device's last firmware update in 30 days, looked up when the devices change
const ups = ref<Record<string, Upgrade | undefined>>({});
watch(
  () => devices.value.map((d) => d.device).join("\n"),
  async (names) => {
    const since = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10);
    for (const device of names ? names.split("\n") : [])
      ups.value[device] = (await get<Upgrade[]>("/api/upgrades", { device, since })).at(-1);
  },
);
// an app older than the newest build any phone runs or /app.apk has (both platforms share the build number): the
// iPhone that stayed on 1.3.0 for days
const apk = ref(0);
onMounted(async () => {
  apk.value = await get<{ versionCode: number }>("/api/app/latest").then(
    (a) => a.versionCode,
    () => 0, // no APK on this server
  );
});
const newest = computed(() => Math.max(apk.value, ...devices.value.map((d) => build(d.hw?.app))));
const outdated = (d: DeviceStatus) => !!d.hw?.app && build(d.hw.app) < newest.value;
const at = (iso: string | null) => (iso ? `${iso.slice(5, 10)} ${iso.slice(11, 16)}` : "—");
function span(s: number) {
  const m = Math.round(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)} 小时 ${m % 60} 分` : `${m} 分钟`;
}
// store is an errno; with nothing mounted this boot (total_kb 0) no card or one not seated (-19, -116: a mount that
// timed out), as the app's cardState says it
const card = (h: RecorderStatus) =>
  h.store === -28
    ? "已满，停止离线录音"
    : h.store === -16
      ? "U 盘模式，离线录音暂停"
      : h.store && !h.total_kb
        ? "没插卡或没插好，只能实时上传"
        : h.store
          ? "出错，重插或换卡"
          : h.total_kb
            ? `剩 ${((h.free_kb ?? 0) / 1048576).toFixed(1)} / ${(h.total_kb / 1048576).toFixed(1)} GB`
            : "—";
const caughtUp = (d: DeviceStatus) => d.left_s < 30;
// Held: the phone reads older audio off the recorder's card in the background while it sends what is said now;
// finals wait at the hold for it, so the server's own estimate does not apply
const held = (d: DeviceStatus) => {
  const kb = d.hw?.backlog_kb,
    eta = d.hw?.backlog_eta_s;
  if (holdLeft(d)) return `定稿停在 ${at(d.hold!)}，等卡里更早的录音；${holdLeft(d)}`;
  return `定稿停在 ${at(d.hold!)}，等手机在后台补传卡里更早的录音${kb ? `（还有 ${(kb / 1024).toFixed(1)} MB${eta ? `，约 ${span(eta)}` : ""}）` : ""}，补完再接着定稿；初稿照常`;
};
// how late its newest audio came (a backlog blocking what is said now), how its last 10 min sounded
const arrived = (d: DeviceStatus) =>
  d.arrival
    ? `${d.arrival.lag_s < 120 ? `${Math.round(d.arrival.lag_s)} 秒` : span(d.arrival.lag_s)}（最新一块 ${new Date(d.arrival.at).toTimeString().slice(0, 5)} 收到）`
    : "—";
const noisy = (d: DeviceStatus) => !!d.sound && (d.sound.zero_s >= 1 || d.sound.full >= 50);
const sound = (d: DeviceStatus) =>
  d.sound
    ? `${noisy(d) ? `全 0 ${d.sound.zero_s.toFixed(1)} 秒 · 满幅 ${d.sound.full} 次` : "正常"}（最近 ${Math.round(d.sound.s / 60)} 分钟音频）`
    : "—";
const eta = (d: DeviceStatus) => {
  if (!d.eta_s) return "—";
  const t = new Date(Date.now() + d.eta_s * 1000);
  return `约 ${span(d.eta_s)}（${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}）`;
};
</script>

<template>
  <AppHeader />
  <main>
    <section v-for="d in devices" :key="d.device" class="card">
      <div class="top">
        <span
          ><b>{{ d.device }}</b>
          <RouterLink :to="`/device/${encodeURIComponent(d.device)}`">日志</RouterLink></span
        >
        <span :class="caughtUp(d) ? 'ok' : 'busy'">{{
          caughtUp(d) ? "已追上" : d.hold ? "等补传" : "处理中"
        }}</span>
      </div>
      <div class="grid">
        <span>已收到</span><span>{{ at(d.received_to) }}</span> <span>已定稿</span
        ><span>{{ at(d.done_to) }}</span> <span>待处理</span
        ><span>{{ caughtUp(d) ? "—" : span(d.left_s) + " 录音" }} · 排队 {{ d.queued }} 块</span>
        <template v-if="d.hold"
          ><span>补传</span><span>{{ held(d) }}</span></template
        >
        <span>速度</span><span>{{ d.rate ? `${d.rate} 倍实时` : "—" }}</span> <span>预计完成</span
        ><span>{{ caughtUp(d) ? "—" : d.hold ? "补传完之后" : eta(d) }}</span> <span>最近一轮</span
        ><span :class="{ bad: d.refine?.error }">{{
          d.refine?.error
            ? "失败：" + d.refine.error
            : d.refine
              ? `${d.refine.at.slice(11)} · ${(d.refine.ms / 1000).toFixed(0)} s`
              : "—"
        }}</span>
        <span>对话</span
        ><span>{{
          d.conversation
            ? `进行中，${d.conversation.start.slice(11, 16)} 起 · ${d.conversation.sentences} 句`
            : "无"
        }}</span>
        <span>实时延迟</span
        ><span :class="{ bad: (d.arrival?.lag_s ?? 0) > 300 }">{{ arrived(d) }}</span>
        <span>录音质量</span><span :class="{ bad: noisy(d) }">{{ sound(d) }}</span>
        <template v-if="d.hw">
          <span>录音设备</span
          ><span>{{ [d.hw.model, d.hw.fw && "固件 " + d.hw.fw].filter(Boolean).join(" · ") }}</span>
          <span>电量</span
          ><span>{{
            [d.hw.battery != null && d.hw.battery + "%", d.hw.charge && CHARGE[d.hw.charge]]
              .filter(Boolean)
              .join(" · ") || "—"
          }}</span>
          <span>存储卡</span><span :class="{ bad: d.hw.store }">{{ card(d.hw) }}</span>
          <template v-if="d.hw.rssi != null"
            ><span>信号</span><span>{{ d.hw.rssi }} dBm</span></template
          >
          <template v-if="d.hw.app">
            <span>手机</span
            ><span :class="{ bad: outdated(d) }"
              >{{ phoneLine(d.hw)
              }}{{ outdated(d) ? `（不是最新，最新 build ${newest}）` : "" }}</span
            >
            <span>手机状态</span
            ><span :class="{ bad: hot(d.hw) }">{{ phoneState(d.hw) || "—" }}</span> <span>上传</span
            ><span :class="{ bad: d.hw.upload_fails }">{{ queueLine(d.hw) }}</span>
          </template>
          <span>上报于</span><span>{{ at(d.hw.at) }}</span>
        </template>
        <template v-if="ups[d.device]">
          <span>最近升级</span
          ><span :class="{ bad: ups[d.device]!.ok === false }"
            >{{ at(ups[d.device]!.at) }} {{ upgradeText(ups[d.device]!) }}</span
          >
        </template>
      </div>
      <Listen class="listen" :device="d.device" />
    </section>
    <section v-if="calls" class="card">
      <div class="top"><b>转写和 AI 调用</b><span class="muted">今天</span></div>
      <div v-if="balance" class="call">
        <b>StepFun 余额</b> <span :class="{ bad: balance.low }">{{ balance.text }}</span>
      </div>
      <div v-for="c in calls.calls" :key="c.kind + c.engine" class="call">
        <b>{{ KINDS[c.kind] ?? c.kind }}</b> <span class="muted">{{ c.engine }}</span>
        <span :class="{ bad: Object.keys(c.failed).length }">{{ callLine(c) }}</span>
      </div>
      <div v-if="!calls.calls.length" class="muted">今天还没有调用</div>
    </section>
    <section v-if="status" class="card">
      <div class="top"><b>摘要</b></div>
      <div class="grid">
        <span>等待生成</span><span>{{ status.summaries_pending ?? 0 }} 段对话</span>
      </div>
    </section>
    <div v-if="status && !devices.length" class="muted">还没有设备的处理任务</div>
    <div v-if="!status" class="muted">加载中…</div>
  </main>
</template>

<style scoped>
.top {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}
main {
  padding-top: var(--space-3);
}
.ok {
  color: var(--ok);
  font-size: var(--fs-sm);
}
.busy {
  color: var(--warn);
  font-size: var(--fs-sm);
}
.grid {
  display: grid;
  grid-template-columns: 5em 1fr;
  gap: 2px 12px;
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
}
.listen {
  margin-top: 8px;
}
.call {
  font-size: var(--fs-sm);
  padding: 2px 0;
  font-variant-numeric: tabular-nums;
}
.call b,
.call .muted {
  margin-right: 6px;
}
.grid span:nth-child(odd) {
  color: var(--muted);
}
</style>
