<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Icon from "../components/Icon.vue";
import { t } from "../i18n";
import { caughtUp, get, hhmm, post, type Status } from "../lib/api";
import { dayLabel } from "../lib/calendar";
import { duration, guess, type From } from "../lib/importStart";
import { useSocket } from "../lib/socket";
import { clock, isoLocal } from "../lib/track";

// 导入录音: the files picked here go up as one batch, a device of its own (import-<when>), each file one piece at its
// start time, in time order (a device's audio is transcribed once, from its earliest on: a file earlier than where
// that has got to is only kept), then /api/chunks/end closes the batch's last conversation. The batches sent before
// (status.imports) are listed under it, apart from the recorders on 我的.
interface Item {
  file: File;
  start: number; // unix ms, as guessed or set here
  from: From;
  seconds: number | null; // as the browser reads it; null: it cannot
  sent: number; // the upload's share sent, 0..1
  state: "" | "sending" | "done" | "failed";
  error?: string;
  got?: number; // the seconds the server decoded
}
const items = ref<Item[]>([]);
const stamp = () => isoLocal(Date.now()).slice(0, 19).replace(/[-:]/g, "").replace("T", "-"); // 20261026-003000
const label = ref(stamp());
const device = computed(() => "import-" + (label.value.trim().replace(/\s+/g, "-") || stamp()));
const busy = ref(false),
  finished = ref(false),
  endError = ref("");
// once a file is up the batch's times and name stay: the rest must go after it, under the same name (a file the
// server refused can still be taken out)
const locked = computed(() => busy.value || items.value.some((i) => i.state === "done"));
const sorted = computed(() => [...items.value].sort((a, b) => a.start - b.start));

const status = ref<Status | null>(null);
const load = async () => (status.value = await get<Status>("/api/status"));
onMounted(load);
useSocket((m) => m.type === "status" && (status.value = m), load);
const used = computed(
  () => !locked.value && !!status.value?.imports?.some((r) => r.device === device.value),
);

async function pick(e: Event) {
  const input = e.target as HTMLInputElement;
  const files = [...(input.files ?? [])];
  input.value = ""; // the same file again is a change too
  const add = await Promise.all(
    files.map(async (file) => {
      const seconds = await duration(file);
      const g = await guess(file, seconds);
      return { file, start: g.at, from: g.from, seconds, sent: 0, state: "" } as Item;
    }),
  );
  items.value = [...items.value, ...add].sort((a, b) => a.start - b.start);
}
const setStart = (it: Item, v: string) => {
  const ms = +new Date(v);
  if (Number.isFinite(ms)) it.start = ms;
};
const end = (it: Item) => it.start + (it.got ?? it.seconds ?? 0) * 1000;
// what may be wrong with a file's start: it ends in the future, or the file before it runs into it
function warn(it: Item) {
  if (end(it) > Date.now() + 60_000) return t("import.future");
  const prev = sorted.value[sorted.value.indexOf(it) - 1];
  return prev && end(prev) > it.start
    ? t("import.overlap", { name: prev.file.name, d: clock(end(prev) - it.start) })
    : "";
}

// one file to /api/chunks, its upload's progress as it goes; the seconds the server decoded
const send = (it: Item) =>
  new Promise<number>((ok, fail) => {
    const x = new XMLHttpRequest();
    x.open(
      "POST",
      `/api/chunks?device=${encodeURIComponent(device.value)}&start=${Math.round(it.start)}`,
    );
    x.setRequestHeader("Content-Type", it.file.type || "application/octet-stream");
    x.upload.onprogress = (e) => e.lengthComputable && (it.sent = e.loaded / e.total);
    x.onload = () =>
      x.status === 200
        ? ok(JSON.parse(x.responseText).seconds)
        : fail(
            new Error(
              x.status === 400
                ? t("import.undecodable")
                : x.status === 401
                  ? t("err.auth")
                  : x.status >= 500
                    ? t("err.server")
                    : t("err.http", { status: x.status }),
            ),
          );
    x.onerror = () => fail(new Error(t("err.network")));
    x.send(it.file);
  });
