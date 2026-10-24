<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import DayPicker from "../components/DayPicker.vue";
import MediaFold from "../components/MediaFold.vue";
import Timeline from "../components/Timeline.vue";
import TranscriptRow from "../components/TranscriptRow.vue";
import {
  fold,
  get,
  inSpans,
  hhmm,
  hhmmss,
  holdLeft,
  post,
  type Conversation,
  type DeviceStatus,
  type LiveState,
  type LogEvent,
  type Row,
  type Status,
} from "../lib/api";
import { loadCalendar, today as todayOf } from "../lib/calendar";
import { useSocket } from "../lib/socket";

// One day at a time (?day=YYYY-MM-DD; none: today, which follows the live stream)
const GAP_MS = 5 * 60 * 1000; // a quiet stretch this long gets a mark, so conversations separate
const CATCHUP_MS = 2 * 60 * 1000; // final rows this far short of a device's drafts (or newest audio): a backlog being finalized
const route = useRoute(),
  router = useRouter();
const today = todayOf();
const day = computed(() => (route.query.day as string | undefined) || today),
  live = computed(() => day.value === today);
const rows = ref<Row[]>([]),
  partials = ref<Record<string, Row>>({}), // per device, the sentence being said now (DRAFT_ENGINE qwen-stream)
  convs = ref<Conversation[]>([]),
  events = ref<LogEvent[]>([]),
  status = ref<Status | null>(null);
const lives = ref<LiveState[]>([]),
  hit = ref("");
const ready = ref(false);
const follow = ref(true),
  logOpen = ref(false),
  logBox = ref<HTMLElement | null>(null);

type Item =
  | { k: "row"; key: string; r: Row; media?: boolean }
  | { k: "fold"; key: string; rows: Row[] }
  | { k: "conv"; key: string; c: Conversation }
  | { k: "gap"; key: string; at: string }
  | { k: "catchup"; key: string; device: string; from: string; to: string; tail: string };
// a device's finalized time: the status's, or its last final message's (it comes before the next status, every 2 s)
function doneTo(device: string) {
  const s = status.value?.devices?.find((d) => d.device === device)?.done_to ?? "",
    f = finalTo[device] ?? "";
  return s > f ? s : f;
}
// Per device, the time between its final rows and its drafts (or, with none, its newest audio): the drafts follow
// what is said now while a backlog (a phone back after hours offline) is finalized in order, so this time is not lost.
// Held: the phone still reads older audio off the recorder's card in the background; finals wait for it
const catchups = computed(() =>
  (live.value ? (status.value?.devices ?? []) : []).flatMap((d) => {
    const from = doneTo(d.device);
    if (!from) return [];
    const to =
      rows.value
        .filter((r) => r.state === "draft" && r.source === d.device && r.start >= from)
        .reduce<string | null>((a, r) => (a && a < r.start ? a : r.start), null) ?? d.received_to;
    if (!to || (!d.hold && +new Date(to) - +new Date(from) <= CATCHUP_MS)) return [];
    const kb = d.hw?.backlog_kb,
      eta = d.hold ? d.hw?.backlog_eta_s : d.eta_s,
      mins = eta ? `，约 ${Math.ceil(eta / 60)} 分钟` : "";
    const it: Item = {
      k: "catchup",
      key: "u" + d.device,
      device: d.device,
      from,
      to,
      tail: !d.hold
        ? mins
        : holdLeft(d) // the phone went away: what it said of the card's backlog then is out of date
          ? `；等录音设备卡里更早的录音，${holdLeft(d)}，之后先定稿已有的录音`
          : `；${kb ? `录音设备卡里还有 ${(kb / 1024).toFixed(kb >= 10240 ? 0 : 1)} MB 录音` : "手机还"}在后台补传${mins}，补完后定稿接上，实时初稿和分析照常`,
    };
    return [{ s: from, it }];
  }),
);
// The status line: what the recording device is doing; with none recording (no audio for 2 min), when the last one
// did instead of a "behind" that only counts the time since (10-07: 落后 13408 秒 at 01:06, nothing recording). The
// queue by device and why: 261 pieces of LR-P3-116C held for its card's backlog read as the recording device's
const when = (iso?: string | null) =>
  !iso ? "—" : iso.slice(0, 10) === todayOf() ? hhmm(iso) : `${iso.slice(5, 10)} ${hhmm(iso)}`;
