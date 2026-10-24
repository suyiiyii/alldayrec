<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { oggOpus } from "../lib/ogg";
import { RecordStream } from "../lib/stream";

// Two sources, one queue. The phone's microphone: every SLICE_MS the recorder is stopped and restarted, so each
// blob is a complete webm file. The BLE recorder: the Opus frames of its record stream go into Ogg pieces of
// SLICE_MS, no re-encoding. Either way each piece POSTs to /api/chunks with its own start time, the same way the
// app sends its Opus pieces.
const SLICE_MS = 20000,
  BPS = 32000;
// the record stream's service (firmware src/lr_store.h): TIME takes the page's clock (read: the card's state), CTRL
// LIST/READ, DATA what READ sends, LIVE each record as the board makes it
const FRAME_MS = 10,
  STORE = "7c3e0000-1a2b-4c5d-8e9f-6c6976657265",
  TIME = "7c3e0001-1a2b-4c5d-8e9f-6c6976657265",
  CTRL = "7c3e0002-1a2b-4c5d-8e9f-6c6976657265",
  DATA = "7c3e0003-1a2b-4c5d-8e9f-6c6976657265",
  LIVE = "7c3e0004-1a2b-4c5d-8e9f-6c6976657265";
// typed by hand, else a name of its own for this browser, kept from the first visit
const device = ref((localStorage.device ||= `web-recorder-${crypto.randomUUID().slice(0, 8)}`)),
  source = ref<"mic" | "ble">(localStorage.source === "ble" ? "ble" : "mic"),
  upload = ref(localStorage.upload !== "0"),
  log = ref(""),
  sent = ref(0),
  queued = ref(0),
  packets = ref(0),
  hw = ref(""),
  lastPiece = ref(""),
  recording = ref(false);
let rec: MediaRecorder | null = null,
  stream: MediaStream | null = null,
  sending = false,
  fails = 0;
const queue: { blob: Blob; start: number; device: string }[] = [];
const stat = computed(() =>
  recording.value
    ? `录音中 · ${source.value === "ble" ? `收到 ${packets.value} 包 · ${hw.value ? hw.value + " · " : ""}` : ""}已传 ${sent.value} 块 · 待传 ${queued.value}`
    : "已停止",
);
const note = (s: string) =>
  (log.value = (new Date().toLocaleTimeString() + " " + s + "\n" + log.value).slice(0, 4000));
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function send(url: string, body?: Blob, type?: string): Promise<unknown> {
  const r = await fetch(url, {
    method: "POST",
    body,
    headers: type ? { "Content-Type": type } : {},
  });
  if (r.status === 429) {
    await sleep(20000); // server catching up
    return send(url, body, type);
  }
  if (!r.ok)
    throw new Error("HTTP " + r.status + " " + ((await r.json().catch(() => ({}))).detail || ""));
  return r.json();
}
async function drain() {
  // in start order, one at a time, retried until sent
  if (sending) return;
  sending = true;
  while (queue.length) {
    const { blob, start, device: dev } = queue[0];
    try {
      await send(
        `/api/chunks?device=${encodeURIComponent(dev)}&start=${start}`,
        blob,
        blob.type.split(";")[0],
      );
      queue.shift();
      sent.value++;
      fails = 0;
    } catch (e) {
      fails++;
      note(`上传失败 ${(e as Error).message}，${Math.min(fails, 6) * 5} 秒后重试`);
      await sleep(Math.min(fails, 6) * 5000);
    }
    queued.value = queue.length;
  }
  sending = false;
}
function enqueue(blob: Blob, start: number) {
  if (lastPiece.value) URL.revokeObjectURL(lastPiece.value);
  lastPiece.value = URL.createObjectURL(blob); // the newest piece, to listen to on the page
  if (!upload.value) return;
  queue.push({ blob, start, device: target() });
  queued.value = queue.length;
  void drain();
}
// the BLE recorder's audio goes up as the board's own name (LR-P1-BDAE), whatever the device field says; cleaned as the app does
const target = () => (source.value === "ble" && ble?.name?.replace(/[^A-Za-z0-9._-]/g, "_")) || device.value;
function piece(): MediaRecorder {
  const r = new MediaRecorder(stream!, {
      mimeType: "audio/webm;codecs=opus",
      audioBitsPerSecond: BPS,
    }),
    start = Date.now();
  r.ondataavailable = (e) => e.data.size && enqueue(e.data, start);
  r.onerror = (e) => note("录音出错：" + String((e as ErrorEvent).error ?? e));
  r.start();
  setTimeout(() => {
    if (rec === r) {
      r.stop();
      rec = piece();
    }
  }, SLICE_MS);
  return r;
}
async function startMic() {
  stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
  });
  const track = stream.getAudioTracks()[0];
  track.onended = () => note("麦克风被系统收回，录音停了");
  rec = piece();
  note(`开始 · ${track.label} · ${JSON.stringify(track.getSettings())}`);
}

