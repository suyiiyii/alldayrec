<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useTemplateRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import DayPicker from "../components/DayPicker.vue";
import DeviceCard from "../components/DeviceCard.vue";
import Icon from "../components/Icon.vue";
import LivePanel from "../components/LivePanel.vue";
import MediaFold from "../components/MediaFold.vue";
import TranscriptRow from "../components/TranscriptRow.vue";
import {
  convLink,
  fold,
  inSpans,
  hhmm,
  hhmmss,
  holdLeft,
  type Conversation,
  type Row,
} from "../lib/api";
import { t } from "../i18n";
import { loadCalendar, today as todayOf } from "../lib/calendar";
import { useHideOnScroll } from "../lib/hideOnScroll";
import { useLive } from "../lib/live";
import { speakerLabel } from "../lib/speakers";

// One day at a time (?day=YYYY-MM-DD; none: today, which follows the live stream)
const GAP_MS = 5 * 60 * 1000; // a quiet stretch this long gets a mark, so conversations separate
const CATCHUP_MS = 2 * 60 * 1000; // final rows this far short of a device's drafts (or newest audio): a backlog being finalized
const route = useRoute(),
  router = useRouter();
const today = todayOf();
const day = computed(() => (route.query.day as string | undefined) || today);
const { live, rows, partials, convs, status, lives, ready, going, connected, doneTo } =
  useLive(day);
const said = computed(() => new Set(Object.values(partials.value))); // the sentences being said now
// the summary card while a conversation goes on; with none, the idle card under the captions says what the
// recorders are doing, the last conversation today and how to start one
const side = computed(() => live.value && going.value);
const idle = computed(() => live.value && ready.value && !going.value && !said.value.size);
const last = computed(() =>
  convs.value.reduce<Conversation | null>((a, c) => (a && a.end > c.end ? a : c), null),
);
const hit = ref("");
const follow = ref(true);

