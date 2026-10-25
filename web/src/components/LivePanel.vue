<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import Icon from "./Icon.vue";
import Timeline from "./Timeline.vue";
import { t } from "../i18n";
import { speakerLabel } from "../lib/speakers";
import {
  errorTitle,
  hhmm,
  hhmmss,
  post,
  type DeviceStatus,
  type LiveState,
  type Status,
} from "../lib/api";

// The live analysis of the conversation going on, as a card: the newest topic is the headline (always in sight), the
// topics under it (a tap on the head folds them away on a phone; a wide screen always shows them); shown only while one
// goes on. mini: the headline and the newest topic's last few points (时间线's live card). The page owns the data: the
// status, the analyses. doneTo: up to where a device's rows are final (the live page knows it from final messages; else
// the status's). device: the one to follow, if it is in a conversation (the live card's caption on top)
const props = defineProps<{
  status: Status | null;
  lives: LiveState[];
  doneTo?: (device: string) => string;
  mini?: boolean;
  device?: string;
}>();
defineEmits<{ jump: [start: string] }>();
const doneTo = (device: string) =>
  props.doneTo?.(device) ?? props.status?.devices?.find((d) => d.device === device)?.done_to ?? "";

// The device: the one given, if it is in a conversation; else of the recording ones, the one whose conversation going on
// spoke last (not the one analyzed last: a conversation under a minute has no analysis yet); with none talking, the one
// whose audio came in last
const MIN_SPEECH_S = 60; // server summary.MIN_SPEECH_S: a conversation is analyzed by itself from this much speech on
const barDev = computed(() => {
  const ds = props.status?.devices ?? [];
  const going = ds.filter((d) => d.recording && d.conversation);
  const talking =
    going.find((d) => d.device === props.device) ??
    going.reduce<DeviceStatus | null>(
      (a, d) => (a && a.conversation!.end > d.conversation!.end ? a : d),
      null,
    );
  return talking ?? ds.find((d) => d.device === props.status?.device) ?? null;
});
const talk = computed(() => (barDev.value?.recording && barDev.value.conversation) || null);
// its analysis, when it is of the conversation going on (server overlaps())
const current = computed(() => {
  const c = talk.value;
  return (
    (c &&
      props.lives.find(
        (l) => l.device === barDev.value!.device && l.start <= c.end && l.upto >= c.start,
      )) ||
    null
  );
});
const said = (s: number) =>
  s < 60 ? t("common.sec", { n: s }) : t("common.minSec", { m: Math.floor(s / 60), s: s % 60 });
// the head: what it is (label), the newest topic or how long the conversation has gone before there is one (headline)
const sub = computed(() =>
  [
    current.value && t("livePanel.updated", { at: hhmm(current.value.at) }),
    barDev.value?.analyzing && t("livePanel.analyzing"),
  ]
    .filter(Boolean)
    .join(" · "),
);
const label = computed(() => t("livePanel.label") + (sub.value && " · " + sub.value));
const headline = computed(() => {
  const c = talk.value,
    l = current.value;
  if (l) return l.topics.at(-1)?.title || t("livePanel.summarizing");
  if (c)
    return t(c.speech_s < MIN_SPEECH_S ? "livePanel.goingAuto" : "livePanel.going", {
      said: said(c.speech_s),
      n: MIN_SPEECH_S / 60,
    });
  return t("livePanel.waiting");
});
// mini: the newest topic's last 3 points, decisions and to-dos, in the order the live page lists them
const MINI_ITEMS = 3;
const items = computed(() => {
  const tp = current.value?.topics.at(-1);
  if (!tp) return [];
  return [
    ...tp.points.map((p) => ({ cls: "", text: p })),
    ...tp.decisions.map((d) => ({ cls: "dec", text: t("topicTimeline.decided", { d }) })),
    ...tp.todos.map((x) => ({
      cls: "todo",
      text:
        t("topicTimeline.todo") +
        (x.who ? speakerLabel(x.who) : t("common.unknown")) +
        " " +
        x.what,
    })),
  ].slice(-MINI_ITEMS);
});
// 立即总结 / 立即更新: the analysis now, with speech it has not seen; the button rests 10 s after a press (double taps)
const asking = ref(false),
  askErr = ref(""),
  askErrTitle = ref("");
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
    askErrTitle.value = errorTitle(e);
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
const keepOpen = () => {
  try {
    localStorage.setItem("livePaneOpen", paneOpen.value ? "1" : "0");
  } catch {}
};
function toggle() {
  paneOpen.value = !paneOpen.value;
  keepOpen();
}
const paneBox = ref<HTMLElement | null>(null);
// the topics' height, dragged by the grip under them (a phone; a wide screen gives the column the screen's height), kept
// per orientation; none: the default (28vh). A double tap on the grip goes back to it, open. Dragged up past half the
// least height the topics fold away (the head stays, as a tap on it does); dragged down from there they open again
const MIN_H = 56; // about two lines
// under it the grip and the page's newest caption stay in sight: grip 20 + gap 8 + a one-line row 81 + main's padding 60
const BELOW = 170;
const portrait = matchMedia("(orientation: portrait)");
const hKey = () => "livePaneH-" + (portrait.matches ? "portrait" : "landscape");
function readH() {
  try {
    return Number(localStorage.getItem(hKey())) || 0;
  } catch {
    return 0;
  }
}
const paneH = ref(readH());
const turned = () => (paneH.value = readH());
portrait.addEventListener("change", turned);
onUnmounted(() => portrait.removeEventListener("change", turned));
let drag: { y: number; h: number; max: number; moved: boolean; was: number } | null = null,
  tapAt = 0;
