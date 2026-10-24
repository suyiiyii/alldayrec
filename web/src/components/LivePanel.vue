<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import Icon from "./Icon.vue";
import Timeline from "./Timeline.vue";
import {
  convLink,
  errorTitle,
  hhmm,
  hhmmss,
  post,
  type Conversation,
  type DeviceStatus,
  type LiveState,
  type Status,
} from "../lib/api";

// What the live analysis does for one device today, its result in a pane (on the live page, under its header; its
// height dragged when resizable). The page owns the data: the status, the analyses, today's closed conversations.
// doneTo: up to where a device's rows are final (the live page knows it from final messages; else the status's)
const props = defineProps<{
  status: Status | null;
  lives: LiveState[];
  convs: Conversation[];
  doneTo?: (device: string) => string;
  resizable?: boolean;
}>();
defineEmits<{ jump: [start: string] }>();
const doneTo = (device: string) =>
  props.doneTo?.(device) ?? props.status?.devices?.find((d) => d.device === device)?.done_to ?? "";

// The device: of the recording ones, the one whose conversation going on spoke last (not the one analyzed
// last: a conversation under a minute has no analysis yet); with none talking, the one whose audio came in last
const MIN_SPEECH_S = 60; // server summary.MIN_SPEECH_S: a conversation is analyzed by itself from this much speech on
const barDev = computed(() => {
  const ds = props.status?.devices ?? [];
  const talking = ds
    .filter((d) => d.recording && d.conversation)
    .reduce<DeviceStatus | null>(
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
// with none going on, its last conversation today: its summary stays until the next one starts
const last = computed(() =>
  props.convs
    .filter((c) => !c.device || c.device === barDev.value?.device)
    .reduce<Conversation | null>((a, c) => (a && a.end > c.end ? a : c), null),
);
const said = (s: number) => (s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`);
const barText = computed(() => {
  const d = barDev.value,
    c = talk.value,
    l = current.value;
  // not recording: the status line above says so already
  const prev = last.value ? `上一段 ${hhmm(last.value.start)}《${convLink(last.value)}》` : "";
  if (!props.status?.recording) return prev;
  if (!c) return "暂无进行中的对话" + (prev && " · " + prev);
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
</script>

<template>
  <div v-if="status && barText" class="pane">
    <div class="pane-line">
      <button
        class="fold"
        :disabled="!body"
        :aria-expanded="body ? paneOpen : undefined"
        @click="toggle"
      >
        <Icon
          v-if="body"
          class="caret"
          :name="paneOpen ? 'chevron-down' : 'chevron-right'"
          :size="14"
        />{{ barText }}
        <span v-if="askErr" class="bad" :title="askErrTitle"> · {{ askErr }}</span>
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
      :style="{ [current && resizable ? 'height' : 'maxHeight']: paneH + 'px' }"
    >
      <template v-if="current">
        <div class="pane-meta">
          {{ current.start > doneTo(current.device) ? "含还没定稿的句子 · " : "" }}对话
          {{ hhmmss(current.start) }} 起 · 到 {{ hhmmss(current.upto) }} 共
          {{ current.sentences }} 句
        </div>
        <Timeline :topics="current.topics" open @jump="$emit('jump', $event)" />
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
      v-if="resizable && body && paneOpen"
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
</template>

<style scoped>
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
  margin-right: 2px;
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
</style>