// --- BLE recorder: the page follows its record stream (firmware src/lr_store.h, lib/stream.ts) from where it is when
// the page connects; a lost notify, or the time the link was down, is READ from the card. The board has no clock: the
// page writes its own (TIME) on connect and every 10 minutes, the board puts it into the stream as a 'T' record, and
// each piece's start is the nearest 'T' + 10 ms a frame. What the board recorded before the page connected is the
// phone app's to take.
let ble: BluetoothDevice | null = null,
  ctrlCh: BluetoothRemoteGATTCharacteristic | null = null,
  clockTimer = 0,
  reading = false;
const follower = new RecordStream((frames, start) => enqueue(oggOpus(frames, FRAME_MS), start), SLICE_MS / FRAME_MS);
const replies: Uint8Array[] = [];
const bytes = (e: Event) => {
  const v = (e.target as BluetoothRemoteGATTCharacteristic).value!;
  return new Uint8Array(v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength));
};
const u32 = (b: Uint8Array, at: number) => new DataView(b.buffer, b.byteOffset).getUint32(at, true);
/** The next CTRL reply of one of `ops`, others passed over. */
async function reply(ops: number[], timeoutMs = 15000): Promise<Uint8Array> {
  for (const until = Date.now() + timeoutMs; Date.now() < until; await sleep(20)) {
    const i = replies.findIndex((r) => ops.includes(r[0]));
    if (i >= 0) return replies.splice(i, 1)[0];
  }
  throw new Error(`录音设备没有回应 0x${ops[0].toString(16)}`);
}
async function command(...b: number[]) {
  await ctrlCh!.writeValueWithResponse(new Uint8Array(b));
}
const le32 = (n: number) => [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, n >>> 24];
/** LIST: the files on the card with their sizes, and the one the stream goes on in. */
async function list() {
  replies.length = 0;
  await command(1);
  const files = new Map<number, number>();
  for (;;) {
    const r = await reply([0x81, 0x80]);
    if (r[0] === 0x80) return { files, open: u32(r, 2) };
    files.set(u32(r, 1), u32(r, 5));
  }
}
/** READ the followed file from where the page's bytes end, to the board's end of it. */
async function read() {
  if (reading) return;
  reading = true;
  try {
    const from = follower.end();
    await command(2, ...le32(follower.file), ...le32(from));
    await reply([0x82], 60000);
    if (follower.end() === from) note(`补读 ${follower.file.toString(16)} 没有拿到数据`);
  } finally {
    reading = false;
  }
}
function onLive(e: Event) {
  const b = bytes(e);
  packets.value++;
  if (follower.put(u32(b, 0), u32(b, 4), b.subarray(8)) !== null) void read().catch((x) => note("补读失败 " + x));
}
const onData = (e: Event) => {
  const b = bytes(e);
  follower.put(follower.file, u32(b, 0), b.subarray(4));
};
async function connect() {
  const gatt = await ble!.gatt!.connect();
  const svc = await gatt.getPrimaryService(STORE);
  const ch = async (uuid: string, on: (e: Event) => void) => {
    const c = await svc.getCharacteristic(uuid);
    c.oncharacteristicvaluechanged = on; // assigned, not added: a reconnect hands back the same object
    await c.startNotifications();
    return c;
  };
  ctrlCh = await ch(CTRL, (e) => replies.push(bytes(e)));
  await ch(DATA, onData);
  await ch(LIVE, onLive); // before LIST: nothing the stream adds after it is missed
  const { files, open } = await list();
  if (follower.file !== open) {
    if (follower.file >= 0 && files.has(follower.file)) await read(); // the rest of the file the page had
    // the first time from where the stream is now; after a drop, all of the file it went on in while the link was down
    follower.follow(open, follower.file < 0 ? (files.get(open) ?? 0) : 0);
  }
  await read();
  await recorderState(gatt);
  clearInterval(clockTimer);
  clockTimer = window.setInterval(() => void setClock(gatt).catch(() => {}), 10 * 60000);
  note(`已连上 ${ble!.name}，跟读 ${open.toString(16)}，按设备名 ${target()} 上传`);
}
/** The page's clock into the board's stream: a 'T' record there, which dates the pieces. */
async function setClock(gatt: BluetoothRemoteGATTServer) {
  const time = await (await gatt.getPrimaryService(STORE)).getCharacteristic(TIME);
  const now = new DataView(new ArrayBuffer(8));
  now.setBigUint64(0, BigInt(Date.now()), true);
  await time.writeValueWithResponse(now.buffer);
  return time;
}
/** Sets the board's clock, reads its battery and card, and tells the server, as the app does. */
async function recorderState(gatt: BluetoothRemoteGATTServer) {
  const t = await (await setClock(gatt)).readValue();
  const battery = (await (await (await gatt.getPrimaryService("battery_service")).getCharacteristic("battery_level")).readValue()).getUint8(0);
  const st = { store: t.getInt16(6, true), free_kb: t.getUint32(8, true), total_kb: t.getUint32(12, true), battery };
  const card =
    st.store === -28
      ? "存储卡已满"
      : st.store === -16
        ? "存储卡在 U 盘模式"
        : st.store && !st.total_kb
          ? "没插存储卡"
          : st.store
            ? "存储卡出错"
            : `卡剩 ${(st.free_kb / 1048576).toFixed(1)} GB`;
  hw.value = `电量 ${battery}% · ${card}`;
  await send(`/api/device-status?device=${encodeURIComponent(target())}`, new Blob([JSON.stringify(st)], { type: "application/json" }), "application/json").catch(() => {});
}
async function onDrop() {
  clearInterval(clockTimer);
  if (!recording.value) return; // stopped on purpose
  note("蓝牙断了，重连中");
  for (let i = 1; recording.value && !ble?.gatt?.connected; i++) {
    try {
      await connect();
    } catch (e) {
      note(`重连失败 ${(e as Error).message}，${Math.min(i, 6) * 5} 秒后再试`);
      await sleep(Math.min(i, 6) * 5000);
    }
  }
}
async function startBle() {
  ble = await navigator.bluetooth.requestDevice({ filters: [{ services: [STORE] }], optionalServices: ["battery_service"] });
  ble.addEventListener("gattserverdisconnected", () => void onDrop());
  follower.follow(-1, 0);
  packets.value = 0;
  await connect();
}