// the files not up yet, in time order, stopping at a failure (retry goes on from there); then the batch's end
async function go() {
  busy.value = true;
  endError.value = "";
  for (const it of sorted.value) {
    if (it.state === "done") continue;
    it.state = "sending";
    it.sent = 0;
    try {
      it.got = await send(it);
      it.state = "done";
    } catch (e) {
      it.state = "failed";
      it.error = (e as Error).message;
      busy.value = false;
      return;
    }
  }
  try {
    const last = Math.round(Math.max(...items.value.map(end)));
    await post(`/api/chunks/end?device=${encodeURIComponent(device.value)}&t=${last}`);
    finished.value = true;
    void load();
  } catch (e) {
    endError.value = (e as Error).message;
  }
  busy.value = false;
}
function again() {
  items.value = [];
  finished.value = false;
  label.value = stamp();
}
// the days the batch's audio falls on, for links to them
const days = computed(() =>
  [
    ...new Set(
      items.value.flatMap((i) => [isoLocal(i.start).slice(0, 10), isoLocal(end(i)).slice(0, 10)]),
    ),
  ].sort(),
);
const when = (iso: string) => `${dayLabel(iso.slice(0, 10))} ${hhmm(iso)}`;
const span = (a: string, b: string) =>
  a.slice(0, 10) === b.slice(0, 10) ? `${when(a)}–${hhmm(b)}` : `${when(a)} – ${when(b)}`;
const left = (dev: string) => {
  const d = status.value?.devices?.find((x) => x.device === dev);
  return d && !caughtUp(d)
    ? t("import.transcribing", { n: Math.max(1, Math.round(d.left_s / 60)) })
    : "";
};
</script>

<template>
  <AppHeader />
  <main>
    <h1>{{ t("title.import") }}</h1>
    <section v-if="finished" class="card result">
      <p>{{ t("import.done", { n: items.length }, items.length) }}</p>
      <p class="days">
        <RouterLink
          v-for="d in days"
          :key="d"
          class="chip"
          :to="{ path: '/', query: { day: d } }"
          >{{ dayLabel(d) }}</RouterLink
        >
      </p>
      <button class="btn" @click="again">{{ t("import.again") }}</button>
    </section>
    <section v-else class="card">
      <p class="muted lead">{{ t("import.lead") }}</p>
      <label class="btn btn-primary pick" :class="{ off: locked }">
        <Icon name="upload" :size="18" />{{ t("import.pick") }}
        <input
          type="file"
          multiple
          accept="audio/*,.m4a,.mp3,.wav,.aac,.flac,.ogg,.opus,.amr,.wma"
          :disabled="locked"
          @change="pick"
        />
      </label>
      <ul v-if="items.length" class="files">
        <li v-for="it in items" :key="it.file.name + it.file.lastModified" class="file">
          <div class="top">
            <b class="name">{{ it.file.name }}</b>
            <button
              v-if="!locked || it.state === 'failed'"
              class="btn-ghost x"
              :aria-label="t('import.remove')"
              @click="items.splice(items.indexOf(it), 1)"
            >
              <Icon name="x" :size="18" />
            </button>
          </div>
          <div class="row">
            <input
              type="datetime-local"
              step="1"
              :value="isoLocal(it.start).slice(0, 19)"
              :disabled="locked"
              @change="setStart(it, ($event.target as HTMLInputElement).value)"
            />
            <span class="muted sub"
              >{{ it.seconds != null ? clock(it.seconds * 1000) + " · " : ""
              }}{{ t(`import.from.${it.from}`) }}</span
            >
          </div>
          <p v-if="warn(it)" class="warn">{{ warn(it) }}</p>
          <p v-if="it.state === 'sending'" class="muted sub">
            <progress :value="it.sent" max="1" />
            {{
              it.sent < 1
                ? t("import.sending", { p: Math.floor(it.sent * 100) })
                : t("import.decoding")
            }}
          </p>
          <p v-else-if="it.state === 'done'" class="ok sub">
            {{ t("import.sent", { d: clock((it.got ?? 0) * 1000) }) }}
          </p>
          <p v-else-if="it.state === 'failed'" class="bad sub">
            {{ t("import.failed", { error: it.error }) }}
            <button class="btn" :disabled="busy" @click="go">{{ t("import.retry") }}</button>
          </p>
        </li>
      </ul>
      <template v-if="items.length">
        <label class="batch">
          <span class="muted">{{ t("import.batch") }}</span>
          <span class="name-in"
            ><span class="muted">import-</span><input v-model="label" :disabled="locked"
          /></span>
        </label>
        <p v-if="used" class="warn">{{ t("import.nameUsed") }}</p>
        <p v-if="endError" class="bad sub">
          {{ t("import.failed", { error: endError }) }}
          <button class="btn" :disabled="busy" @click="go">{{ t("import.retry") }}</button>
        </p>
        <button
          v-if="!items.some((i) => i.state === 'failed') && !endError"
          class="btn-primary go"
          :disabled="busy || used"
          @click="go"
        >
          {{ t("import.go", { n: items.length }, items.length) }}
        </button>
      </template>
    </section>
    <section v-if="status?.imports?.length" class="card history">
      <h2>{{ t("import.history") }}</h2>
      <RouterLink
        v-for="r in status.imports"
        :key="r.device"
        class="batch-row"
        :to="{ path: '/', query: { day: (r.audio_start ?? r.audio_end ?? '').slice(0, 10) } }"
      >
        <span class="text"
          ><b>{{ r.device }}</b
          ><span class="muted sub"
            >{{ span(r.audio_start ?? "", r.audio_end ?? "")
            }}{{ left(r.device) && " · " + left(r.device) }}</span
          ></span
        >
        <Icon name="chevron-right" :size="18" class="go-icon" />
      </RouterLink>
    </section>
  </main>
