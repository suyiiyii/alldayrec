<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useTemplateRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import DayPicker from "../components/DayPicker.vue";
import Icon from "../components/Icon.vue";
import SelfVoice from "../components/SelfVoice.vue";
import TodoMarks from "../components/TodoMarks.vue";
import {
  errorTitle,
  get,
  hhmm,
  minutes,
  MOSTLY_MEDIA,
  post,
  type Conversation,
  type Status,
} from "../lib/api";
import { shiftDay, today } from "../lib/calendar";
import { speakerLabel } from "../lib/speakers";
import { useSocket } from "../lib/socket";

// newest first, PAGE at a time as the end scrolls into view; words, one tag and the kind filter it; ?day= starts
// the list at the end of that day (older ones keep coming as it scrolls)
const PAGE = 30;
const route = useRoute(),
  router = useRouter();
const q = ref(String(route.query.q ?? "")),
  tag = ref(String(route.query.tag ?? "")),
  kind = ref(String(route.query.kind ?? "对话")),
  day = ref(String(route.query.day ?? ""));
// the day of the topmost card on screen, shown by the picker
const seen = ref(""),
  find = useTemplateRef<HTMLElement>("find");
const tags = ref<[string, number][]>([]),
  items = ref<Conversation[]>([]),
  open = ref<Status["conversation"]>(null);
const loading = ref(false),
  done = ref(false);
const more = useTemplateRef<HTMLElement>("more"),
  selfCard = useTemplateRef<InstanceType<typeof SelfVoice>>("selfCard");
let seq = 0;

// 问 AI: the words in the search box as a question (POST /api/ask, 10-30 s); [c…] in the answer link to the conversation.
// The answer is kept for the tab: back from a cited conversation it is still there
interface Reply {
  answer: string;
  sources: { id: string; start: string; title?: string | null }[];
  calls: unknown[];
  secs: number;
}
const kept = () => {
  try {
    return JSON.parse(sessionStorage.getItem("ask") ?? "null") as Reply | null;
  } catch {
    return null;
  }
};
const reply = ref<Reply | null>(kept()),
  asking = ref(false),
  askErr = ref(""),
  askErrTitle = ref("");
watch(reply, (r) => {
  try {
    sessionStorage.setItem("ask", JSON.stringify(r));
  } catch {}
});
async function askAI() {
  asking.value = true;
  askErr.value = "";
  const t0 = Date.now();
  try {
    const r = await post<Reply>("/api/ask", { q: q.value });
    reply.value = { ...r, secs: Math.round((Date.now() - t0) / 1000) };
  } catch (e) {
    askErr.value = String((e as Error).message ?? e);
    askErrTitle.value = errorTitle(e);
  }
  asking.value = false;
}
const answer = computed(() => {
  const by = new Map((reply.value?.sources ?? []).map((s) => [s.id, s]));
  return (reply.value?.answer ?? "")
    .split(/(c\d{14}(?:-[A-Za-z0-9_]+)?)/)
    .map((t) => ({ t, s: by.get(t) ?? by.get(t.split("-")[0]) }));
});

// the newest daily recap (server recap.py, written each morning for the day before), folded to its headline
interface Recap {
  date: string;
  conversations: number;
  headline: string;
  highlights: { text: string; conv: string | null }[];
  // text: the summary's (完成 / 不对 name it by it), short: the recap's wording (a recap written before there was a
  // short holds the wording in text: no buttons, they would name no todo); due: as said that day
  todos: { conv: string; text: string; short?: string; to?: string | null; due?: string | null }[];
  people: { who: string; note: string }[];
}
const recap = ref<Recap | null>(null);

// my todos due today or before (summary prompt v8 dates them), not marked done or wrong
interface Due {
  conv: string;
  text: string;
  to?: string | null;
  due?: string | null;
  due_date: string;
  at?: string | null;
}
const due = ref<Due[]>([]);
// a todo marked 完成 or 不对 leaves both cards at once (the server leaves it out from now on)
function closed(conv: string, text: string) {
  due.value = due.value.filter((t) => t.conv !== conv || t.text !== text);
  if (recap.value)
    recap.value.todos = recap.value.todos.filter((t) => t.conv !== conv || t.text !== text);
}