async function start() {
  localStorage.device = device.value;
  localStorage.source = source.value;
  localStorage.upload = upload.value ? "1" : "0";
  sent.value = 0;
  recording.value = true;
  try {
    await (source.value === "ble" ? startBle() : startMic());
  } catch (e) {
    recording.value = false;
    throw e;
  }
}
async function stop() {
  const at = Date.now(),
    dev = target();
  recording.value = false;
  if (rec) {
    const r = rec;
    rec = null;
    r.stop();
    stream!.getTracks().forEach((t) => t.stop());
  }
  if (ble) {
    ble.gatt?.disconnect();
    ble = null;
    follower.cut();
  }
  note("停止，等最后几块传完");
  await sleep(500);
  while (queue.length || sending) await sleep(500);
  if (upload.value)
    await send(`/api/chunks/end?device=${encodeURIComponent(dev)}&t=${at}`).catch((e) =>
      note("结束信号失败 " + e.message),
    );
  note("已停止");
}
const toggle = () => (recording.value ? stop() : start().catch((e) => note("启动失败 " + e)));
const onVisible = () =>
  recording.value && note(`页面${document.hidden ? "进后台" : "回前台"}，已传 ${sent.value} 块`);
onMounted(() => document.addEventListener("visibilitychange", onVisible));
onUnmounted(() => document.removeEventListener("visibilitychange", onVisible));
</script>

<template>
  <div class="page">
    <div class="top">
      <div class="ctl">
        <input v-model="device" placeholder="设备名" />
        <select v-model="source" :disabled="recording">
          <option value="mic">本机麦克风</option>
          <option value="ble">蓝牙录音硬件</option>
        </select>
        <label><input v-model="upload" type="checkbox" :disabled="recording" />上传</label>
        <button class="btn-primary" :class="{ on: recording }" @click="toggle">
          {{ recording ? "停止" : "开始录音" }}
        </button>
      </div>
      <p class="stat">{{ stat }}</p>
      <audio v-if="lastPiece" :src="lastPiece" controls></audio>
      <details>
        <summary>日志</summary>
        <div class="log">{{ log }}</div>
      </details>
    </div>
    <iframe src="/live" title="字幕"></iframe>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.top {
  padding: 10px 16px 6px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.ctl {
  display: flex;
  gap: 10px;
  align-items: center;
}
.ctl button {
  font-size: var(--fs-md);
  white-space: nowrap;
}
.ctl button.on {
  background: var(--err);
  border-color: var(--err);
}
.ctl label {
  font-size: var(--fs-sm);
  white-space: nowrap;
}
.ctl select {
  padding: 8px 6px;
}
audio {
  width: 100%;
  height: 32px;
  margin-top: 4px;
}
.ctl input:not([type="checkbox"]) {
  padding: 8px 10px;
  flex: 1;
  min-width: 0;
}
.stat {
  color: var(--fg-2);
  font-size: var(--fs-sm);
  margin: 6px 0 0;
}
details {
  font-size: var(--fs-xs);
  color: var(--muted);
  margin-top: 4px;
}
.log {
  font:
    12px ui-monospace,
    monospace;
  white-space: pre-wrap;
  color: var(--muted);
  max-height: 30vh;
  overflow: auto;
}
iframe {
  flex: 1;
  border: 0;
  width: 100%;
}
</style>