const topQueued = computed(
  () =>
    status.value?.devices?.find((d) => d.device === status.value?.device)?.queued ??
    status.value?.queued ??
    0,
);
const queues = computed(() =>
  (status.value?.devices ?? []).filter(
    (d) => d.queued && (status.value?.recording === false || d.device !== status.value?.device),
  ),
);
const queueText = (d: DeviceStatus) =>
  `${d.device} 排队 ${d.queued} 块${d.hold ? `，等卡里 ${d.done_to ? hhmm(d.done_to) + " 后" : "更早"}的旧录音` : "，定稿中"}`;
const timeline = computed(() => {
  const ev = [
    ...rows.value.map((r) => ({ s: r.start, it: { k: "row", key: "s" + r.id, r } as Item })),
    ...Object.values(partials.value).map((r) => ({ s: r.start, it: { k: "row", key: r.id, r } as Item })),
    ...convs.value.map((c) => ({ s: c.end, it: { k: "conv", key: "c" + c.id, c } as Item })),
    ...catchups.value,
  ].sort((a, b) => (a.s < b.s ? -1 : a.s > b.s ? 1 : 0)); // stable: a divider stays after rows starting at its end
  const out: Item[] = [];
  let lastEnd = 0;
  for (const { s, it } of ev) {
    if (it.k === "row") {
      if (lastEnd && +new Date(s) - lastEnd > GAP_MS) out.push({ k: "gap", key: "g" + s, at: s });
      lastEnd = +new Date(it.r.end);
    }
    out.push(it);
  }
  // media rows, known once their conversation is summarized, fold run by run between dividers
  const res: Item[] = [];
  let rs: Row[] = [];
  const flush = () => {
    for (const g of fold(rs, mediaOf))
      res.push(g.k === "row" ? { k: "row", key: "s" + g.r.id, r: g.r, media: g.media } : g);
    rs = [];
  };
  for (const it of out) {
    if (it.k === "row") rs.push(it.r);
    else {
      flush();
      res.push(it);
    }
  }
  flush();
  return res;
});
const mediaOf = (r: Row) =>
  convs.value.some(
    (c) =>
      (!c.device || c.device === r.source) &&
      c.start <= r.start &&
      r.start <= c.end &&
      inSpans(r, c.summary?.media_spans),
  );
const lastEvent = computed(() => {
  const e = events.value.at(-1);
  return e ? `· ${hhmmss(e.at)} ${e.text}`.slice(0, 90) : "";
});

// per device, up to where final rows cover the time: its drafts before that are no longer shown (they stay on the server)
const finalTo: Record<string, string> = {};
function finalize(m: { device?: string; from: string; to: string; rows: Row[] }) {
  if (m.device) finalTo[m.device] = m.to;
  rows.value = rows.value
    .filter((r) =>
      r.state === "draft"
        ? !(r.source === m.device && r.start < m.to)
        : !(m.from <= r.start && r.start < m.to && (!m.device || r.source === m.device)),
    )
    .concat(m.rows);
}
function addDrafts(m: { device: string; rows: Row[] }) {
  const to = finalTo[m.device] || "";
  delete partials.value[m.device]; // the sentence it showed has ended: now a draft row
  rows.value = rows.value.concat(m.rows.filter((r) => r.start >= to));
}
function addConv(c: Conversation) {
  if (c.state === "open") return;
  const i = convs.value.findIndex((x) => x.id === c.id);
  if (i >= 0) convs.value[i] = c;
  else convs.value.push(c);
}
function addEvent(e: LogEvent) {
  events.value.push(e);
  if (events.value.length > 300) events.value.splice(0, events.value.length - 300);
}

async function loadDay() {
  const d = day.value;
  const [rs, cs, ls] = await Promise.all([
    get<Row[]>("/api/sentences", { date: d }),
    get<Conversation[]>("/api/conversations", { date: d }),
    get<LiveState[]>("/api/live"),
  ]);
  if (d !== day.value) return; // another day was picked meanwhile
  rows.value = rs;
  lives.value = ls;
  convs.value = [];
  cs.forEach(addConv);
  ready.value = true;
  await nextTick();
  if (live.value) toBottom();
  else window.scrollTo(0, 0);
}
const go = (d: string) => router.push({ query: d === today ? {} : { day: d } });
watch(day, () => {
  ready.value = false;
  follow.value = live.value;
  loadDay();
});

const atBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 40;
const onScroll = () => (follow.value = atBottom());
const toBottom = () => window.scrollTo(0, document.body.scrollHeight);
watch(timeline, async () => {
  if (!follow.value || !live.value) return;
  await nextTick();
  toBottom();
});
watch(
  () => events.value.length,
  async () => {
    await nextTick();
    if (logOpen.value && logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight;
  },
);

onMounted(async () => {
  window.addEventListener("scroll", onScroll);
  loadCalendar();
  follow.value = live.value;
  await loadDay();
  (await get<LogEvent[]>("/api/events")).forEach(addEvent);
});
onUnmounted(() => window.removeEventListener("scroll", onScroll));

const { connected, send } = useSocket((m) => {
  // rows and conversations of another day than the one shown are not for this page
  if (m.type === "final") {
    if (live.value) finalize(m);
  } else if (m.type === "draft") {
    if (live.value) addDrafts(m);
  } else if (m.type === "partial") {
    if (live.value && m.row) partials.value[m.device] = m.row;
    else delete partials.value[m.device];
  } else if (m.type === "conversation") {
    if (m.start.slice(0, 10) === day.value) addConv(m);
  } else if (m.type === "summary") {
    const c = convs.value.find((x) => x.id === m.conversation);
    if (c) c.summary = { ...m };
  } else if (m.type === "live") {
    lives.value = lives.value.filter((l) => l.device !== m.device).concat(m.live ? [m.live] : []);
  } else if (m.type === "event") addEvent(m);
  else status.value = m;
}, loadDay);

// The live analysis runs only while a page shows today in front: a heartbeat every 20 s, and a wake when
// the page comes back to the front so a stale analysis is redone at once.
let wake = true;
function beat() {
  if (live.value && !document.hidden && send({ type: "watch", wake })) wake = false;
}
function onVisible() {
  if (document.hidden) return;
  wake = true;
  beat();
}
let beatTimer = 0;
onMounted(() => {
  beatTimer = window.setInterval(beat, 20000);
  document.addEventListener("visibilitychange", onVisible);
});
onUnmounted(() => {
  clearInterval(beatTimer);
  document.removeEventListener("visibilitychange", onVisible);
});
watch([connected, live], ([c, l]) => c && l && ((wake = true), beat()));

// The bar above the transcript, today: what the live analysis does for one device, its result in a pane whose height
// is dragged. The device: of the recording ones, the one whose conversation going on spoke last (not the one analyzed
// last: a conversation under a minute has no analysis yet); with none talking, the one whose audio came in last
const MIN_SPEECH_S = 60; // server summary.MIN_SPEECH_S: a conversation is analyzed by itself from this much speech on
const barDev = computed(() => {
  const ds = status.value?.devices ?? [];
  const talking = ds
    .filter((d) => d.recording && d.conversation)
    .reduce<DeviceStatus | null>(
      (a, d) => (a && a.conversation!.end > d.conversation!.end ? a : d),
      null,
    );
  return talking ?? ds.find((d) => d.device === status.value?.device) ?? null;
});
const talk = computed(() => (barDev.value?.recording && barDev.value.conversation) || null);
// its analysis, when it is of the conversation going on (server overlaps())
const current = computed(() => {
  const c = talk.value;
  return (
    (c &&
      lives.value.find(
        (l) => l.device === barDev.value!.device && l.start <= c.end && l.upto >= c.start,
      )) ||
    null
  );
});
// with none going on, its last conversation today: its summary stays until the next one starts
const last = computed(() =>
  convs.value
    .filter((c) => !c.device || c.device === barDev.value?.device)
    .reduce<Conversation | null>((a, c) => (a && a.end > c.end ? a : c), null),
);
const said = (s: number) => (s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`);
const barText = computed(() => {
  const d = barDev.value,
    c = talk.value,
    l = current.value;
  const prev = last.value ? ` · 上一段 ${hhmm(last.value.start)}《${convLink(last.value)}》` : "";
  if (!status.value?.recording) return "未在录音" + prev;
  if (!c) return "暂无进行中的对话" + prev;
  if (l) return `实时分析 · 更新于 ${hhmm(l.at)}` + (d?.analyzing ? " · 分析中…" : "");
  if (d?.analyzing) return "分析中…";
  return (
    `对话进行中 · 已说 ${said(c.speech_s)}` +
    (c.speech_s < MIN_SPEECH_S ? `，满 ${MIN_SPEECH_S / 60} 分钟自动分析` : "")
  );
});
const body = computed(() => (current.value ? "live" : !talk.value && last.value ? "last" : ""));
// 立即总结 / 立即更新: the analysis now, with speech it has not seen; the button rests 10 s after a press (double taps)
const asking = ref(false),
  askErr = ref("");
const fresh = computed(() => (talk.value?.speech_s ?? 0) > (current.value?.speech_s ?? 0));
async function askNow() {
  const d = barDev.value;
  if (!d) return;
  asking.value = true;
  setTimeout(() => (asking.value = false), 10000);
  askErr.value = "";
  try {
    Object.assign(
      d,
      await post<Partial<DeviceStatus>>(`/api/live/${encodeURIComponent(d.device)}`),
    );
  } catch (e) {
    askErr.value = (e as Error).message;
    setTimeout(() => (askErr.value = ""), 5000);
  }
}
const paneOpen = ref(
  (() => {
    try {
      return localStorage.getItem("livePaneOpen") !== "0";
    } catch {
      return true;
    }
  })(),
);
function toggle() {
  paneOpen.value = !paneOpen.value;
  try {
    localStorage.setItem("livePaneOpen", paneOpen.value ? "1" : "0");
  } catch {}
}
const clampH = (h: number) => Math.round(Math.min(Math.max(h, 80), window.innerHeight * 0.7));
const paneH = ref(
  clampH(
    Number(
      (() => {
        try {
          return localStorage.getItem("livePaneH");
        } catch {
          return null;
        }
      })(),
    ) || 220,
  ),
);
const paneBox = ref<HTMLElement | null>(null);
let drag: { y: number; h: number } | null = null;
function dragStart(e: PointerEvent) {
  drag = { y: e.clientY, h: paneH.value };
  (e.target as HTMLElement).setPointerCapture(e.pointerId);
}
function dragMove(e: PointerEvent) {
  if (drag) paneH.value = clampH(drag.h + e.clientY - drag.y);
}
function dragEnd() {
  drag = null;
  try {
    localStorage.setItem("livePaneH", String(paneH.value));
  } catch {}
}
watch(
  () => [current.value?.at, paneOpen.value],
  async () => {
    await nextTick();
    if (paneBox.value) paneBox.value.scrollTop = paneBox.value.scrollHeight; // the newest topic is at the bottom
  },
);
function jump(start: string) {
  hit.value = start;
  const el = document.querySelector(`[data-start="${CSS.escape(start)}"]`);
  if (!el) return;
  const head = document.querySelector("header")?.getBoundingClientRect().height ?? 0;
  window.scrollTo({
    top: el.getBoundingClientRect().top + window.scrollY - head - 8,
    behavior: "smooth",
  });
}

const convLink = (c: Conversation) =>
  c.summary?.status === "done"
    ? c.summary.title || "查看"
    : c.summary?.status === "failed"
      ? "摘要生成失败"
      : "摘要生成中…";
</script>

<template>
  <AppHeader in-app>
    <div class="head">
      <DayPicker :day="day" @pick="go">
        <button v-if="!live" class="back" @click="go(today)">回到今天</button>
      </DayPicker>
      <RouterLink class="status" to="/progress" title="处理进度">
        <template v-if="!connected">连接断开，重连中…</template>
        <template v-else-if="status">
          <span v-if="status.recording === false"
            >未在录音 · 最后录音 {{ when(status.last_audio) }}</span
          >
          <template v-else>
            <span>{{ status.device || "等待录音" }}</span>
            <span :class="{ bad: (status.behind_s ?? 0) > 30 }"
              >落后 {{ status.behind_s == null ? "—" : status.behind_s + " 秒" }}</span
            >
            <span :class="{ bad: topQueued }">排队 {{ topQueued }} 块</span>
            <span :class="{ bad: (status.latency_s ?? 0) > 60 }"
              >上屏 {{ status.latency_s ?? "—" }} 秒</span
            >
          </template>
          <span v-for="d in queues" :key="'q' + d.device" :class="{ bad: !d.hold }">{{
            queueText(d)
          }}</span>
          <span :class="{ bad: status.refine?.error }">
            分离
            {{
              status.refine
                ? status.refine.error
                  ? "失败"
                  : status.refine.at.slice(11) + " " + (status.refine.ms / 1000).toFixed(0) + "s"
                : "等待"
            }}
          </span>
          <span
            >状态机
            {{
              status.conversation
                ? "对话打开 " +
                  hhmmss(status.conversation.start) +
                  " · " +
                  status.conversation.sentences +
                  " 句"
                : "空闲，无打开的对话"
            }}</span
          >
          <span>{{ status.engine }} {{ status.commit }}</span>
        </template>
        <template v-else>连接中…</template>
      </RouterLink>
    </div>
    <div v-if="live && status" class="pane">
      <div class="pane-line">
        <button
          class="fold"
          :disabled="!body"
          :aria-expanded="body ? paneOpen : undefined"
          @click="toggle"
        >
          <span v-if="body" class="caret">{{ paneOpen ? "▾" : "▸" }}</span
          >{{ barText }}
          <span v-if="askErr" class="bad"> · {{ askErr }}</span>
        </button>
        <button
          v-if="talk"
          class="ask"
          :disabled="asking || barDev?.analyzing || !fresh"
          :title="fresh ? '' : '没有新的语音'"
          @click="askNow"
        >
          {{ current ? "立即更新" : "立即总结" }}
        </button>
      </div>
      <div
        v-if="body && paneOpen"
        ref="paneBox"
        class="pane-body"
        :style="{ [current ? 'height' : 'maxHeight']: paneH + 'px' }"
      >
        <template v-if="current">
          <div class="pane-meta">
            {{ current.start > doneTo(current.device) ? "基于最新初稿 · " : "" }}对话
            {{ hhmmss(current.start) }} 起 · 到 {{ hhmmss(current.upto) }} 共
            {{ current.sentences }} 句
          </div>
          <Timeline :topics="current.topics" open @jump="jump" />
        </template>
        <template v-else-if="last">
          <div class="pane-meta">
            对话 {{ hhmmss(last.start) }}–{{ hhmmss(last.end) }} · {{ last.sentences }} 句 ·
            <RouterLink :to="'/c/' + last.id">查看</RouterLink>
          </div>
          <p v-if="last.summary?.status !== 'done'" class="muted">{{ convLink(last) }}</p>
          <p v-else-if="last.summary.summary">{{ last.summary.summary }}</p>
          <p v-else class="muted">这段太短，没有生成摘要</p>
        </template>
      </div>
      <div
        v-if="body && paneOpen"
        class="grip"
        role="separator"
        aria-orientation="horizontal"
        aria-label="拖动调整实时分析的高度"
        title="拖动调整高度"
        @pointerdown="dragStart"
        @pointermove="dragMove"
        @pointerup="dragEnd"
        @pointercancel="dragEnd"
      ></div>
    </div>
  </AppHeader>
  <main class="live">
    <template v-for="it in timeline" :key="it.key">
      <TranscriptRow
        v-if="it.k === 'row'"
        :row="it.r"
        :media="it.media"
        :hit="hit === it.r.start"
      />
      <MediaFold v-else-if="it.k === 'fold'" :rows="it.rows" :hit="hit" />
      <div v-else-if="it.k === 'conv'" class="conv">
        对话 {{ hhmmss(it.c.start) }}–{{ hhmmss(it.c.end) }} 结束 · 参与
        {{ it.c.participants.join("、") || "—" }} · {{ it.c.sentences }} 句 ·
        <RouterLink :to="'/c/' + it.c.id">{{ convLink(it.c) }}</RouterLink>
      </div>
      <div v-else-if="it.k === 'catchup'" class="gap">
        正在补齐 {{ it.device }} {{ hhmm(it.from) }}–{{ hhmm(it.to) }} 的录音{{ it.tail }}
      </div>
      <div v-else class="gap">— {{ hhmm(it.at) }} —</div>
    </template>
    <div v-if="ready && !rows.length" class="empty">
      {{ live ? "今天还没有转写内容" : "这一天没有转写内容" }}
    </div>
  </main>
  <button v-show="live && !follow" class="jump" @click="toBottom">回到最新</button>
  <details
    class="syslog"
    :open="logOpen"
    @toggle="logOpen = ($event.target as HTMLDetailsElement).open"
  >
    <summary>
      系统日志 <span>{{ lastEvent }}</span>
    </summary>
    <div ref="logBox" class="events">
      <div v-for="(e, i) in events" :key="i" :class="e.kind">{{ hhmmss(e.at) }} {{ e.text }}</div>
    </div>
  </details>
</template>

<style scoped>
.status {
  font-size: var(--fs-xs);
  color: var(--muted);
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.live {
  padding-bottom: 120px;
}
.pane {
  flex-basis: 100%;
  border-top: 1px solid var(--line);
  margin-top: 4px;
}
.pane-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 4px;
}
.fold {
  flex: 1;
  min-width: 0;
  border: 0;
  padding: 0;
  text-align: left;
  font-size: var(--fs-xs);
  color: var(--muted);
  background: none;
}
.caret {
  display: inline-block;
  width: 14px;
  color: var(--accent);
}
.ask {
  flex: none;
}
.pane-body {
  overflow-y: auto;
  overscroll-behavior: contain;
}
.pane-body p {
  margin: 4px 0;
  font-size: var(--fs-sm);
}
.pane-meta {
  font-size: var(--fs-xs);
  color: var(--muted);
  padding-top: 4px;
}
/* the whole strip takes the drag (tall enough for a finger); its bottom is the header's bottom line, through the
   header's padding, and a handle sits on that line: the line between the analysis and the transcript is what moves */
.grip {
  position: relative;
  height: 24px;
  margin-bottom: -12px;
  cursor: ns-resize;
  touch-action: none;
}
.grip::before {
  content: "";
  position: absolute;
  inset: auto 0 -1px;
  height: 2px;
  background: var(--accent);
  opacity: 0;
  transition: opacity 0.15s;
}
.grip::after {
  content: "";
  position: absolute;
  left: 50%;
  bottom: -9px;
  width: 48px;
  height: 18px;
  margin-left: -24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  background:
    linear-gradient(var(--muted) 0 0) 50% 5px / 18px 1.5px no-repeat,
    linear-gradient(var(--muted) 0 0) 50% 9px / 18px 1.5px no-repeat,
    var(--surface);
  box-shadow: var(--shadow);
  transition:
    border-color 0.15s,
    box-shadow 0.15s;
}
.grip:hover::before,
.grip:active::before {
  opacity: 1;
}
.grip:hover::after,
.grip:active::after {
  border-color: var(--accent);
  background:
    linear-gradient(var(--accent) 0 0) 50% 5px / 18px 1.5px no-repeat,
    linear-gradient(var(--accent) 0 0) 50% 9px / 18px 1.5px no-repeat,
    var(--surface);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
@media (max-width: 600px) {
  .grip {
    height: 28px;
    margin-bottom: -8px; /* the header's padding here */
  }
}
.conv {
  color: var(--accent);
  font-size: var(--fs-xs);
  padding: 12px 0 4px;
  border-top: 2px solid var(--accent);
  margin-top: 8px;
}
.gap {
  color: var(--muted);
  font-size: var(--fs-xs);
  text-align: center;
  padding: 10px 0 4px;
}
.head {
  flex: 1;
  min-width: 260px; /* below the nav on a phone outside the app */
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-1) 0;
}
.back {
  margin-left: auto;
}
.jump {
  position: fixed;
  right: 24px;
  bottom: 64px;
  border-radius: var(--radius-pill);
  padding: 0 var(--space-4);
  box-shadow: var(--shadow);
}
.syslog {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--surface);
  border-top: 1px solid var(--line);
  z-index: 2;
}
.syslog summary {
  max-width: 860px;
  margin: 0 auto;
  padding: 6px 16px;
  font-size: var(--fs-xs);
  color: var(--muted);
  cursor: pointer;
  list-style: none;
}
.syslog summary::before {
  content: "▸ ";
}
.syslog[open] summary::before {
  content: "▾ ";
}
.events {
  max-width: 860px;
  margin: 0 auto;
  padding: 0 16px 8px;
  max-height: 32vh;
  overflow: auto;
  font:
    12px/1.5 ui-monospace,
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
@media (max-width: 600px) {
  .status {
    flex-wrap: nowrap;
    overflow-x: auto;
    white-space: nowrap;
    gap: 12px;
    scrollbar-width: none;
  }
  .live {
    padding: 4px 12px 110px;
  }
}
</style>