const search = (before: string) =>
  get<Conversation[]>("/api/search", {
    q: q.value,
    tag: tag.value,
    kind: kind.value,
    before: before || (day.value ? shiftDay(day.value, 1) + "T00:00:00" : ""),
    limit: String(PAGE),
  });
async function restart() {
  // the old list stays on screen until the new first page is in: no blank flash
  router.replace({
    query: {
      ...(q.value && { q: q.value }),
      ...(tag.value && { tag: tag.value }),
      ...(kind.value !== "对话" && { kind: kind.value }),
      ...(day.value && { day: day.value }),
    },
  });
  const my = ++seq,
    plain = !q.value && !tag.value && kind.value === "对话" && !day.value;
  loading.value = true;
  if (plain) selfCard.value?.load(); // after a reconnect too: it may have popped meanwhile
  const [st, cs] = await Promise.all([plain ? get<Status>("/api/status") : null, search("")]);
  if (my !== seq) return; // a newer search started meanwhile
  open.value = st?.conversation ?? null;
  items.value = cs;
  done.value = cs.length < PAGE;
  loading.value = false;
  spy();
}
async function page() {
  if (loading.value || done.value) return;
  const my = seq;
  loading.value = true;
  const cs = await search(items.value.at(-1)?.start ?? "");
  if (my !== seq) return;
  items.value.push(...cs);
  done.value = cs.length < PAGE;
  loading.value = false;
}
const dayOf = (i: number) => {
  const d = items.value[i].start.slice(0, 10);
  return i === 0 || items.value[i - 1].start.slice(0, 10) !== d ? d : "";
};

let timer = 0;
watch(q, () => {
  clearTimeout(timer);
  timer = window.setTimeout(restart, 300);
});
watch([tag, kind, day], restart);
watch(day, () => window.scrollTo(0, 0));
const pick = (d: string) => (day.value = d === today() ? "" : d);

function spy() {
  // the last day header above the bottom of the sticky search bar is the day being read
  const edge = (find.value?.getBoundingClientRect().bottom ?? 0) + 8;
  let d = items.value[0]?.start.slice(0, 10) ?? day.value;
  for (const el of document.querySelectorAll<HTMLElement>("[data-day]"))
    if (el.getBoundingClientRect().top < edge) d = el.dataset.day!;
  seen.value = d;
}

let io: IntersectionObserver;
onMounted(async () => {
  window.addEventListener("scroll", spy, { passive: true });
  io = new IntersectionObserver((es) => es[0].isIntersecting && page());
  if (more.value) io.observe(more.value);
  get<Recap | null>("/api/recap").then((r) => (recap.value = r));
  get<Due[]>("/api/todos/due").then((d) => (due.value = d));
  get<Record<string, number>>("/api/tags").then(
    (t) => (tags.value = Object.entries(t).slice(0, 40)),
  );
  await restart();
});
onUnmounted(() => {
  io?.disconnect();
  window.removeEventListener("scroll", spy);
});
useSocket((m) => {
  // summaries arrive every few seconds while old ones are redone: patch the card, never reload the list
  if (m.type === "self") selfCard.value?.load(); // 「哪个是你？」 popped or was answered
  else if (m.type === "summary") {
    const c = items.value.find((x) => x.id === m.conversation);
    if (c) c.summary = { ...m };
  } else if (
    m.type === "conversation" &&
    !q.value &&
    !tag.value &&
    !day.value &&
    window.scrollY < 200
  )
    restart();
}, restart);
</script>

