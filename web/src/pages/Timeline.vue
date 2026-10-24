<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useTemplateRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import ConversationItem from "../components/ConversationItem.vue";
import DayPicker from "../components/DayPicker.vue";
import DueTodos from "../components/DueTodos.vue";
import Icon from "../components/Icon.vue";
import LiveCard from "../components/LiveCard.vue";
import RecapCard from "../components/RecapCard.vue";
import SelfVoice from "../components/SelfVoice.vue";
import Skeleton from "../components/Skeleton.vue";
import {
  errorTitle,
  get,
  post,
  type Conversation,
  type Due,
  type Recap,
  type Status,
} from "../lib/api";
import { calendar, dayLabel, loadCalendar, shiftDay, today } from "../lib/calendar";
import { useSocket, useWatch } from "../lib/socket";

// 时间线, the home page: every conversation newest first, by day, PAGE at a time as the end scrolls into view; words,
// one tag filter it; ?day= starts it at the end of that day (older ones keep coming as it scrolls).
// Pinned on top while it shows today unfiltered: the conversation going on (the live card) and what is due. Each past
// day opens with its recap, yesterday's unfolded. A brand-new instance gets the three steps to start instead.
const PAGE = 30;
const route = useRoute(),
  router = useRouter();
const q = ref(String(route.query.q ?? "")),
  tag = ref(String(route.query.tag ?? "")),
  day = ref(String(route.query.day ?? ""));
// the day of the topmost card on screen, shown by the picker
const seen = ref(""),
  find = useTemplateRef<HTMLElement>("find");
const tags = ref<[string, number][]>([]),
  items = ref<Conversation[]>([]),
  status = ref<Status | null>(null),
  due = ref<Due[]>([]),
  recaps = ref<Record<string, Recap | null>>({});
const ready = ref(false),
  loading = ref(false),
  done = ref(false);
const more = useTemplateRef<HTMLElement>("more"),
  selfCard = useTemplateRef<InstanceType<typeof SelfVoice>>("selfCard");
let seq = 0;

// today unfiltered: the pinned cards; no words or tag: the recaps
const top = computed(() => !q.value && !tag.value && !day.value);
const going = computed(() => !!status.value?.devices?.some((d) => d.recording && d.conversation));
const empty = computed(
  () => ready.value && !Object.keys(calendar.value).length && !status.value?.devices?.length,
);
const yesterday = shiftDay(today(), -1);