function dragStart(e: PointerEvent) {
  const g = e.currentTarget as HTMLElement,
    h = paneOpen.value ? paneBox.value!.offsetHeight : 0, // folded: the topics come back under the grip
    tabs = parseFloat(getComputedStyle(document.body).paddingBottom); // the bottom tab bar
  drag = {
    y: e.clientY,
    h,
    max: innerHeight - tabs - g.getBoundingClientRect().top + h - BELOW, // the grip is right under the topics
    moved: false,
    was: paneH.value,
  };
  g.setPointerCapture(e.pointerId);
}
function dragMove(e: PointerEvent) {
  if (!drag) return;
  const dy = e.clientY - drag.y;
  drag.moved ||= Math.abs(dy) > 4;
  if (!drag.moved) return;
  paneOpen.value = drag.h + dy >= MIN_H / 2;
  // folded, it keeps the height it had: a tap on the head opens it to that
  paneH.value = paneOpen.value
    ? Math.round(Math.min(Math.max(drag.h + dy, MIN_H), drag.max))
    : drag.was;
}
function dragEnd() {
  if (!drag) return;
  const moved = drag.moved;
  drag = null;
  if (!moved) {
    // a tap (no dblclick: iOS does not fire it for a finger)
    if (performance.now() - tapAt < 300) {
      paneH.value = 0;
      paneOpen.value = true;
    }
    tapAt = performance.now();
  }
  keepOpen();
  try {
    if (paneH.value) localStorage.setItem(hKey(), String(paneH.value));
    else localStorage.removeItem(hKey());
  } catch {}
}
watch(
  () => [current.value?.at, paneOpen.value],
  async () => {
    await nextTick();
    if (paneBox.value) paneBox.value.scrollTop = paneBox.value.scrollHeight; // the newest topic is at the bottom
  },
);
</script>

<template>
  <div v-if="status && mini" class="mini">
    <Icon class="spark" name="sparkles" :size="16" />
    <div class="mini-text">
      <b>{{ headline }}</b>
      <span v-if="askErr" class="bad" :title="askErrTitle">{{ askErr }}</span>
      <span v-else-if="sub" class="muted">{{ sub }}</span>
      <ul v-if="items.length" class="items">
        <li v-for="(x, i) in items" :key="i" :class="x.cls">{{ x.text }}</li>
      </ul>
    </div>
    <button
      v-if="talk"
      class="ask"
      :disabled="asking || barDev?.analyzing || !fresh"
      :title="fresh ? '' : t('livePanel.noNew')"
      @click="askNow"
    >
      {{ current ? t("livePanel.update") : t("livePanel.summarize") }}
    </button>
  </div>
  <section v-else-if="status" class="card sum" :class="{ shut: !paneOpen }">
    <div class="head">
      <button
        class="fold"
        :disabled="!current"
        :aria-expanded="current ? paneOpen : undefined"
        @click="toggle"
      >
        <span class="label"><Icon name="sparkles" :size="14" />{{ label }}</span>
        <b class="title">{{ headline }}</b>
        <span v-if="askErr" class="bad" :title="askErrTitle">{{ askErr }}</span>
      </button>
      <button
        v-if="talk"
        class="ask"
        :disabled="asking || barDev?.analyzing || !fresh"
        :title="fresh ? '' : t('livePanel.noNew')"
        @click="askNow"
      >
        {{ current ? t("livePanel.update") : t("livePanel.summarize") }}
      </button>
      <Icon
        v-if="current"
        class="caret"
        :name="paneOpen ? 'chevron-down' : 'chevron-right'"
        :size="18"
        @click="toggle"
      />
    </div>
    <div
      v-if="current"
      ref="paneBox"
      class="body"
      :style="paneH ? { '--pane-h': paneH + 'px' } : undefined"
    >
      <div class="meta">
        {{
          t(
            current.start > doneTo(current.device) ? "livePanel.metaDraft" : "livePanel.meta",
            { from: hhmmss(current.start), upto: hhmmss(current.upto), n: current.sentences },
            current.sentences,
          )
        }}
      </div>
      <Timeline :topics="current.topics" open @jump="$emit('jump', $event)" />
    </div>
    <div
      v-if="current"
      class="grip"
      role="separator"
      aria-orientation="horizontal"
      :aria-label="t('livePanel.resize')"
      :title="t('livePanel.resize')"
      @pointerdown="dragStart"
      @pointermove="dragMove"
      @pointerup="dragEnd"
      @pointercancel="dragEnd"
    >
      <Icon name="grip-horizontal" :size="20" />
    </div>
  </section>