<template>
  <AppHeader />
  <main>
    <div ref="find" class="find">
      <DayPicker :day="seen || day || today()" @pick="pick">
        <RouterLink class="cal" to="/calendar">日历</RouterLink>
      </DayPicker>
      <div class="q">
        <input v-model.trim="q" type="search" placeholder="搜索标题、摘要、待办、原文" />
        <button :disabled="!q || asking" @click="askAI">问 AI</button>
      </div>
      <div class="kinds">
        <button
          v-for="[k, label] in [
            ['对话', '对话'],
            ['口述', '口述'],
            ['', '全部'],
          ]"
          :key="k"
          :class="{ on: kind === k }"
          @click="kind = k"
        >
          {{ label }}
        </button>
      </div>
      <div class="tags">
        <button
          v-for="[t, n] in tags"
          :key="t"
          class="tag"
          :class="{ on: tag === t }"
          @click="tag = tag === t ? '' : t"
        >
          {{ t }} {{ n }}
        </button>
      </div>
    </div>
    <div v-if="asking || reply || askErr" class="card ask">
      <div class="meta">
        问 AI<template v-if="!asking && reply">
          · 查了 {{ reply.calls.length }} 次 · {{ reply.secs }} 秒</template
        >
        <button
          class="x btn-ghost"
          title="关掉"
          @click="
            reply = null;
            askErr = '';
          "
        >
          <Icon name="x" :size="18" />
        </button>
      </div>
      <div v-if="asking" class="muted">正在查……一般要 10~30 秒</div>
      <div v-else-if="askErr" class="bad" :title="askErrTitle">{{ askErr }}</div>
      <div v-else class="answer">
        <template v-for="(p, i) in answer" :key="i"
          ><RouterLink v-if="p.s" :to="'/c/' + p.s.id" :title="p.s.title ?? ''">{{
            p.s.start.slice(5, 16).replace("T", " ")
          }}</RouterLink
          ><template v-else>{{ p.t }}</template></template
        >
      </div>
    </div>
    <SelfVoice v-show="!q && !tag && !day" ref="selfCard" />
    <div v-if="due.length && !q && !tag && !day" class="card due">
      <div class="meta">到期的待办 · {{ due.length }} 条</div>
      <ul>
        <li v-for="t in due" :key="t.conv + t.text">
          <RouterLink :to="'/c/' + t.conv + (t.at ? '#' + t.at : '')">{{ t.text }}</RouterLink
          ><span class="muted">
            · {{ t.due_date === today() ? "今天" : "已过期 " + t.due_date.slice(5)
            }}{{ t.to ? " · 答应" + t.to : "" }}</span
          ><TodoMarks :conv="t.conv" :text="t.text" @marked="closed(t.conv, t.text)" />
        </li>
      </ul>
    </div>
    <details v-if="recap && !q && !tag && !day" class="card recap">
      <summary>
        <span class="meta"
          >{{ recap.date === shiftDay(today(), -1) ? "昨天" : recap.date.slice(5) }}的回顾 ·
          {{ recap.conversations }} 段对话<template v-if="recap.todos.length">
            · {{ recap.todos.length }} 条待办</template
          ></span
        >
        <div class="title">{{ recap.headline }}</div>
      </summary>
      <ul>
        <li v-for="(h, i) in recap.highlights" :key="i">
          <RouterLink v-if="h.conv" :to="'/c/' + h.conv">{{ h.text }}</RouterLink
          ><template v-else>{{ h.text }}</template>
        </li>
      </ul>
      <template v-if="recap.todos.length">
        <div class="sub">我要做的</div>
        <ul>
          <li v-for="t in recap.todos" :key="t.conv + t.text">
            <RouterLink :to="'/c/' + t.conv">{{ t.short ?? t.text }}</RouterLink
            ><span v-if="t.to || t.due" class="muted">
              · {{ [t.to && "答应" + t.to, t.due].filter(Boolean).join(" · ") }}</span
            ><TodoMarks
              v-if="t.short"
              :conv="t.conv"
              :text="t.text"
              @marked="closed(t.conv, t.text)"
            />
          </li>
        </ul>
      </template>
      <template v-if="recap.people.length">
        <div class="sub">聊过的人</div>
        <ul>
          <li v-for="p in recap.people" :key="p.who">{{ p.who }}：{{ p.note }}</li>
        </ul>
      </template>
    </details>
    <div v-if="open" class="card open">
      <div class="meta">{{ hhmm(open.start) }} 开始 · 进行中 · {{ open.sentences }} 句</div>
      <div class="title muted">对话进行中，结束后生成摘要</div>
    </div>
    <template v-for="(c, i) in items" :key="c.id">
      <div v-if="dayOf(i)" class="day" :data-day="dayOf(i)">{{ dayOf(i) }}</div>
      <RouterLink
        class="card"
        :class="{ media: (c.summary?.media_share ?? 0) >= MOSTLY_MEDIA }"
        :to="'/c/' + c.id"
      >
        <div class="meta">
          {{ (c.summary?.media_share ?? 0) >= MOSTLY_MEDIA ? "媒体 · " : ""
          }}{{ c.kind === "口述" ? "口述 · " : "" }}{{ hhmm(c.start) }}–{{ hhmm(c.end) }} ·
          {{ minutes(c) }} 分钟 · {{ (c.participants || []).map(speakerLabel).join("、") || "—" }}
          <template v-if="c.summary?.todos?.length">
            · {{ c.summary.todos.length }} 条待办</template
          >
        </div>
        <div
          class="title"
          :class="{
            bad: c.summary?.status === 'failed',
            muted: c.summary?.status !== 'done' && c.summary?.status !== 'failed',
          }"
        >
          {{
            c.summary?.status === "done"
              ? c.summary.title || "（无标题）"
              : c.summary?.status === "failed"
                ? "摘要生成失败"
                : "摘要生成中…"
          }}
        </div>
        <template v-if="(c.summary?.media_share ?? 0) < MOSTLY_MEDIA">
          <div v-if="c.summary?.summary" class="gist">{{ c.summary.summary }}</div>
          <div v-if="c.summary?.tags?.length" class="tagline">
            <span v-for="t in c.summary.tags" :key="t" class="tag">{{ t }}</span>
          </div>
        </template>
      </RouterLink>
    </template>
    <template v-if="done && !items.length && !open && !reply && !asking">
      <div v-if="q || tag || day || kind !== '对话'" class="empty">
        <Icon name="search" :size="48" />没有匹配的对话
      </div>
      <div v-else class="empty">
        <Icon name="message-circle" :size="48" />
        <div>还没有对话。装好 App、连上录音设备以后，聊过的话会在这里整理成一段段对话</div>
        <RouterLink class="btn btn-primary" to="/connect">连接手机</RouterLink>
      </div>
    </template>
    <button v-show="!done" ref="more" class="more" @click="page">
      {{ loading ? "加载中…" : "更早" }}
    </button>
  </main>