// 问 AI: the words in the search box (or the sheet's) as a question (POST /api/ask, 10-30 s); [c…] in the answer link
// to the conversation. The answer is kept for the tab: back from a cited conversation it is still there
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
async function askAI(text: string) {
  asking.value = true;
  askErr.value = "";
  const t0 = Date.now();
  try {
    const r = await post<Reply>("/api/ask", { q: text });
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

// a phone (and inside the app): asking is a round button that opens a sheet (a modal <dialog>); the answer comes up
// on top of the page
const SUGGEST = ["今天答应了谁什么", "昨天的回顾", "帮我写跟进消息"];
const sq = ref("");
const sheet = ref<HTMLDialogElement>(),
  input = ref<HTMLInputElement>(),
  fab = ref<HTMLButtonElement>();
function ask(text: string) {
  if (!text) return;
  sheet.value?.close();
  window.scrollTo(0, 0);
  void askAI(text);
}
// focus inside the tap itself: iOS raises the keyboard only for a focus() in the user's gesture
function openSheet() {
  sheet.value?.showModal();
  input.value?.focus();
}

const search = (before: string) =>
  get<Conversation[]>("/api/search", {
    q: q.value,
    tag: tag.value,
    before: before || (day.value ? shiftDay(day.value, 1) + "T00:00:00" : ""),
    limit: String(PAGE),
  });
async function restart() {
  // the old list stays on screen until the new first page is in: no blank flash
  router.replace({
    query: {
      ...(q.value && { q: q.value }),
      ...(tag.value && { tag: tag.value }),
      ...(day.value && { day: day.value }),
    },
  });
  const my = ++seq;
  loading.value = true;
  if (top.value) selfCard.value?.load(); // after a reconnect too: it may have popped meanwhile
  const cs = await search("");
  if (my !== seq) return; // a newer search started meanwhile
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

// the list by day, newest first
const days = computed(() => {
  const out: { day: string; convs: Conversation[] }[] = [];
  for (const c of items.value) {
    const d = c.start.slice(0, 10);
    if (out.at(-1)?.day !== d) out.push({ day: d, convs: [] });
    out.at(-1)!.convs.push(c);
  }
  return out;
});
// each past day's recap, asked once as the day comes into the list (null: none, or still on its way)
watch(days, (ds) => {
  if (q.value || tag.value) return;
  for (const { day: d } of ds)
    if (d < today() && !(d in recaps.value)) {
      recaps.value[d] = null;
      get<Recap | null>("/api/recap", { date: d })
        .then((r) => (recaps.value[d] = r))
        .catch(() => {});
    }
});

let timer = 0;
watch(q, () => {
  clearTimeout(timer);
  timer = window.setTimeout(restart, 300);
});
watch([tag, day], restart);
watch(day, () => window.scrollTo(0, 0));
const pick = (d: string) => (day.value = d === today() ? "" : d);

function spy() {
  // the last day header above the bottom of the search bar (the top of the screen once it scrolled away) is the
  // day being read
  const edge = Math.max(0, find.value?.getBoundingClientRect().bottom ?? 0) + 8;
  let d = items.value[0]?.start.slice(0, 10) ?? day.value;
  for (const el of document.querySelectorAll<HTMLElement>("[data-day]"))
    if (el.getBoundingClientRect().top < edge) d = el.dataset.day!;
  seen.value = d;
}

// 更早: the next page as the button scrolls into view (it is there once the page has loaded)
const io = new IntersectionObserver((es) => es[0].isIntersecting && page());
watch(more, (el, old) => {
  if (old) io.unobserve(old);
  if (el) io.observe(el);
});
onMounted(async () => {
  window.addEventListener("scroll", spy, { passive: true });
  get<Due[]>("/api/todos/due").then((d) => (due.value = d));
  get<Record<string, number>>("/api/tags").then(
    (t) => (tags.value = Object.entries(t).slice(0, 40)),
  );
  const [st] = await Promise.all([get<Status>("/api/status"), loadCalendar(true), restart()]);
  status.value = st;
  ready.value = true;
});
onUnmounted(() => {
  io.disconnect();
  window.removeEventListener("scroll", spy);
});
const { connected, send } = useSocket((m) => {
  // summaries arrive every few seconds while old ones are redone: patch the card, never reload the list
  if (m.type === "status") status.value = m;
  else if (m.type === "self") selfCard.value?.load(); // 「哪个是你？」 popped or was answered
  else if (m.type === "summary") {
    const c = items.value.find((x) => x.id === m.conversation);
    if (c) c.summary = { ...m };
  } else if (m.type === "conversation" && top.value && window.scrollY < 200) restart();
}, restart);
useWatch(send, connected); // the live card's analysis runs while this page is in front, as on the live page
</script>

<template>
  <AppHeader />
  <main>
    <Skeleton v-if="!ready" />
    <div v-else-if="empty" class="card start">
      <h2>三步开始</h2>
      <ol>
        <li><b>装 App</b><span class="muted">在手机上装好 alldayrec App</span></li>
        <li>
          <b>扫码</b><span class="muted">在 App 的「设置 › 扫码登录」里扫连接页上的二维码</span>
        </li>
        <li>
          <b>戴上设备</b
          ><span class="muted">打开录音设备戴在身上，说的话会实时出现，聊完整理成对话</span>
        </li>
      </ol>
      <RouterLink class="btn btn-primary" to="/connect">去连接手机</RouterLink>
    </div>
    <template v-else>
      <template v-if="top">
        <LiveCard v-if="going" />
        <DueTodos :due="due" />
      </template>
      <div ref="find" class="find">
        <DayPicker :day="seen || day || today()" @pick="pick">
          <RouterLink class="cal" to="/calendar">日历</RouterLink>
        </DayPicker>
        <div class="q">
          <input v-model.trim="q" type="search" placeholder="搜索标题、摘要、待办、原文" />
          <button :disabled="!q || asking" @click="askAI(q)">问 AI</button>
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
      <SelfVoice v-if="top" ref="selfCard" />
      <section v-for="g in days" :key="g.day">
        <h2 class="day" :data-day="g.day">
          {{ dayLabel(g.day) }} · {{ g.convs.length }} 段对话
        </h2>
        <RecapCard
          v-if="!q && !tag && recaps[g.day]"
          :recap="recaps[g.day]!"
          :open="g.day === yesterday"
        />
        <ConversationItem v-for="c in g.convs" :key="c.id" :c="c" />
      </section>
      <template v-if="done && !items.length && !reply && !asking">
        <div v-if="q || tag || day" class="empty">
          <Icon name="search" :size="48" />没有匹配的对话
        </div>
        <div v-else-if="!going" class="empty">
          <Icon name="message-circle" :size="48" />
          <div>还没有聊完的对话。戴上录音设备聊过的话，会在这里整理成一段段对话</div>
        </div>
      </template>
      <button v-show="!done" ref="more" class="more" @click="page">
        {{ loading ? "加载中…" : "更早" }}
      </button>
      <!-- 问 AI on a phone: a sheet from the bottom; a tap on the dim backdrop (the dialog itself) or Escape closes it -->
      <dialog
        ref="sheet"
        class="sheet"
        aria-label="问 AI"
        @click.self="sheet?.close()"
        @close="fab?.focus()"
      >
        <form @submit.prevent="ask(sq)">
          <div class="chips">
            <button v-for="s in SUGGEST" :key="s" type="button" class="chip" @click="ask(s)">
              {{ s }}
            </button>
          </div>
          <div class="row">
            <input
              ref="input"
              v-model.trim="sq"
              type="search"
              enterkeyhint="send"
              placeholder="问问今天…"
            />
            <button class="btn-primary" :disabled="!sq" aria-label="问 AI">
              <Icon name="sparkles" :size="18" />
            </button>
          </div>
        </form>
      </dialog>
      <button ref="fab" class="fab btn-primary" aria-label="问 AI" @click="openSheet">
        <Icon name="sparkles" :size="24" />
      </button>
    </template>
  </main>
</template>

<style scoped>
/* sticky under the header on a wide screen (AppHeader: 45 px) */
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
.tags {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  padding-top: 8px;
}
/* a day: 10月24日 周六 · 3 段对话 */
.day {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--fg-2);
  margin: var(--space-5) 0 var(--space-2);
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
.meta {
  font-size: var(--fs-xs);
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.more {
  display: block;
  margin: 16px auto;
}
/* the three steps, numbered in circles */
.start {
  margin-top: var(--space-3);
}
.start h2 {
  margin: 0 0 var(--space-3);
}
.start ol {
  list-style: none;
  counter-reset: step;
  padding: 0;
  margin: 0 0 var(--space-4);
  display: grid;
  gap: var(--space-3);
}
.start li {
  counter-increment: step;
  display: grid;
  grid-template-columns: 28px 1fr;
  column-gap: var(--space-3);
}
.start li::before {
  content: counter(step);
  grid-row: span 2;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.start li span {
  font-size: var(--fs-sm);
}
.start .btn {
  width: 100%;
}
/* the ask button: over the bottom right on a phone and inside the app, where it takes the place of the search bar's
   问 AI (a wide screen asks from the search bar) */
.fab {
  display: none;
  position: fixed;
  right: var(--space-4);
  bottom: calc(var(--space-4) + var(--tabbar));
  z-index: 3;
  width: 56px;
  height: 56px;
  padding: 0;
  border-radius: 50%;
  box-shadow: var(--shadow);
  align-items: center;
  justify-content: center;
}
.sheet[open] + .fab {
  display: none;
}
.chips {
  display: flex;
  gap: var(--space-2);
  overflow-x: auto;
  scrollbar-width: none;
  margin-bottom: var(--space-2);
}
.row {
  display: flex;
  gap: var(--space-2);
}
.row input {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-pill);
}
.row input:focus {
  outline: none;
  border-color: var(--accent);
}
.row .btn-primary {
  flex: none;
  width: 44px;
  padding: 0;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
/* the sheet: from the bottom edge, full width; the form fills it, so a tap on the dialog itself is the backdrop */
.sheet {
  width: 100%;
  max-width: none;
  margin: auto 0 0;
  padding: 0;
  border: 0;
  border-radius: 16px 16px 0 0;
  background: var(--surface);
  color: var(--fg);
}
.sheet[open] {
  animation: up 0.2s ease-out;
}
@keyframes up {
  from {
    transform: translateY(100%);
  }
}
.sheet::backdrop {
  background: rgb(0 0 0 / 0.4);
}
.sheet form {
  padding: var(--space-4) var(--space-4) max(var(--space-4), env(safe-area-inset-bottom));
}
/* a phone and inside the app: the bar scrolls away with the page (the screen is for reading), the ask button shows */
@media (max-width: 600px) {
  .find {
    position: static;
  }
  .q button {
    display: none;
  }
  .fab {
    display: inline-flex;
  }
  main {
    padding-bottom: 88px; /* the last conversation scrolls clear of the button */
  }
}
:global(.in-app .find) {
  position: static;
}
html.in-app .q button {
  display: none;
}
html.in-app .fab {
  display: inline-flex;
}
html.in-app main {
  padding-bottom: 88px;
}
</style>