</template>

<style scoped>
h1 {
  margin: var(--space-3) 0 var(--space-4);
}
.lead {
  margin: 0 0 var(--space-3);
  font-size: var(--fs-sm);
}
/* the file input hidden inside its button-styled label: the label's tap opens the chooser */
.pick {
  gap: var(--space-2);
}
.pick input {
  display: none;
}
.pick.off {
  opacity: 0.5;
  pointer-events: none;
}
.files {
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
}
.file {
  padding: var(--space-3) 0;
  border-top: 1px solid var(--line);
}
.top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.x {
  padding: 0 var(--space-2);
  color: var(--muted);
}
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1) var(--space-3);
  margin-top: var(--space-1);
}
.row input {
  min-height: 36px;
  padding: 0 var(--space-2);
}
.sub {
  font-size: var(--fs-sm);
  margin: var(--space-1) 0 0;
}
.row .sub {
  margin: 0;
}
.warn {
  margin: var(--space-1) 0 0;
  font-size: var(--fs-sm);
  color: var(--warn);
}
.ok {
  color: var(--ok);
}
progress {
  width: 120px;
  vertical-align: middle;
  accent-color: var(--accent);
}
.bad .btn {
  margin-left: var(--space-2);
}
.batch {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1) var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--line);
  font-size: var(--fs-sm);
}
.name-in {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.name-in input {
  min-height: 36px;
  padding: 0 var(--space-2);
  width: 12em;
}
.go {
  width: 100%;
  margin-top: var(--space-4);
}
.result p:first-child {
  margin-top: 0;
}
.days {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.history h2 {
  margin: 0 0 var(--space-1);
  font-size: var(--fs-md);
}
.batch-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  color: var(--fg);
}
.batch-row + .batch-row {
  border-top: 1px solid var(--line);
}
.text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.text b {
  overflow-wrap: anywhere;
}
.go-icon {
  color: var(--muted);
}
</style>
