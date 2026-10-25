<script lang="ts">
let stopOther = () => {}; // the card listening now
</script>

<script setup lang="ts">
// Listening in on a recorder (how its mic and box sound): /api/listen polled every second for the device's pieces
// newer than the last, played back to back through Web Audio, with their spectrum. Latency is the phone's piece
// length (the server's SLICE_*: 1 s with the app in front, 20 s in the background, 60 s with Android's screen off) +
// upload + the poll + LEAD_S. Started by a tap (iOS plays nothing otherwise); a page that goes to the background
// stops (iOS suspends it anyway). One per device card on the progress page; one listening on the page at a time.
import { onUnmounted, ref } from "vue";
import { intl, t } from "../i18n";
import { toast } from "../lib/toast";

const { device } = defineProps<{ device: string }>();
const LEAD_S = 1; // played this far behind the newest piece's arrival: pieces come late by their upload's jitter
const MAX_AHEAD_S = 3; // more than this still to play when a piece comes: skip to it (the first piece was an old one)
const MAX_HZ = 8000; // the audio is 16 kHz
const on = ref(false),
  note = ref(""),
  canvas = ref<HTMLCanvasElement | null>(null);
let ctx: AudioContext | null = null,
  an: AnalyserNode | null = null,
  lock: WakeLockSentinel | null = null,
  run = 0, // the current listening; a stop or another start ends the poll loop of the one before
  next = 0, // where the next piece goes on ctx's clock
  queued = new Set<AudioBufferSourceNode>(),
  raf = 0;

function start() {
  if (import.meta.env.MODE === "demo") return toast(t("err.code.demo")); // no recorder to hear (src/demo.ts)
  stopOther();
  stopOther = stop;
  on.value = true;
  note.value = t("listen.connecting");
  // inside the tap: iOS lets only a gesture start audio; "playback" plays with the ring switch on silent (Safari 16.4+)
  const session = (navigator as { audioSession?: { type: string } }).audioSession;
  if (session) session.type = "playback";
  ctx = new AudioContext();
  void ctx.resume();
  an = ctx.createAnalyser();
  an.fftSize = 4096;
  an.connect(ctx.destination);
  next = 0;
  avg = null;
  const me = ++run;
  // a locked screen stops the audio
  navigator.wakeLock?.request("screen").then(
    (l) => (me === run ? (lock = l) : void l.release()),
    () => {},
  );
  void poll(me, device);
  raf = requestAnimationFrame(draw);
}

function stop(why = "") {
  run++;
  on.value = false;
  note.value = why;
  cancelAnimationFrame(raf);
  void ctx?.close();
  ctx = an = null;
  queued.clear();
  void lock?.release();
  lock = null;
}

async function poll(me: number, device: string) {
  let after = "",
    got = 0, // when the last piece came, its seconds
    dur = 0;
  while (me === run && ctx && an) {
    try {
      const r = await fetch(
        `/api/listen?${new URLSearchParams({ device, ...(after && { after }) })}`,
        {
          signal: AbortSignal.timeout(10000),
        },
      );
      if (me !== run || !ctx || !an) return;
      if (r.status === 200) {
        after = r.headers.get("X-After") ?? "";
        const t0 = Number(r.headers.get("X-Start")),
          buf = await ctx.decodeAudioData(await r.arrayBuffer());
        if (me !== run || !an) return;
        const now = ctx.currentTime;
        const behind = next - now > MAX_AHEAD_S;
        if (behind) queued.forEach((s) => s.stop());
        if (next < now || behind) next = now + LEAD_S; // the first piece, ran dry or skipped: a little ahead of now
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(an);
        src.start(next);
        queued.add(src);
        src.onended = () => queued.delete(src);
        const lag = (Date.now() + (next - now) * 1000 - t0) / 1000; // its first sample, recorded to played
        next += buf.duration;
        got = Date.now();
        dur = buf.duration;
        note.value = t("listen.playing", {
          at: new Date(t0).toLocaleTimeString(intl(), { hour12: false }),
          lag: lag.toFixed(1),
        });
      } else if (r.status === 204) {
        if (!got) note.value = t("listen.noAudio");
        else if (ctx.currentTime > next)
          note.value = t("listen.waiting", {
            dur: dur.toFixed(0),
            ago: ((Date.now() - got) / 1000).toFixed(0),
          });
      } else note.value = t("listen.httpError", { status: r.status });
    } catch {
      if (me === run) note.value = t("listen.offline");
    }
    await new Promise((ok) => setTimeout(ok, 1000));
  }
}

