<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useTemplateRef, watch } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Listen from "../components/Listen.vue";
import Skeleton from "../components/Skeleton.vue";
import { t } from "../i18n";
import {
  caseText,
  chargeText,
  build,
  card,
  caughtUp,
  engines,
  get,
  hhmm,
  hhmmss,
  holdLeft,
  hot,
  phoneLine,
  phoneState,
  queueLine,
  type Calls,
  type CallSum,
  type DeviceStatus,
  type LogEvent,
  type Status,
  type Upgrade,
} from "../lib/api";
import { upgradeText } from "../lib/recorderLog";
import { useSocket } from "../lib/socket";
import { importLine, showLiveLag } from "../lib/progress";

// What the server is doing per device: how far its audio has arrived, how far it is finalized, what is left and
// when it should be done; refreshed by the status the socket sends every 2 s.
const status = ref<Status | null>(null);
onMounted(async () => (status.value = await get<Status>("/api/status")));
// the server's log lines (pipelines, engines, errors), the last 300, the newest at the bottom of its box
const events = ref<LogEvent[]>([]),
  logBox = useTemplateRef<HTMLElement>("logBox");
async function addEvents(es: LogEvent[]) {
  events.value = events.value.concat(es).slice(-300);
  await nextTick();
  if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight;
}
onMounted(async () => addEvents(await get<LogEvent[]>("/api/events")));
useSocket(
  (m) => {
    if (m.type === "status") status.value = m;
    else if (m.type === "event") addEvents([m]);
  },
  async () => (status.value = await get<Status>("/api/status")),
);

// The whole server, what the live page's status line showed before it was cut to 录音中 / 未在录音: the recording
// device, how far behind finals and the screen are, the queue, the speaker pass, the conversation open, the build.
// With none recording (no audio for 2 min), when the last one did instead of a "behind" that only counts the time
// since (10-07: 落后 13408 秒 at 01:06, nothing recording); the queue is the recording device's own
const when = (iso?: string | null) =>
  !iso
    ? "—"
    : iso.slice(0, 10) === new Date().toLocaleDateString("sv")
      ? hhmm(iso)
      : `${iso.slice(5, 10)} ${hhmm(iso)}`;
