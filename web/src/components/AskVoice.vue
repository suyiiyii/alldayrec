<script setup lang="ts">
import { onUnmounted, ref } from "vue";
import { t } from "../i18n";
import { call } from "../lib/api";
import Icon from "./Icon.vue";

// 问 AI said aloud, as 豆包 does it: hold the bar and speak, let go and the question goes, slide up first to drop it;
// `toggle` (a mouse, beside the wide screen's search box): a click starts, a click sends, Escape drops it. What the
// browser records (MediaRecorder: webm, ogg or mp4, as it likes) goes to POST /api/ask/voice, which answers the text
// it heard ("" for nothing): `heard`. `fail`: why not, and whether the microphone itself is out of reach.
const { toggle = false } = defineProps<{ toggle?: boolean }>();
const emit = defineEmits<{ heard: [text: string]; fail: [why: string, mic: boolean] }>();
const MAX_S = 60, // the server takes a minute
  MIN_MS = 400, // a tap, not a question
  AWAY_PX = 60, // slid up this far: letting go drops it
  BARS = 24;
const state = ref<"idle" | "rec" | "hearing">("idle"),
  away = ref(false),
  secs = ref(0),
  wave = ref<number[]>(Array(BARS).fill(0));
let rec: MediaRecorder | null = null,
  done: Promise<Blob> | null = null, // the recording, once rec stopped
  ctx: AudioContext | null = null,
  frame = 0,
  t0 = 0,
  y0 = 0,
  down = false; // held (or clicked on) still: a release while the microphone was being opened drops it

async function start() {
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true }); // the browser's echo, noise and level care: a voice up close
  } catch {
    down = false;
    return emit("fail", t("timeline.noMic"), true);
  }
  if (!down) return stream.getTracks().forEach((x) => x.stop());
  const parts: Blob[] = [],
    r = (rec = new MediaRecorder(stream, { audioBitsPerSecond: 32000 }));
  r.ondataavailable = (e) => e.data.size && parts.push(e.data);
  done = new Promise((ok) => {
    r.onstop = () => {
      stream.getTracks().forEach((x) => x.stop());
      ok(new Blob(parts, { type: r.mimeType }));
    };
  });
  r.start();
  t0 = performance.now();
  state.value = "rec";
  away.value = false;
  if (toggle) addEventListener("keydown", esc);
  // the level, a bar of it every 60 ms, scrolling from the right
  ctx = new AudioContext();
  const an = ctx.createAnalyser();
  ctx.createMediaStreamSource(stream).connect(an);
  const buf = new Float32Array(an.fftSize);
  let last = 0;
  const tick = (now: number) => {
    an.getFloatTimeDomainData(buf);
    const db = 10 * Math.log10(buf.reduce((s, v) => s + v * v, 0) / buf.length + 1e-10);
    if (now - last > 60) {
      last = now;
      wave.value = [...wave.value.slice(1), Math.min(1, Math.max(0, (db + 55) / 40))];
    }
    secs.value = Math.floor((now - t0) / 1000);
    if (now - t0 >= MAX_S * 1000) return void finish(true);
    frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
}

async function finish(send: boolean) {
  down = false;
  if (state.value !== "rec" || !rec) return;
  cancelAnimationFrame(frame);
  removeEventListener("keydown", esc);
  const ms = performance.now() - t0;
  rec.stop();
  rec = null;
  void ctx?.close();
  const blob = await done!;
  wave.value = Array(BARS).fill(0);
  state.value = "idle";
  if (!send) return;
  if (ms < MIN_MS) return emit("fail", t("timeline.tooShort"), false);
  state.value = "hearing";
  try {
    emit(
      "heard",
      (await call<{ text: string }>("/api/ask/voice", { method: "POST", body: blob })).text,
    );
  } catch (e) {
    emit("fail", (e as Error).message, false);
  }
  state.value = "idle";
}
const esc = (e: KeyboardEvent) => e.key === "Escape" && finish(false);

// hold: the bar keeps the finger (pointer capture) wherever it goes until it lets go
function press(e: PointerEvent) {
  if (state.value !== "idle") return;
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  y0 = e.clientY;
  down = true;
  void start();
}
const move = (e: PointerEvent) => state.value === "rec" && (away.value = y0 - e.clientY > AWAY_PX);
function release() {
  if (state.value === "rec") void finish(!away.value);
  down = false;
}
function click() {
  if (state.value === "idle") {
    down = true;
    void start();
  } else void finish(true);
}
onUnmounted(() => void finish(false));
</script>

<template>
  <button
    v-if="toggle"
    type="button"
    class="mic"
    :class="state"
    :style="{ '--lv': wave[BARS - 1] }"
    :disabled="state === 'hearing'"
    :title="state === 'rec' ? t('timeline.clickToSend') : t('timeline.askByVoice')"
    :aria-label="state === 'rec' ? t('timeline.clickToSend') : t('timeline.askByVoice')"
    @click="click"
  >
    <Icon :name="state === 'rec' ? 'square' : 'mic'" :size="16" /><span
      v-if="state === 'rec'"
      class="secs"
      >{{ secs }}″</span
    ><span v-else-if="state === 'hearing'">…</span>
  </button>
  <button
    v-else
    type="button"
    class="talk"
    :class="[state, { away }]"
    :disabled="state === 'hearing'"
    @pointerdown="press"
    @pointermove="move"
    @pointerup="release"
    @pointercancel="finish(false)"
    @contextmenu.prevent
  >
    <template v-if="state === 'rec'">
      <span class="hint" :class="{ bad: away }">{{
        away ? t("timeline.releaseToCancel") : t("timeline.releaseToSend")
      }}</span>
      <span class="wave" aria-hidden="true"
        ><i v-for="(l, i) in wave" :key="i" :style="{ height: 3 + l * 22 + 'px' }"
      /></span>
      <span class="secs">{{ secs }}″</span>
    </template>
    <template v-else>{{
      state === "hearing" ? t("timeline.hearing") : t("timeline.holdToTalk")
    }}</template>
  </button>
</template>

<style scoped>
/* hold to talk: a wide pill; while held it is the level, red once slid up to drop it */
.talk {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 48px;
  border: 0;
  border-radius: var(--radius-pill);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: var(--fs-md);
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
  -webkit-tap-highlight-color: transparent;
}
.talk:active,
.talk.rec {
  background: var(--accent);
  color: var(--surface);
}
.talk.rec.away {
  background: var(--err);
}
.talk:disabled {
  color: var(--muted);
}
/* above the bar, where the sheet's suggestions were */
.hint {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + var(--space-3));
  color: var(--muted);
  font-size: var(--fs-sm);
  font-weight: 400;
}
.hint.bad {
  color: var(--err);
}
.wave {
  display: flex;
  align-items: center;
  gap: 3px;
  height: 26px;
}
.wave i {
  width: 3px;
  border-radius: 2px;
  background: currentColor;
}
.secs {
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
  min-width: 2.5em;
}
/* the desktop's: round beside the search box, ringed by the level while it records */
.mic {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  min-width: 36px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
}
.mic.rec {
  color: var(--err);
  border-color: var(--err);
  box-shadow: 0 0 0 calc(var(--lv, 0) * 6px) var(--err-soft);
}
</style>
