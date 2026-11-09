<script setup lang="ts">
// 「这是你吗？」/「这是 X 吗？」(server selfvoice.unsure): one voice of a recent conversation the server was unsure of, with
// one sentence to play. 是 and 不是 open only once it was heard to its end; 是 is the rename by hand, 不是 keeps the
// voice apart from the name, nothing is learnt from it
import { computed, onMounted, ref } from "vue";
import { t } from "../i18n";
import { get, hhmm, post } from "../lib/api";
import { play, player } from "../lib/audio";
import { dayLabel, today } from "../lib/calendar";
import Icon from "./Icon.vue";

interface Card {
  kind: "a" | "b" | "c"; // a: 我? b: a name the voice is near; c: the name the server gave it
  conv: string;
  label: string;
  name: string;
  start: string;
  clip: { start: string; end: string; device: string | null; text: string | null };
}

const card = ref<Card | null>(null),
  busy = ref(false),
  err = ref(""),
  done = ref(""); // its words' key

async function load() {
  card.value = (await get<{ card: Card | null }>("/api/voice-check")).card;
}
defineExpose({ load });
onMounted(load);

const HINT = { a: "voiceCheck.hintMe", b: "voiceCheck.hint", c: "voiceCheck.hintNamed" };
const clipId = computed(() => "check" + (card.value?.clip.start ?? ""));
const heard = computed(() => player.heard.has(clipId.value));
// the conversation's start; up to two days back: then its day too
const when = (iso: string) => (iso.slice(0, 10) === today() ? "" : dayLabel(iso.slice(0, 10)) + " ") + hhmm(iso);
const src = (c: Card["clip"]) =>
  `/api/audio?${new URLSearchParams({ start: c.start, end: c.end, ...(c.device && { device: c.device }) })}`;

const DONE = { yes: "voiceCheck.doneYes", no: "voiceCheck.doneNo", later: "voiceCheck.doneLater" };
async function answer(a: "yes" | "no" | "later") {
  busy.value = true;
  err.value = "";
  try {
    const r = await post<{ ok: boolean }>("/api/voice-check", { answer: a });
    done.value = r.ok ? DONE[a] : "voiceCheck.stale";
    card.value = null;
  } catch (e) {
    err.value = String((e as Error).message ?? e);
  }
  busy.value = false;
}
</script>

<template>
  <div v-if="card" class="card ask">
    <div class="title">
      {{ card.kind === "a" ? t("voiceCheck.titleMe") : t("voiceCheck.title", { name: card.name }) }}
    </div>
    <p class="hint">
      {{ t(HINT[card.kind], { at: when(card.start), name: card.name }) }}
    </p>
    <button
      class="clip"
      :class="{ playing: player.playing === clipId }"
      :disabled="player.failed.has(clipId)"
      @click="play(clipId, src(card.clip))"
    >
      <Icon :name="player.playing === clipId ? 'square' : 'play'" :size="14" />
      <span>{{ card.clip.text || t("selfVoice.noText") }}</span>
    </button>
    <div class="acts">
      <button class="btn-primary" :disabled="busy || !heard" @click="answer('yes')">
        {{ t("voiceCheck.yes") }}
      </button>
      <button :disabled="busy || !heard" @click="answer('no')">{{ t("voiceCheck.no") }}</button>
      <button :disabled="busy" @click="answer('later')">{{ t("selfVoice.notNow") }}</button>
      <span v-if="!heard" class="muted">{{ t("voiceCheck.listen") }}</span>
      <span v-if="err" class="bad">{{ err }}</span>
    </div>
  </div>
  <div v-else-if="done" class="card ask muted">{{ t(done) }}</div>
</template>

<style scoped>
/* as 「哪个是你？」: the cards that ask something are on the accent's light ground */
.ask {
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
.clip {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  text-align: left;
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
