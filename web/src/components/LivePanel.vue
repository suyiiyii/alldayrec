<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import Icon from "./Icon.vue";
import Timeline from "./Timeline.vue";
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
// goes on. mini: the headline line alone (时间线's live card). The page owns the data: the status, the analyses.
// doneTo: up to where a device's rows are final (the live page knows it from final messages; else the status's)
const props = defineProps<{
  status: Status | null;
  lives: LiveState[];
  doneTo?: (device: string) => string;
  mini?: boolean;
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
const said = (s: number) => (s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`);
// the head: what it is (label), the newest topic or how long the conversation has gone before there is one (headline)
const sub = computed(() =>
  [current.value && `更新于 ${hhmm(current.value.at)}`, barDev.value?.analyzing && "分析中…"]
    .filter(Boolean)
    .join(" · "),
);
const label = computed(() => "AI 实时摘要" + (sub.value && " · " + sub.value));
const headline = computed(() => {
  const c = talk.value,
    l = current.value;
  if (l) return l.topics.at(-1)?.title || "正在总结";
  if (c)
    return (
      `对话进行中 · 已说 ${said(c.speech_s)}` +
      (c.speech_s < MIN_SPEECH_S ? `，满 ${MIN_SPEECH_S / 60} 分钟自动总结` : "")
    );
  return "对话开始后，AI 会边听边总结要点";
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
function toggle() {
  paneOpen.value = !paneOpen.value;
  try {
    localStorage.setItem("livePaneOpen", paneOpen.value ? "1" : "0");
  } catch {}
}
const paneBox = ref<HTMLElement | null>(null);
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
    </div>
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
        :title="fresh ? '' : '没有新的语音'"
        @click="askNow"
      >
        {{ current ? "立即更新" : "立即总结" }}
      </button>
      <Icon
        v-if="current"
        class="caret"
        :name="paneOpen ? 'chevron-down' : 'chevron-right'"
        :size="18"
        @click="toggle"
      />
    </div>
    <div v-if="current" ref="paneBox" class="body">
      <div class="meta">
        {{ current.start > doneTo(current.device) ? "含还没定稿的句子 · " : "" }}对话
        {{ hhmmss(current.start) }} 起 · 到 {{ hhmmss(current.upto) }} 共
        {{ current.sentences }} 句
      </div>
      <Timeline :topics="current.topics" open @jump="$emit('jump', $event)" />
    </div>
  </section>
</template>

<style scoped>
/* 时间线's live card: the headline on one line, its time under it, the button on the right */
.mini {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.spark {
  flex: none;
  color: var(--accent);
  align-self: flex-start;
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
  max-height: 28vh;
  margin-top: var(--space-2);
  border-top: 1px solid var(--line);
}
.meta {
  font-size: var(--fs-xs);
  color: var(--muted);
  padding-top: var(--space-2);
}
@media (max-width: 899px) {
  .shut .body {
    display: none;
  }
}
/* a wide screen: the card is a column of its own, always open, as tall as the screen allows */
@media (min-width: 900px) {
  .caret {
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
    max-height: none;
  }
}
</style>