</template>

<style scoped>
/* sticky under the header (AppHeader: 45 px; on a phone, two rows, 79 px) */
.find {
  position: sticky;
  top: 45px;
  background: var(--bg);
  padding: 10px 0 6px;
  z-index: 1;
}
.cal {
  margin-left: auto;
  font-size: var(--fs-sm);
}
.q {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.q button {
  white-space: nowrap;
}
.find input {
  flex: 1;
  min-width: 0;
  padding: 7px 12px;
}
.find input:focus {
  border-color: var(--accent);
}
.kinds {
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  overflow: hidden;
  margin-top: 8px;
}
.kinds button {
  border: 0;
  border-radius: 0;
  padding: 0 14px;
  color: var(--fg-2);
}
.kinds button.on {
  background: var(--accent);
  color: var(--surface);
}
.tags {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  padding-top: 8px;
}
.day {
  font-size: var(--fs-sm);
  color: var(--muted);
  margin: 20px 0 8px;
}
.card.media {
  opacity: 0.5;
}
.ask .x {
  float: right;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 36px;
  padding: 0;
  margin: -8px -8px 0 0;
  color: var(--muted);
}
.answer {
  white-space: pre-wrap;
  margin-top: 4px;
}
.recap summary {
  cursor: pointer;
}
.recap ul,
.due ul {
  margin: 6px 0 0;
  padding-left: 20px;
  font-size: var(--fs-sm);
}
.recap .sub {
  font-size: var(--fs-xs);
  color: var(--muted);
  margin-top: 8px;
}
/* still going: an outline, not a card yet */
.card.open {
  background: none;
  box-shadow: none;
  border: 1px dashed var(--line);
}
.meta {
  font-size: var(--fs-xs);
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.title {
  font-weight: 600;
  margin: 2px 0;
}
.gist {
  font-size: var(--fs-sm);
  color: var(--fg-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tagline {
  margin-top: 6px;
}
.tagline .tag {
  cursor: inherit;
  margin-right: 4px;
  min-height: 22px;
  padding: 0 8px;
}
.more {
  display: block;
  margin: 16px auto;
}
@media (max-width: 600px) {
  .find {
    top: 79px;
  }
}
/* after the phone rule: inside the app there is no header to stay under */
:global(.in-app .find) {
  top: 0;
}
</style>
