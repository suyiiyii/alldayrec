<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import DayPicker from "../components/DayPicker.vue";
import Icon from "../components/Icon.vue";
import LivePanel from "../components/LivePanel.vue";
import MediaFold from "../components/MediaFold.vue";
import TranscriptRow from "../components/TranscriptRow.vue";
import {
  convLink,
  fold,
  get,
  inSpans,
  hhmm,
  hhmmss,
  holdLeft,
  type Conversation,
  type LiveState,
  type Row,
  type Status,
} from "../lib/api";
import { loadCalendar, today as todayOf } from "../lib/calendar";
import { useSocket, useWatch } from "../lib/socket";
import { speakerLabel } from "../lib/speakers";

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
  status = ref<Status | null>(null);
const lives = ref<LiveState[]>([]),
  hit = ref("");
const ready = ref(false);
const follow = ref(true);

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
          : `；${kb ? `录音设备卡里还有 ${(kb / 1024).toFixed(kb >= 10240 ? 0 : 1)} MB 录音` : "手机还"}在后台补传${mins}，补完后定稿接上，实时字幕和分析照常`,
    };
    return [{ s: from, it }];
  }),
);
const timeline = computed(() => {
  const ev = [
    ...rows.value.map((r) => ({ s: r.start, it: { k: "row", key: "s" + r.id, r } as Item })),
    ...Object.values(partials.value).map((r) => ({
      s: r.start,
      it: { k: "row", key: r.id, r } as Item,
    })),
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

onMounted(async () => {
  window.addEventListener("scroll", onScroll);
  loadCalendar();
  follow.value = live.value;
  await loadDay();
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
  } else if (m.type === "status") status.value = m;
}, loadDay);

useWatch(send, connected, () => live.value);

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
</script>

<template>
  <AppHeader in-app>
    <div class="head">
      <DayPicker :day="day" @pick="go">
        <button v-if="!live" class="back" @click="go(today)">回到今天</button>
      </DayPicker>
      <!-- recording or not, and how far behind the words come up; the rest is on the progress page -->
      <RouterLink class="status" to="/progress" title="处理进度">
        <template v-if="!connected">连接断开，重连中…</template>
        <template v-else-if="status">
          <span v-if="status.recording === false">未在录音</span>
          <span v-else :class="{ bad: (status.latency_s ?? 0) > 60 }"
            >录音中{{ status.latency_s == null ? "" : ` · 延迟 ${status.latency_s} 秒` }}</span
          >
        </template>
        <template v-else>连接中…</template>
      </RouterLink>
    </div>
    <LivePanel
      v-if="live"
      :status="status"
      :lives="lives"
      :convs="convs"
      :done-to="doneTo"
      resizable
      @jump="jump"
    />
  </AppHeader>
  <main>
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
        {{ it.c.participants.map(speakerLabel).join("、") || "—" }} · {{ it.c.sentences }} 句 ·
        <RouterLink :to="'/c/' + it.c.id">{{ convLink(it.c) }}</RouterLink>
      </div>
      <div v-else-if="it.k === 'catchup'" class="gap">
        正在补齐 {{ it.device }} {{ hhmm(it.from) }}–{{ hhmm(it.to) }} 的录音{{ it.tail }}
      </div>
      <div v-else class="gap">— {{ hhmm(it.at) }} —</div>
    </template>
    <div v-if="ready && !rows.length && live" class="empty">
      <Icon name="mic" :size="48" />
      <div>今天还没有转写内容。连上手机和录音设备，说的话会实时出现在这里</div>
      <RouterLink class="btn btn-primary" to="/connect">连接手机</RouterLink>
    </div>
    <div v-else-if="ready && !rows.length" class="empty">
      <Icon name="calendar" :size="48" />这一天没有转写内容
    </div>
  </main>
  <button v-show="live && !follow" class="jump" @click="toBottom">回到最新</button>
</template>

<style scoped>
.status {
  font-size: var(--fs-xs);
  color: var(--muted);
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.pane {
  flex-basis: 100%;
  border-top: 1px solid var(--line);
  margin-top: 4px;
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
  bottom: calc(var(--tabbar) + 24px);
  border-radius: var(--radius-pill);
  padding: 0 var(--space-4);
  box-shadow: var(--shadow);
}
</style>