</template>

<style scoped>
/* 时间线's live card: the headline on one line, its time under it, the button on the right */
.mini {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}
.spark {
  flex: none;
  color: var(--accent);
  margin-top: 4px;
}
.mini-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}
.mini-text span {
  font-size: var(--fs-xs);
}
/* one line each: the card keeps its height, the rest is on the live page */
.items {
  margin: var(--space-1) 0 0;
  padding: 0;
  list-style-position: inside; /* an outside bullet is clipped with the line's overflow */
  font-size: var(--fs-sm);
}
.items li {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.items .dec {
  color: var(--accent);
}
.items .todo {
  color: var(--warn);
}
.ask {
  flex: none;
}
/* the live page's card: the head always, the topics under it scroll by themselves */
.sum {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: var(--space-3) var(--space-4);
}
.head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.fold {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  border: 0;
  padding: 0;
  text-align: left;
  background: none;
  color: var(--fg);
  min-height: 44px;
}
.fold:disabled {
  color: var(--fg);
}
.label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--fs-xs);
  color: var(--muted);
}
.label svg {
  color: var(--accent);
}
.title {
  font-size: var(--fs-md);
  line-height: 1.45;
}
.fold .bad {
  font-size: var(--fs-xs);
}
.caret {
  flex: none;
  color: var(--muted);
  cursor: pointer;
}
.body {
  overflow-y: auto;
  overscroll-behavior: contain;
  height: var(--pane-h, auto);
  max-height: var(--pane-h, 28vh);
  margin-top: var(--space-2);
  border-top: 1px solid var(--line);
}
.meta {
  font-size: var(--fs-xs);
  color: var(--muted);
  padding-top: var(--space-2);
}
/* the grip on the card's bottom edge (in its padding); the strip a finger takes reaches 44 px over the edges */
.grip {
  position: relative;
  display: flex;
  justify-content: center;
  margin-bottom: calc(-1 * var(--space-3));
  color: var(--muted);
  cursor: ns-resize;
  touch-action: none; /* the drag moves the grip, not the page */
}
.grip::before {
  content: "";
  position: absolute;
  inset: -12px 0;
}
.grip:hover,
.grip:active {
  color: var(--accent);
}
/* a phone: a sheet hanging from the header over the captions, edge to edge, frosted (the captions under it blur), its
   bottom corners round */
@media (max-width: 899px) {
  .sum {
    border-radius: 0 0 var(--radius) var(--radius);
    background: color-mix(in srgb, var(--surface) 60%, transparent);
    -webkit-backdrop-filter: blur(10px) saturate(180%);
    backdrop-filter: blur(10px) saturate(180%);
    box-shadow: 0 6px 16px rgb(0 0 0 / 0.12);
  }
  .shut .body {
    display: none;
  }
}
@media (max-width: 899px) and (prefers-color-scheme: dark) {
  .sum {
    box-shadow: 0 6px 16px rgb(0 0 0 / 0.5);
  }
}
/* a wide screen: the card is a column of its own, always open, as tall as the screen allows */
@media (min-width: 900px) {
  .caret,
  .grip {
    display: none;
  }
  .fold {
    pointer-events: none;
  }
  .head {
    flex: none;
  }
  .body {
    flex: 1;
    min-height: 0; /* a flex item: without it the topics push the card past the screen instead of scrolling */
    height: auto;
    max-height: none;
  }
}
</style>