type Item =
  | { k: "row"; key: string; r: Row; media?: boolean }
  | { k: "fold"; key: string; rows: Row[] }
  | { k: "conv"; key: string; c: Conversation }
  | { k: "gap"; key: string; at: string }
  | { k: "catchup"; key: string; text: string };
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
      eta = d.hold ? d.hw?.backlog_eta_s : d.eta_s;
    const p = {
      device: d.device,
      from: hhmm(from),
      to: hhmm(to),
      eta: eta ? t("live.catchup.eta", { n: Math.ceil(eta / 60) }) : "",
      hold: holdLeft(d),
      mb: kb ? (kb / 1024).toFixed(kb >= 10240 ? 0 : 1) : "",
    };
    const it: Item = {
      k: "catchup",
      key: "u" + d.device,
      text: t(
        !d.hold
          ? "live.catchup.plain"
          : p.hold // the phone went away: what it said of the card's backlog then is out of date
            ? "live.catchup.held"
            : kb
              ? "live.catchup.card"
              : "live.catchup.phone",
        p,
      ),
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

const go = (d: string) => router.push({ query: d === today ? {} : { day: d } });
watch(day, () => {
  follow.value = live.value;
  window.scrollTo(0, 0);
});

const atBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 40;
const onScroll = () => (follow.value = atBottom());
const toBottom = () => window.scrollTo(0, document.body.scrollHeight);
watch(timeline, async () => {
  if (!follow.value || !live.value) return;
  await nextTick();
  toBottom();
});

// the header's height: the summary card sticks right under it
const headH = ref(0);
// the day picker goes away on scroll where it has the header's line to itself (a phone, the app: nothing beside it);
// a tap on what is left of the header brings it back (kept while the captions follow), and only that (its status line
// is a link)
const head = useTemplateRef<HTMLElement>("head");
const { lift, show } = useHideOnScroll(
  head,
  () =>
    matchMedia("(max-width: 600px)").matches ||
    document.documentElement.classList.contains("in-app"),
);
function reveal(e: Event) {
  if (!lift.value) return;
  e.preventDefault();
  e.stopPropagation();
  show();
}
let sizer: ResizeObserver | undefined;
onMounted(() => {
  window.addEventListener("scroll", onScroll);
  loadCalendar();
  follow.value = live.value;
  const h = document.querySelector("header");
  if (h) (sizer = new ResizeObserver(() => (headH.value = h.offsetHeight))).observe(h);
});
onUnmounted(() => {
  window.removeEventListener("scroll", onScroll);
  sizer?.disconnect();
});

function jump(start: string) {
  hit.value = start;
  follow.value = false; // the next words would pull the page back down before it gets there
  const el = document.querySelector(`[data-start="${CSS.escape(start)}"]`);
  if (!el) return;
  // just under what stays on top: the header, and on a phone the summary card stuck under it (wide: beside the row)
  const r = el.getBoundingClientRect(),
    s = document.querySelector(".side")?.getBoundingClientRect();
  const cover = headH.value + (s && s.left < r.right && r.left < s.right ? s.height : 0);
  window.scrollTo({ top: r.top + window.scrollY - cover - 8, behavior: "smooth" });
}
</script>

<template>
  <AppHeader in-app :lift="lift">
    <div ref="head" class="head" @click.capture="reveal">
      <DayPicker :day="day" @pick="go">
        <button v-if="!live" class="back" @click="go(today)">{{ t("live.backToday") }}</button>
      </DayPicker>
      <!-- recording or not, and how far behind the newest sentence came up (each status, every 2 s); the rest is on the
           progress page -->
      <RouterLink class="status" to="/progress" :title="t('title.progress')">
        <template v-if="!connected">{{ t("live.reconnecting") }}</template>
        <template v-else-if="status">
          <span v-if="status.recording === false">{{ t("live.notRecording") }}</span>
          <span v-else :class="{ bad: (status.latency_s ?? 0) > 30 }">{{
            t("live.recordingLag", {
              v: status.latency_s == null ? "—" : t("common.sec", { n: status.latency_s }),
            })
          }}</span>
        </template>
        <template v-else>{{ t("live.connecting") }}</template>
      </RouterLink>
    </div>
  </AppHeader>
  <!-- today: the summary card on top (a phone) or on the right (a wide screen), the captions large -->
  <main :class="{ cols: side }" :style="{ '--head': headH - lift + 'px' }">
    <aside v-if="side" class="side">
      <LivePanel :status="status" :lives="lives" :done-to="doneTo" @jump="jump" />
    </aside>
    <div class="caps">
      <template v-for="it in timeline" :key="it.key">
        <TranscriptRow
          v-if="it.k === 'row'"
          :row="it.r"
          :media="it.media"
          :hit="hit === it.r.start"
          :caption="live"
          :now="said.has(it.r)"
        />
        <MediaFold v-else-if="it.k === 'fold'" :rows="it.rows" :hit="hit" />
        <div v-else-if="it.k === 'conv'" class="conv">
          {{
            t(
              "live.convEnd",
              {
                from: hhmmss(it.c.start),
                to: hhmmss(it.c.end),
                who: it.c.participants.map(speakerLabel).join(t("common.list")) || "—",
                n: it.c.sentences,
              },
              it.c.sentences,
            )
          }}
          <RouterLink :to="'/c/' + it.c.id">{{ convLink(it.c) }}</RouterLink>
        </div>
        <div v-else-if="it.k === 'catchup'" class="gap">{{ it.text }}</div>
        <div v-else class="gap">— {{ hhmm(it.at) }} —</div>
      </template>
      <!-- nothing being said: where the next words will come up, under today's earlier ones -->
      <section v-if="idle" class="idle">
        <div class="none">
          <Icon name="mic" :size="28" />
          <div>
            <b>{{ t("live.idle") }}</b>
            <div class="muted">
              {{ t(status?.devices?.length ? "live.idleWear" : "live.idleConnect") }}
            </div>
          </div>
        </div>
        <DeviceCard v-for="d in status?.devices ?? []" :key="d.device" :d="d" />
        <RouterLink v-if="last" class="card last" :to="'/c/' + last.id">
          <span class="muted">{{ t("live.last", { at: hhmm(last.end) }) }}</span>
          <b>{{ convLink(last) }}</b>
          <Icon name="chevron-right" :size="18" />
        </RouterLink>
        <!-- the app records from its own top bar: no page recorder inside it -->
        <RouterLink class="btn btn-primary rec" to="/record"
          ><Icon name="mic" :size="18" />{{ t("live.recordHere") }}</RouterLink
        >
        <p class="in-app-hint muted">{{ t("live.inAppHint") }}</p>
        <RouterLink v-if="!status?.devices?.length" class="card last" to="/connect">
          <Icon name="smartphone" :size="20" />
          <b>{{ t("live.connectRecorder") }}</b>
          <Icon name="chevron-right" :size="18" />
        </RouterLink>
      </section>
      <div v-else-if="ready && !rows.length && !live" class="empty">
        <Icon name="calendar" :size="48" />{{ t("live.empty") }}
      </div>
    </div>
  </main>
  <button v-show="live && !follow" class="jump" @click="toBottom">
    <Icon name="chevron-down" :size="16" />{{ t("live.toLatest") }}
  </button>
</template>

<style scoped>
.status {
  font-size: var(--fs-xs);
  color: var(--muted);
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
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
/* 回到最新: a pill over the captions while they wait for the reader to come back down */
.jump {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  bottom: calc(var(--tabbar) + var(--space-4));
  z-index: 2;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  border: 0;
  border-radius: var(--radius-pill);
  padding: 0 var(--space-4);
  background: var(--accent);
  color: var(--surface);
  box-shadow: 0 2px 8px rgb(0 0 0 / 0.15);
}
/* a phone: the summary card sticks under the header, the captions scroll under it */
.side {
  position: sticky;
  top: var(--head);
  z-index: 1;
  background: var(--bg);
  padding: var(--space-2) 0;
}
@media (prefers-reduced-motion: no-preference) {
  .side {
    transition: top 0.15s; /* with the header, when the day picker goes or comes */
  }
}
/* nothing going on: a line of what to do, the recorders, the last conversation, how to start */
.idle {
  padding: var(--space-5) 0 var(--space-4);
}
.idle .none {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  color: var(--muted);
}
.idle .none b {
  color: var(--fg);
}
.idle .none .muted {
  font-size: var(--fs-sm);
}
.last {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--fg);
}
.last b {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.last .muted {
  flex: none;
  font-size: var(--fs-sm);
}
.last > .icon {
  flex: none;
  color: var(--muted);
}
.rec {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: 100%;
  margin-bottom: var(--space-3);
}
.in-app-hint {
  display: none;
  text-align: center;
  font-size: var(--fs-sm);
}
:global(html.in-app .idle .rec) {
  display: none;
}
:global(html.in-app .idle .in-app-hint) {
  display: block;
}
/* a wide screen: captions on the left, the summary on the right, as tall as the screen */
@media (min-width: 900px) {
  main.cols {
    padding-bottom: 0; /* on .caps instead: the summary column, stuck, may reach the bottom of the screen */
    display: grid;
    grid-template-columns: minmax(0, 1fr) 320px;
    grid-template-areas: "caps side";
    column-gap: var(--space-5);
    align-items: start;
  }
  .caps {
    grid-area: caps;
    padding-bottom: 60px;
  }
  .side {
    grid-area: side;
    padding: var(--space-3) 0;
  }
  .side > * {
    max-height: calc(100dvh - var(--head) - var(--space-5));
  }
}
</style>
