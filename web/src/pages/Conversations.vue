<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useTemplateRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import ConversationItem from "../components/ConversationItem.vue";
import DayPicker from "../components/DayPicker.vue";
import DueTodos from "../components/DueTodos.vue";
import Icon from "../components/Icon.vue";
import RecapCard from "../components/RecapCard.vue";
import SelfVoice from "../components/SelfVoice.vue";
import {
  errorTitle,
  get,
  hhmm,
  post,
  type Conversation,
  type Due,
  type Recap,
  type Status,
} from "../lib/api";
import { shiftDay, today } from "../lib/calendar";
import { useSocket } from "../lib/socket";

// newest first, PAGE at a time as the end scrolls into view; words, one tag and the kind filter it; ?day= starts
// the list at the end of that day (older ones keep coming as it scrolls); ?ask= (the today page's question box) asks
// the AI at once
const PAGE = 30;
const route = useRoute(),
  router = useRouter();
const ask = route.query.ask ? String(route.query.ask) : "";
const q = ref(ask || String(route.query.q ?? "")),
  tag = ref(String(route.query.tag ?? "")),
  kind = ref(String(route.query.kind ?? "对话")),
  day = ref(String(route.query.day ?? ""));
// the day of the topmost card on screen, shown by the picker
const folded = ref(false);
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

// the newest daily recap, folded to its headline; my todos due today or before
const recap = ref<Recap | null>(null);
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

let lastY = 0;
function spy() {
  // scrolling down folds the search bar away (not while typing in it), scrolling up brings it back
  const y = window.scrollY;
  folded.value = y > lastY && y > 80 && !find.value?.contains(document.activeElement);
  lastY = y;
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
  if (ask) askAI(); // restart() drops ?ask= from the address: a reload does not ask again
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
    <div ref="find" class="find" :class="{ folded }">
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
    <template v-if="!q && !tag && !day">
      <DueTodos :due="due" @closed="closed" />
      <RecapCard v-if="recap" :recap="recap" @closed="closed" />
    </template>
    <div v-if="open" class="card open">
      <div class="meta">{{ hhmm(open.start) }} 开始 · 进行中 · {{ open.sentences }} 句</div>
      <div class="title muted">对话进行中，结束后生成摘要</div>
    </div>
    <template v-for="(c, i) in items" :key="c.id">
      <div v-if="dayOf(i)" class="day" :data-day="dayOf(i)">{{ dayOf(i) }}</div>
      <ConversationItem :c="c" />
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
/* sticky under the header (AppHeader: 45 px; none on a phone) */
.find {
  position: sticky;
  top: 45px;
  background: var(--bg);
  padding: 10px 0 6px;
  z-index: 1;
  transition:
    transform 0.2s,
    opacity 0.2s;
}
.find.folded {
  transform: translateY(-110%);
  opacity: 0;
  pointer-events: none;
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
.more {
  display: block;
  margin: 16px auto;
}
/* a phone and inside the app: no header to stay under */
@media (max-width: 600px) {
  .find {
    top: 0;
  }
}
:global(.in-app .find) {
  top: 0;
}
</style>