const topQueued = computed(
  () =>
    status.value?.devices?.find((d) => d.device === status.value?.device)?.queued ??
    status.value?.queued ??
    0,
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
const KINDS = ["final", "draft", "late", "summary", "split", "hints", "profile", "recap", "live", "ask"];
const kind = (k: string) => (KINDS.includes(k) ? t(`progress.kind.${k}`) : k);
const secs = (ms: number | null) => (ms == null ? "—" : `${(ms / 1000).toFixed(1)} s`);
const sec = (n?: number | null) => (n == null ? "—" : t("common.sec", { n }));
const callLine = (c: CallSum) => {
  const failed = Object.entries(c.failed);
  const nf = failed.reduce((s, [, n]) => s + n, 0);
  return [
    t("progress.calls", c.n),
    nf
      ? t("progress.failed", {
          n: nf,
          list: failed.map(([w, n]) => `${w} ×${n}`).join(t("progress.failedSep")),
        })
      : "",
    t("progress.timing", { p50: secs(c.ms50), p90: secs(c.ms90) }),
    c.sent_h
      ? t("progress.sent", { h: c.sent_h }) +
        (c.engine === "volc" ? t("progress.afp", { n: Math.round(c.sent_h * 450) }) : "")
      : "",
    c.prompt_tokens ? t("progress.tokens", { in: c.prompt_tokens, out: c.completion_tokens }) : "",
  ]
    .filter(Boolean)
    .join(" · ");
};
const balance = computed(() => {
  const b = calls.value?.balance ?? status.value?.balance;
  if (!b) return null;
  const rate = b.per_day == null ? "" : t("progress.perDay", { n: b.per_day });
  const left = b.days_left == null ? "" : t("progress.daysLeft", b.days_left);
  return {
    text: t("progress.balance", { cny: b.cny, at: b.at.slice(11, 16), rate, left }),
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
  return m >= 60
    ? t("common.hourMin", { h: Math.floor(m / 60), m: m % 60 })
    : t("common.min", { n: m });
}
// Held: the phone reads older audio off the recorder's card in the background while it sends what is said now;
// finals wait at the hold for it, so the server's own estimate does not apply
const held = (d: DeviceStatus) => {
  const kb = d.hw?.backlog_kb,
    eta = d.hw?.backlog_eta_s;
  if (holdLeft(d)) return t("progress.heldGone", { at: at(d.hold!), why: holdLeft(d) });
  const left = kb
    ? t("progress.heldLeft", {
        mb: (kb / 1024).toFixed(1),
        eta: eta ? t("progress.heldEta", { span: span(eta) }) : "",
      })
    : "";
  return t("progress.held", { at: at(d.hold!), left });
};
// how late its newest audio came (a backlog blocking what is said now), how its last 10 min sounded
const arrived = (d: DeviceStatus) =>
  d.arrival
    ? t("progress.arrived", {
        lag:
          d.arrival.lag_s < 120
            ? t("common.sec", { n: Math.round(d.arrival.lag_s) })
            : span(d.arrival.lag_s),
        at: new Date(d.arrival.at).toTimeString().slice(0, 5),
      })
    : "—";
const noisy = (d: DeviceStatus) => !!d.sound && (d.sound.zero_s >= 1 || d.sound.full >= 50);
const sound = (d: DeviceStatus) =>
  d.sound
    ? t("progress.sound", {
        state: noisy(d)
          ? t("progress.soundNoisy", { z: d.sound.zero_s.toFixed(1), n: d.sound.full })
          : t("progress.soundOk"),
        m: Math.round(d.sound.s / 60),
      })
    : "—";
const eta = (d: DeviceStatus) => {
  if (!d.eta_s) return "—";
  const end = new Date(Date.now() + d.eta_s * 1000);
  const hm = `${String(end.getHours()).padStart(2, "0")}:${String(end.getMinutes()).padStart(2, "0")}`;
  return t("progress.eta", { span: span(d.eta_s), at: hm });
};
const lateLine = (d: DeviceStatus) =>
  Object.entries(d.late ?? {})
    .filter(([, v]) => v.spans > 0)
    .map(([s, v]) => t(`progress.late.${s}`, { n: v.spans, m: v.minutes.toFixed(1) }, v.spans) + (v.note ? ` (${v.note})` : ""))
    .join(" · ");
</script>

<template>
  <AppHeader />
  <main>
    <section v-if="status" class="card">
      <div class="top">
        <b>{{ t("progress.overall") }}</b
        ><span :class="status.recording === false ? 'muted' : 'ok'">{{
          t(status.recording === false ? "progress.notRecording" : "progress.recording")
        }}</span>
      </div>
      <div class="grid">
        <template v-if="status.recording === false"
          ><span>{{ t("progress.lastAudio") }}</span
          ><span>{{ when(status.last_audio) }}</span></template
        >
        <template v-else>
          <span>{{ t("progress.device") }}</span
          ><span>{{ status.device || t("progress.waitingAudio") }}</span>
          <span>{{ t("progress.arrival") }}</span
          ><span :class="{ bad: (status.arrival?.lag_s ?? 0) > 30 }">{{
            t("progress.arrivalVal", { v: sec(status.arrival?.lag_s) })
          }}</span>
          <span>{{ t("progress.draft") }}</span
          ><span :class="{ bad: status.drafting && (status.latency_s ?? 0) > 30 }">{{
            status.drafting
              ? t("progress.draftVal", { v: sec(status.latency_s), m: sec(status.latency_med_s) })
              : t("progress.draftOff")
          }}</span>
          <span>{{ t("progress.final") }}</span
          ><span :class="{ bad: (status.behind_s ?? 0) > 30 }">{{
            t("progress.finalVal", { v: sec(status.final_latency_s), b: sec(status.behind_s) })
          }}</span>
        </template>
        <span>{{ t("progress.queued") }}</span
        ><span :class="{ bad: topQueued }">{{ t("progress.queuedVal", topQueued) }}</span>
        <span>{{ t("progress.speakers") }}</span
        ><span :class="{ bad: status.refine?.error }">{{
          status.refine
            ? status.refine.error
              ? t("progress.refineFailed", { e: status.refine.error })
              : t("progress.refineLast", {
                  at: status.refine.at.slice(11),
                  s: (status.refine.ms / 1000).toFixed(0),
                })
            : t("progress.refineWaiting")
        }}</span>
        <span>{{ t("progress.conversation") }}</span
        ><span>{{
          status.conversation
            ? t(
                "progress.conversationVal",
                {
                  at: hhmmss(status.conversation.start),
                  n: status.conversation.sentences,
                },
                status.conversation.sentences,
              )
            : t("progress.noConversation")
        }}</span>
        <span>{{ t("progress.engine") }}</span
        ><span>{{ engines(status) || "—" }}</span> <span>{{ t("progress.version") }}</span
        ><span>{{ status.commit || "—" }}</span>
      </div>
    </section>
    <section v-for="d in devices" :key="d.device" class="card">
      <div class="top">
        <span
          ><b>{{ d.device }}</b>
          <RouterLink :to="`/device/${encodeURIComponent(d.device)}`">{{
            t("progress.log")
          }}</RouterLink></span
        >
        <span class="badge" :class="caughtUp(d) ? 'ok' : 'busy'">{{
          t(
            caughtUp(d)
              ? "progress.caughtUp"
              : d.hold
                ? "progress.waitCatchUp"
                : "progress.processing",
          )
        }}</span>
      </div>
      <div class="grid">
        <span>{{ t("progress.received") }}</span
        ><span>{{ at(d.received_to) }}</span> <span>{{ t("progress.finalized") }}</span
        ><span>{{ at(d.done_to) }}</span>
        <template v-if="d.import_progress">
          <span>{{ t("progress.import") }}</span
          ><span>{{ importLine(d) }}</span>
        </template>
        <template v-else>
          <span>{{ t("progress.left") }}</span
          ><span>{{
            t("progress.leftVal", {
              left: caughtUp(d) ? "—" : t("progress.leftAudio", { span: span(d.left_s) }),
              n: d.queued,
            })
          }}</span>
        </template>
        <template v-if="d.hold || d.hold_expired"
          ><span>{{ t("progress.catchUp") }}</span
          ><span>{{ d.hold_expired ? holdLeft(d) : held(d) }}</span></template
        >
        <template v-if="lateLine(d)">
          <span>{{ t("progress.late.title") }}</span>
          <span :class="{ bad: d.late?.failed?.spans }">{{ lateLine(d) }}</span>
        </template>
        <span>{{ t("progress.speed") }}</span
        ><span>{{ d.rate ? t("progress.speedVal", { rate: d.rate }) : "—" }}</span>
        <template v-if="!d.import_progress || (!d.hold && (d.eta_s ?? 0) > 0)">
          <span>{{ t("progress.etaLabel") }}</span
          ><span>{{ caughtUp(d) ? "—" : d.hold ? t("progress.afterCatchUp") : eta(d) }}</span>
        </template>
        <span>{{ t("progress.diarization") }}</span
        ><span :class="{ bad: d.refine?.error }">{{
          d.refine?.error
            ? t("progress.failedE", { e: d.refine.error })
            : d.refine
              ? `${d.refine.at.slice(11)} · ${(d.refine.ms / 1000).toFixed(0)} s`
              : "—"
        }}</span>
        <span>{{ t("progress.conv") }}</span
        ><span>{{
          d.conversation
            ? t(
                "progress.convVal",
                { at: d.conversation.start.slice(11, 16), n: d.conversation.sentences },
                d.conversation.sentences,
              )
            : t("progress.none")
        }}</span>
        <template v-if="showLiveLag(d)">
          <span>{{ t("progress.liveLag") }}</span
          ><span :class="{ bad: (d.arrival?.lag_s ?? 0) > 300 }">{{ arrived(d) }}</span>
        </template>
        <span>{{ t("progress.quality") }}</span
        ><span :class="{ bad: noisy(d) }">{{ sound(d) }}</span>
        <template v-if="d.hw">
          <span>{{ t("progress.recorder") }}</span
          ><span>{{
            [d.hw.model, d.hw.fw && t("progress.firmware", { fw: d.hw.fw })]
              .filter(Boolean)
              .join(" · ")
          }}</span>
          <span>{{ t("progress.battery") }}</span
          ><span>{{
            [
              d.hw.battery != null && d.hw.battery + "%",
              d.hw.charge && chargeText(d.hw.charge),
              caseText(d.hw),
            ]
              .filter(Boolean)
              .join(" · ") || "—"
          }}</span>
          <span>{{ t("progress.card") }}</span
          ><span :class="{ bad: d.hw.store }">{{ card(d.hw) }}</span>
          <template v-if="d.hw.rssi != null"
            ><span>{{ t("progress.signal") }}</span
            ><span>{{ d.hw.rssi }} dBm</span></template
          >
          <template v-if="d.hw.app">
            <span>{{ t("progress.phone") }}</span
            ><span :class="{ bad: outdated(d) }"
              >{{ phoneLine(d.hw)
              }}{{ outdated(d) ? t("progress.outdated", { n: newest }) : "" }}</span
            >
            <span>{{ t("progress.phoneState") }}</span
            ><span :class="{ bad: hot(d.hw) }">{{ phoneState(d.hw) || "—" }}</span>
            <span>{{ t("progress.upload") }}</span
            ><span :class="{ bad: d.hw.upload_fails }">{{ queueLine(d.hw) }}</span>
          </template>
          <span>{{ t("progress.reported") }}</span
          ><span>{{ at(d.hw.at) }}</span>
        </template>
        <template v-if="ups[d.device]">
          <span>{{ t("progress.upgrade") }}</span
          ><span :class="{ bad: ups[d.device]!.ok === false }"
            >{{ at(ups[d.device]!.at) }} {{ upgradeText(ups[d.device]!) }}</span
          >
        </template>
      </div>
      <Listen class="listen" :device="d.device" />
    </section>
    <section v-if="calls" class="card">
      <div class="top">
        <b>{{ t("progress.callsTitle") }}</b
        ><span class="muted">{{ t("common.today") }}</span>
      </div>
      <div v-if="balance" class="call">
        <b>{{ t("progress.balanceTitle") }}</b
        >{{ " " }}<span :class="{ bad: balance.low }">{{ balance.text }}</span>
      </div>
      <div v-for="c in calls.calls" :key="c.kind + c.engine" class="call">
        <b>{{ kind(c.kind) }}</b> <span class="muted">{{ c.engine }}</span>
        <span :class="{ bad: Object.keys(c.failed).length }">{{ callLine(c) }}</span>
      </div>
      <div v-if="!calls.calls.length" class="muted">{{ t("progress.noCalls") }}</div>
    </section>
    <section v-if="status" class="card">
      <div class="top">
        <b>{{ t("progress.summaries") }}</b>
      </div>
      <div class="grid">
        <span>{{ t("progress.summariesPending") }}</span
        ><span>{{ t("progress.summariesVal", status.summaries_pending ?? 0) }}</span>
      </div>
    </section>
    <div v-if="status && !devices.length" class="muted">{{ t("progress.noDevices") }}</div>
    <Skeleton v-if="!status" />
    <section class="card">
      <div class="top">
        <b>{{ t("progress.events") }}</b>
      </div>
      <div ref="logBox" class="events">
        <div v-for="(e, i) in events" :key="i" :class="e.kind">{{ hhmmss(e.at) }} {{ e.text }}</div>
        <div v-if="!events.length" class="muted">{{ t("progress.noEvents") }}</div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.top {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}
.top b + a {
  margin-left: var(--space-2);
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
  grid-template-columns: 5.5em 1fr;
  gap: 2px 12px;
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
}
/* English labels run longer */
.grid:lang(en) {
  grid-template-columns: 7.5em 1fr;
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
.events {
  max-height: 40vh;
  overflow: auto;
  font:
    var(--fs-xs) / 1.5 ui-monospace,
    monospace;
}
.events div {
  white-space: pre-wrap;
  word-break: break-all;
}
.events .state {
  color: var(--accent);
}
.events .warn {
  color: var(--warn);
}
.events .error {
  color: var(--err);
}
.grid span:nth-child(odd) {
  color: var(--muted);
}
</style>
