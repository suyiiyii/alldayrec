<script setup lang="ts">
import { onUnmounted, ref } from "vue";
import { t } from "../i18n";
import { blocks, resampler } from "../lib/pcm";
import Icon from "./Icon.vue";

// 问 AI said aloud, as 豆包 does it: hold the bar and speak, the words show as they are heard, let go and the question
// goes, slide up first to drop it; `toggle` (a mouse, beside the wide screen's search box): a click starts, a click
// sends, Escape drops it. The microphone goes as 16 kHz PCM (lib/pcm.ts), a block every 0.1 s, on the WebSocket
// /api/ask/voice/ws, which answers the words so far (`said`, for the page to show; "" once done) and, once let go,
// the text it heard ("" for nothing): `heard`. `fail`: why not, and whether the microphone itself is out of reach.
const { toggle = false } = defineProps<{ toggle?: boolean }>();
const emit = defineEmits<{
  heard: [text: string];
  said: [text: string];
  fail: [why: string, mic: boolean];
}>();
const MAX_S = 60, // the server takes a minute
  MIN_MS = 400, // a tap, not a question
  AWAY_PX = 60, // slid up this far: letting go drops it
  BARS = 24;
const state = ref<"idle" | "rec" | "hearing">("idle"),
  away = ref(false),
  secs = ref(0),
  wave = ref<number[]>(Array(BARS).fill(0));
let ctx: AudioContext | null = null,
  mic: MediaStream | null = null,
  ws: WebSocket | null = null,
  put: (x: Int16Array<ArrayBuffer> | string) => void = () => {}, // to the socket, queued until it is open
  out: ReturnType<typeof blocks> | null = null,
  result: Promise<string | Error> | null = null, // the text heard, or why not
  frame = 0,
  t0 = 0,
  y0 = 0,
  peak = 0, // the loudest block's mean square since the last bar
  down = false; // held (or clicked on) still: a release while the microphone was being opened drops it

// The audio thread's blocks of samples to the page, 2048 at a time (43 ms at 48 kHz), as they come
const WORKLET = `registerProcessor("pcm", class extends AudioWorkletProcessor {
  b = new Float32Array(2048); n = 0;
  process([[x]]) {
    if (x) { this.b.set(x, this.n); this.n += x.length; }
    if (this.n === this.b.length) { this.port.postMessage(this.b, [this.b.buffer]); this.b = new Float32Array(2048); this.n = 0; }
    return true;
  }
});`;
let workletUrl = "";

// in the press or the click itself: iOS lets an AudioContext run only when it starts in the user's gesture
async function start() {
  down = true;
  const c = (ctx = new AudioContext());
  void c.resume();
  let stream: MediaStream;
  try {
    // the browser's own echo, noise and level care, as it has them by default: a voice up close
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    if (ctx === c) drop();
    down = false;
    const denied = (e as Error).name === "NotAllowedError";
    return emit("fail", t(denied ? "timeline.micDenied" : "timeline.noMic"), true);
  }
  if (!down || ctx !== c) return stream.getTracks().forEach((x) => x.stop()); // let go already: drop() went before
  // the socket once there is a microphone (no listening opened for nothing); what comes before it opens waits
  const w = (ws = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ask/voice/ws`,
    )),
    queue: (Int16Array<ArrayBuffer> | string)[] = [];
  put = (x) => (w.readyState === WebSocket.OPEN ? w.send(x) : queue.push(x));
  w.onopen = () => queue.splice(0).forEach((x) => w.send(x));
  result = new Promise((ok) => {
    w.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "partial") return emit("said", m.text);
      ok(m.type === "final" ? m.text : new Error(t("err.code.asr_failed")));
    };
    w.onclose = () => {
      ok(new Error(t("timeline.netLost")));
      if (state.value === "rec") void finish(true); // the network went while it was said: say so at once
    };
  });
  mic = stream;
  const src = c.createMediaStreamSource(stream),
    r = resampler(c.sampleRate), // 48 kHz on iPhones, whatever it asks for
    o = (out = blocks(put));
  const take = (x: Float32Array) => {
    peak = Math.max(peak, x.reduce((s, v) => s + v * v, 0) / x.length);
    o.push(r(x));
  };
  try {
    workletUrl ||= URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    await c.audioWorklet.addModule(workletUrl);
    const n = new AudioWorkletNode(c, "pcm");
    n.port.onmessage = (e) => take(e.data);
    src.connect(n).connect(c.destination); // pulled by the output (it writes silence), else it never runs
  } catch {
    // an older WebView without AudioWorklet: the deprecated main-thread node does the same
    const n = c.createScriptProcessor(2048, 1, 1);
    n.onaudioprocess = (e) => {
      take(e.inputBuffer.getChannelData(0));
      e.outputBuffer.getChannelData(0).fill(0);
    };
    src.connect(n).connect(c.destination);
  }
  if (!down || ctx !== c) return; // let go while the worklet loaded: drop() went before
  t0 = performance.now();
  state.value = "rec";
  away.value = false;
  if (toggle) addEventListener("keydown", esc);
  // the level, a bar of it every 60 ms, scrolling from the right
  let last = 0;
  const tick = (now: number) => {
    if (now - last > 60) {
      last = now;
      const db = 10 * Math.log10(peak + 1e-10);
      peak = 0;
      wave.value = [...wave.value.slice(1), Math.min(1, Math.max(0, (db + 55) / 40))];
    }
    secs.value = Math.floor((now - t0) / 1000);
    if (now - t0 >= MAX_S * 1000) return void finish(true);
    frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
}

// the microphone off; the socket too unless it still has the answer to give
function stopMic() {
  cancelAnimationFrame(frame);
  removeEventListener("keydown", esc);
  mic?.getTracks().forEach((x) => x.stop());
  void ctx?.close();
  mic = ctx = out = null;
  wave.value = Array(BARS).fill(0);
}
function drop() {
  stopMic();
  if (ws) ws.onopen = ws.onmessage = ws.onclose = null;
  ws?.close(); // closed: the server stops listening at once and answers nothing
  ws = result = null;
  emit("said", "");
}

async function finish(send: boolean) {
  down = false;
  if (state.value !== "rec") return void (state.value === "idle" && drop()); // let go while the microphone opened
  const ms = performance.now() - t0,
    r = result!;
  out?.flush();
  stopMic();
  if (!send || ms < MIN_MS) {
    drop();
    state.value = "idle";
    return void (send && emit("fail", t("timeline.tooShort"), false));
  }
  put(JSON.stringify({ type: "end" }));
  state.value = "hearing";
  const got = await r;
  drop();
  state.value = "idle";
  if (got instanceof Error) emit("fail", got.message, false);
  else emit("heard", got);
}
const esc = (e: KeyboardEvent) => e.key === "Escape" && finish(false);

// hold: the bar keeps the finger (pointer capture) wherever it goes until it lets go
function press(e: PointerEvent) {
  if (state.value !== "idle" || down) return;
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  y0 = e.clientY;
  void start();
}
const move = (e: PointerEvent) => state.value === "rec" && (away.value = y0 - e.clientY > AWAY_PX);
function release() {
  void finish(state.value === "rec" && !away.value);
}
function click() {
  if (state.value === "idle" && !down) void start();
  else void finish(true);
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