// the spectrum (dB, 0 to MAX_HZ, the 1–3 kHz a box can block shaded): now as an area, a slow average (~5 s, only
// while audio plays) as a line; the level (RMS dBFS) as a bar on top
let avg: Float32Array | null = null;
function draw() {
  raf = requestAnimationFrame(draw);
  const c = canvas.value,
    g = c?.getContext("2d");
  if (!c || !g || !an || !ctx) return;
  const w = (c.width = c.clientWidth * devicePixelRatio),
    h = (c.height = c.clientHeight * devicePixelRatio);
  const css = getComputedStyle(c),
    accent = css.getPropertyValue("--accent"),
    muted = css.getPropertyValue("--muted"),
    err = css.getPropertyValue("--err");
  const n = Math.floor((an.frequencyBinCount * MAX_HZ * 2) / ctx.sampleRate),
    f = new Float32Array(an.frequencyBinCount),
    lo = an.minDecibels,
    hi = an.maxDecibels;
  an.getFloatFrequencyData(f);
  const playing = ctx.currentTime < next;
  if (playing && !avg) avg = f.slice(0, n).map((v) => Math.max(v, lo));
  const y = (db: number) =>
    h - ((Math.min(Math.max(db, lo), hi) - lo) / (hi - lo)) * (h - 8 * devicePixelRatio);
  const x = (i: number) => (i / n) * w;
  g.fillStyle = accent;
  g.globalAlpha = 0.06;
  g.fillRect(x((n * 1000) / MAX_HZ), 0, x((n * 2000) / MAX_HZ), h); // 1–3 kHz
  g.globalAlpha = 1;
  g.fillStyle = muted;
  g.font = `${10 * devicePixelRatio}px sans-serif`;
  for (let k = 1; k < MAX_HZ / 1000; k++)
    g.fillText(k + "k", x((n * k * 1000) / MAX_HZ) + 2, h - 2);
  g.beginPath();
  g.moveTo(0, h);
  for (let i = 0; i < n; i++) {
    g.lineTo(x(i), y(f[i]));
    if (playing && avg) avg[i] += (Math.max(f[i], lo) - avg[i]) * 0.003;
  }
  g.lineTo(w, h);
  g.fillStyle = muted;
  g.globalAlpha = 0.35;
  g.fill();
  g.globalAlpha = 1;
  g.beginPath();
  for (let i = 0; avg && i < n; i++) g.lineTo(x(i), y(avg[i]));
  g.strokeStyle = accent;
  g.lineWidth = 1.5 * devicePixelRatio;
  g.stroke();
  const s = new Float32Array(an.fftSize);
  an.getFloatTimeDomainData(s);
  const rms = 10 * Math.log10(s.reduce((a, v) => a + v * v, 0) / s.length + 1e-10);
  g.fillStyle = rms > -6 ? err : accent;
  g.fillRect(0, 0, (Math.max(rms + 60, 0) / 60) * w, 3 * devicePixelRatio);
  g.fillStyle = muted;
  g.fillText(`${rms.toFixed(0)} dBFS`, w - 52 * devicePixelRatio, 14 * devicePixelRatio);
}

const onHidden = () => document.hidden && on.value && stop(t("listen.hidden"));
document.addEventListener("visibilitychange", onHidden);
onUnmounted(() => {
  document.removeEventListener("visibilitychange", onHidden);
  stop();
});
</script>

<template>
  <div class="listen">
    <button :class="{ on }" @click="on ? stop() : start()">
      {{ on ? t("listen.stop") : t("listen.listen") }}
    </button>
    <span v-if="note" class="note">{{ note }}</span>
    <canvas v-if="on" ref="canvas" :title="t('listen.spectrum')"></canvas>
  </div>
</template>

<style scoped>
.listen {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  font-size: var(--fs-xs);
}
.listen button.on {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--surface);
}
.note {
  color: var(--muted);
}
canvas {
  width: 100%;
  height: 90px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}
</style>
