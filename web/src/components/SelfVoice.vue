<script setup lang="ts">
// 「哪个是你？」(server selfvoice.py): on an instance that does not know the user's voice yet, the voices that
// recur most, a few sentences of each to play. None is preselected (a whole first day with one person can put them
// first) and there is no %: a tier word. The pick is 我 from the next conversation on; the ones before stay as they are
import { onMounted, ref } from "vue";
import { t } from "../i18n";
import { get, post } from "../lib/api";
import { play, player } from "../lib/audio";
import Avatar from "./Avatar.vue";
import Icon from "./Icon.vue";

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
  done = ref(""); // its words' key

async function load() {
  card.value = (await get<{ card: Card | null }>("/api/self")).card;
  chosen.value = null;
}
defineExpose({ load });
onMounted(load);

const clipId = (c: Clip) => "self" + c.start;
const src = (c: Clip) =>
  `/api/audio?${new URLSearchParams({ start: c.start, end: c.end, ...(c.device && { device: c.device }) })}`;
// the server's tier words (data) as the page says them
const TIERS: Record<string, string> = { 很可能: "likely", 可能: "maybe", 不确定: "unsure" };
const tierText = (tier: string) => (TIERS[tier] ? t(`selfVoice.tier.${TIERS[tier]}`) : tier);

async function answer(a: number | "none" | "later") {
  busy.value = true;
  err.value = "";
  try {
    await post("/api/self", { answer: a });
    done.value =
      typeof a === "number"
        ? "selfVoice.picked"
        : a === "later"
          ? "selfVoice.later"
          : "selfVoice.none";
    card.value = null;
  } catch (e) {
    err.value = String((e as Error).message ?? e);
  }
  busy.value = false;
}
</script>

<template>
  <div v-if="card" class="card self">
    <div class="title">{{ t("selfVoice.title") }}</div>
    <p class="hint">{{ t("selfVoice.hint") }}</p>
    <div v-for="(c, i) in card.candidates" :key="i" class="cand">
      <div class="head">
        <Avatar :name="'S' + (i + 1)" :size="32" />
        <div class="meta">
          <b>{{ t("speakers.voice", { n: i + 1 }) }}</b> · {{ tierText(c.tier) }}<br />{{
            t(
              "selfVoice.seen",
              { n: c.convs, min: Math.max(1, Math.round(c.speech_s / 60)) },
              c.convs,
            )
          }}
        </div>
      </div>
      <button
        v-for="cl in c.clips"
        :key="cl.start"
        class="clip"
        :class="{ playing: player.playing === clipId(cl) }"
        :disabled="player.failed.has(clipId(cl))"
        @click="play(clipId(cl), src(cl))"
      >
        <Icon :name="player.playing === clipId(cl) ? 'square' : 'play'" :size="14" />
        <span>{{ cl.text || t("selfVoice.noText") }}</span>
      </button>
      <div v-if="!c.clips.length" class="muted">{{ t("selfVoice.noClips") }}</div>
      <div class="acts">
        <template v-if="chosen === i">
          <span>{{ t("selfVoice.confirmQ", { n: i + 1 }) }}</span>
          <button class="btn-primary" :disabled="busy" @click="answer(i)">
            {{ t("selfVoice.confirm") }}
          </button>
          <button :disabled="busy" @click="chosen = null">{{ t("selfVoice.cancel") }}</button>
        </template>
        <button v-else :disabled="busy" @click="chosen = i">{{ t("selfVoice.isMe") }}</button>
      </div>
    </div>
    <div class="acts">
      <button :disabled="busy" @click="answer('none')">{{ t("selfVoice.noneMe") }}</button>
      <button :disabled="busy" @click="answer('later')">{{ t("selfVoice.notNow") }}</button>
      <span v-if="err" class="bad">{{ err }}</span>
    </div>
  </div>
  <div v-else-if="done" class="card self muted">{{ t(done) }}</div>
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
/* each voice a white card on the light ground, laid out like a person on 人物 */
.cand {
  background: var(--surface);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  margin-top: var(--space-2);
}
.head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.meta {
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: var(--muted);
}
.meta b {
  font-size: var(--fs-md);
  color: var(--fg);
}
.clip {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  text-align: left;
  margin-top: var(--space-2);
}
.clip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
