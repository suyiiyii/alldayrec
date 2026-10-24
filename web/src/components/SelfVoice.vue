<script setup lang="ts">
// 「哪个是你？」(server selfvoice.py): on an instance that does not know the user's voice yet, the voices that
// recur most, a few sentences of each to play. None is preselected (a whole first day with one person can put them
// first) and there is no %: a tier word. The pick is 我 from the next conversation on; the ones before stay as they are
import { onMounted, ref } from "vue";
import { get, post } from "../lib/api";
import { play, player } from "../lib/audio";

interface Clip {
  start: string;
  end: string;
  device: string | null;
  text: string | null;
}
interface Candidate {
  tier: string; // 很可能 / 可能 / 不确定
  convs: number;
  speech_s: number;
  clips: Clip[];
}
interface Card {
  at: string;
  why: string;
  convs: number;
  candidates: Candidate[];
}

const card = ref<Card | null>(null),
  chosen = ref<number | null>(null),
  busy = ref(false),
  err = ref(""),
  done = ref("");

async function load() {
  card.value = (await get<{ card: Card | null }>("/api/self")).card;
  chosen.value = null;
}
defineExpose({ load });
onMounted(load);

const clipId = (c: Clip) => "self" + c.start;
const src = (c: Clip) =>
  `/api/audio?${new URLSearchParams({ start: c.start, end: c.end, ...(c.device && { device: c.device }) })}`;
const tierText = (t: string) => (t === "不确定" ? "不确定" : t + "是你");

async function answer(a: number | "none" | "later") {
  busy.value = true;
  err.value = "";
  try {
    await post("/api/self", { answer: a });
    done.value =
      typeof a === "number"
        ? "记住了：之后的新对话里，你说的话会显示为「我」；已有的对话不变。"
        : a === "later"
          ? "好的，明天再问。"
          : "好的，等录得更多以后再问。";
    card.value = null;
  } catch (e) {
    err.value = String((e as Error).message ?? e);
  }
  busy.value = false;
}
</script>

<template>
  <div v-if="card" class="card self">
    <div class="title">哪个是你？</div>
    <p class="hint">
      这几个声音在你的录音里出现得最多。逐个听一听，选出你自己的声音。确认后，之后的新对话里你说的话会显示为「我」，已有的对话不变。
    </p>
    <div v-for="(c, i) in card.candidates" :key="i" class="cand">
      <div class="meta">
        <b>声音 {{ i + 1 }}</b> · {{ tierText(c.tier) }} · 出现在 {{ c.convs }} 段对话 · 共
        {{ Math.max(1, Math.round(c.speech_s / 60)) }} 分钟
      </div>
      <button
        v-for="cl in c.clips"
        :key="cl.start"
        class="clip"
        :class="{ playing: player.playing === clipId(cl) }"
        :disabled="player.failed.has(clipId(cl))"
        @click="play(clipId(cl), src(cl))"
      >
        {{ player.playing === clipId(cl) ? "■" : "▶" }} {{ cl.text || "（无文字）" }}
      </button>
      <div v-if="!c.clips.length" class="muted">没有合适的试听句子</div>
      <div class="acts">
        <template v-if="chosen === i">
          <span>确认声音 {{ i + 1 }} 是你？</span>
          <button class="btn-primary" :disabled="busy" @click="answer(i)">确认</button>
          <button :disabled="busy" @click="chosen = null">取消</button>
        </template>
        <button v-else :disabled="busy" @click="chosen = i">这是我</button>
      </div>
    </div>
    <div class="acts">
      <button :disabled="busy" @click="answer('none')">都不是我</button>
      <button :disabled="busy" @click="answer('later')">以后再说</button>
      <span v-if="err" class="bad">{{ err }}</span>
    </div>
  </div>
  <div v-else-if="done" class="card self muted">{{ done }}</div>
</template>

<style scoped>
/* the one card that asks something: on the accent's light ground */
.self {
  background: var(--accent-soft);
}
.title {
  font-weight: 600;
}
.hint {
  font-size: var(--fs-sm);
  color: var(--fg-2);
  margin: 4px 0 8px;
}
.cand {
  border-top: 1px solid var(--line);
  padding: 8px 0;
}
.meta {
  font-size: var(--fs-sm);
  color: var(--muted);
}
.meta b {
  color: var(--fg);
}
.clip {
  display: block;
  width: 100%;
  text-align: left;
  margin-top: 6px;
  color: var(--fg);
}
.clip.playing {
  border-color: var(--accent);
  color: var(--accent);
}
.acts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
  font-size: var(--fs-sm);
}
</style>
