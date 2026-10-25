<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useTemplateRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import AskVoice from "../components/AskVoice.vue";
import ConversationItem from "../components/ConversationItem.vue";
import DayPicker from "../components/DayPicker.vue";
import DueTodos from "../components/DueTodos.vue";
import Icon from "../components/Icon.vue";
import LiveCard from "../components/LiveCard.vue";
import RecapCard from "../components/RecapCard.vue";
import SelfVoice from "../components/SelfVoice.vue";
import Skeleton from "../components/Skeleton.vue";
import { t } from "../i18n";
import {
  errorTitle,
  get,
  isFragment,
  post,
  type Conversation,
  type Due,
  type Recap,
  type Status,
} from "../lib/api";
import { calendar, dayLabel, loadCalendar, shiftDay, today } from "../lib/calendar";
import { useHeaderFollow } from "../lib/headerFollow";
import { useSocket, useWatch } from "../lib/socket";
import { toast } from "../lib/toast";

// 时间线, the home page: every conversation newest first, by day, PAGE at a time as the end scrolls into view; words,
// one tag filter it; ?day= starts it at the end of that day (older ones keep coming as it scrolls).
// Pinned on top while it shows today unfiltered: the conversation going on (the live card) and what is due. Each past
// day opens with its recap, yesterday's unfolded. Without words or a tag, a day's fragments (too little speech for a
// summary) fold into one line under its recap, a tap shows them in their places. A brand-new instance gets the three
// steps to start instead.
const PAGE = 30;
const route = useRoute(),
  router = useRouter();
const q = ref(String(route.query.q ?? "")),
  tag = ref(String(route.query.tag ?? "")),
  day = ref(String(route.query.day ?? ""));
// the day of the topmost card on screen, shown by the picker
const seen = ref(""),
  find = useTemplateRef<HTMLElement>("find"),
  pull = useTemplateRef<HTMLElement>("pull"),
  mark = useTemplateRef<HTMLElement>("mark");
const tags = ref<[string, number][]>([]),
  items = ref<Conversation[]>([]),
  status = ref<Status | null>(null),
  due = ref<Due[]>([]),
  recaps = ref<Record<string, Recap | null>>({});
const ready = ref(false),
  loading = ref(false),
  done = ref(false),
  failed = ref<{ msg: string; title: string } | null>(null); // the list did not load: said, with 重试
const more = useTemplateRef<HTMLElement>("more"),
  selfCard = useTemplateRef<InstanceType<typeof SelfVoice>>("selfCard");
let seq = 0;

// today unfiltered: the pinned cards; no words or tag: the recaps
const top = computed(() => !q.value && !tag.value && !day.value);
const going = computed(() => !!status.value?.devices?.some((d) => d.recording && d.conversation));
const empty = computed(
  () =>
    ready.value &&
    !failed.value &&
    !items.value.length &&
    !Object.keys(calendar.value).length &&
    !status.value?.recorders?.length,
);
const yesterday = shiftDay(today(), -1);

// 问 AI: the words in the search box (or the sheet's) as a question (POST /api/ask, 10-30 s); [c…] in the answer link
// to the conversation. The answer is kept for the tab: back from a cited conversation it is still there
interface Reply {
  q?: string; // the question, as typed or heard
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
  question = ref(reply.value?.q ?? ""),
  asking = ref(false),
  askErr = ref(""),
  askErrTitle = ref("");
let asked = 0; // the newest question: an older one's answer (10-30 s) that lands after it is dropped
watch(reply, (r) => {
  try {
    sessionStorage.setItem("ask", JSON.stringify(r));
  } catch {}
});
async function askAI(text: string) {
  const my = ++asked;
  asking.value = true;
  askErr.value = "";
  question.value = text;
  const t0 = Date.now();
  try {
    const r = await post<Reply>("/api/ask", { q: text });
    if (my === asked) reply.value = { ...r, q: text, secs: Math.round((Date.now() - t0) / 1000) };
  } catch (e) {
    if (my === asked) {
      askErr.value = String((e as Error).message ?? e);
      askErrTitle.value = errorTitle(e);
    }
  }
  if (my === asked) asking.value = false;
}
const answer = computed(() => {
  const by = new Map((reply.value?.sources ?? []).map((s) => [s.id, s]));
  return (reply.value?.answer ?? "")
    .split(/(c\d{14}(?:-[A-Za-z0-9_]+)?)/)
    .map((x) => ({ t: x, s: by.get(x) ?? by.get(x.split("-")[0]) }));
});

// a phone (and inside the app): asking is a round button that opens a sheet (a modal <dialog>); the answer comes up
// on top of the page
const SUGGEST = computed(() => [
  t("timeline.suggestPromises"),
  t("timeline.suggestRecap"),
  t("timeline.suggestFollowUp"),
]);
const sq = ref("");
const sheet = ref<HTMLDialogElement>(),
  input = ref<HTMLInputElement>(),
  fab = ref<HTMLButtonElement>(),
  askCard = useTemplateRef<HTMLElement>("askCard");
// the question and its answer come up where the eye is: the card at the top of the screen (the pinned cards above it
// would hold it under the fold)
function ask(text: string) {
  if (!text) return;
  sheet.value?.close();
  void askAI(text);
  void nextTick(() => askCard.value?.scrollIntoView({ block: "start" }));
}
// the sheet starts as hold-to-talk (豆包's way) on a touch screen, as the text box with a mouse; the one last picked
// stays. No microphone (a page over plain http, an old WebView), or the online demo (src/demo.ts): the text box alone
const canTalk =
  import.meta.env.MODE !== "demo" &&
  typeof MediaRecorder !== "undefined" &&
  !!navigator.mediaDevices?.getUserMedia;
const mode = localStorage.askMode ?? (matchMedia("(pointer: coarse)").matches ? "voice" : "text");
const talk = ref(canTalk && mode === "voice"),
  hint = ref("");
// focus inside the tap itself: iOS raises the keyboard only for a focus() in the user's gesture
function openSheet() {
  hint.value = "";
  sheet.value?.showModal();
  if (!talk.value) input.value?.focus();
}
function setTalk(on: boolean) {
  talk.value = on;
  localStorage.askMode = on ? "voice" : "text";
  hint.value = "";
  if (!on) void nextTick(() => input.value?.focus()); // shown first; a microtask is still inside the tap for iOS
}
const heard = (text: string) => (text ? ask(text) : (hint.value = t("timeline.notHeard")));
function talkFailed(why: string, mic: boolean) {
  hint.value = why;
  if (mic) talk.value = false; // for now, not kept: the next time may have it
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
  let cs: Conversation[];
  try {
    cs = await search("");
  } catch (e) {
    if (my !== seq) return;
    // what is on screen stays; 重试 (or the socket's resync) loads it again
    loading.value = false;
    failed.value = { msg: (e as Error).message, title: errorTitle(e) };
    return;
  }
  if (my !== seq) return; // a newer search started meanwhile
  items.value = cs;
  done.value = cs.length < PAGE;
  loading.value = false;
  failed.value = null;
  spy();
}
async function page() {
  if (loading.value || done.value) return;
  const my = seq;
  loading.value = true;
  let cs: Conversation[];
  try {
    cs = await search(items.value.at(-1)?.start ?? "");
  } catch (e) {
    // said where it was asked (the card is pages above); 更早 again is the retry, the pages loaded stay
    if (my === seq) {
      loading.value = false;
      toast((e as Error).message);
    }
    return;
  }
  if (my !== seq) return;
  items.value.push(...cs);
  done.value = cs.length < PAGE;
  loading.value = false;
}

// the list by day, newest first
const days = computed(() => {
  const out: { day: string; convs: Conversation[]; frags: number }[] = [];
  for (const c of items.value) {
    const d = c.start.slice(0, 10);
    if (out.at(-1)?.day !== d) out.push({ day: d, convs: [], frags: 0 });
    out.at(-1)!.convs.push(c);
    out.at(-1)!.frags += +isFragment(c);
  }
  return out;
});
// the days whose fragments are out; words or a tag: every result as found
const unfolded = ref<Record<string, boolean>>({});
const fold = computed(() => !q.value && !tag.value);
const shown = (g: (typeof days.value)[number]) =>
  fold.value && !unfolded.value[g.day] ? g.convs.filter((c) => !isFragment(c)) : g.convs;
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

// The day picker follows the finger (useHeaderFollow). A wide screen: the pinned search bar goes up by --lift under
// the header, its picker out of sight, once it has been pinned that far. A phone (the bar scrolls away with the page):
// a second picker, fixed over the top, comes down as the page is pulled back anywhere below where the page's own one
// sits, and goes as soon as it would be no lower than that one, where both look the same: no two pickers, no jump.
// A second one, not the page's moved there, so the page under it keeps its height.
const pinned = () => getComputedStyle(find.value!).position === "sticky";
function pickerH() {
  if (!find.value) return 0;
  const row = find.value.firstElementChild as HTMLElement; // offsetTop: in the pinned bar
  return pinned() ? row.offsetTop + row.offsetHeight : (pull.value?.offsetHeight ?? 0);
}
// a pinned bar's own place is where it pins (45 px under the header): the mark, an empty line, is where it would be
const hideFrom = () =>
  mark.value
    ? mark.value.getBoundingClientRect().top + scrollY + (pinned() ? pickerH() - 45 : 0)
    : Infinity;
useHeaderFollow(pickerH, hideFrom, (h, past) => {
  for (const el of [find.value, pull.value]) el?.style.setProperty("--lift", h + "px");
  const p = pull.value; // hidden: while it would be no lower than the page's own, and all the way up
  if (p) p.style.visibility = h < past && h < p.offsetHeight ? "visible" : "hidden";
});

function spy() {
  // the last day header above the bottom of the search bar (the top of the screen once it scrolled away, or the bottom
  // of the picker come down over it) is the day being read
  const edge =
    Math.max(
      0,
      find.value?.getBoundingClientRect().bottom ?? 0,
      pull.value?.getBoundingClientRect().bottom ?? 0,
    ) + 8;
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
// the page's loads (重试 again); only the list's failure is said: the socket pushes the status every 2 s, and the
// calendar only marks the picker's days
async function init() {
  get<Due[]>("/api/todos/due").then(
    (d) => (due.value = d),
    () => {},
  );
  get<Record<string, number>>("/api/tags").then(
    (ts) => (tags.value = Object.entries(ts).slice(0, 40)),
    () => {},
  );
  const [st] = await Promise.all([
    get<Status>("/api/status").catch(() => null),
    loadCalendar(true).catch(() => {}),
    restart(),
  ]);
  if (st) status.value = st;
  ready.value = true;
}
onMounted(() => {
  window.addEventListener("scroll", spy, { passive: true });
  void init();
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
      <h2>{{ t("timeline.start") }}</h2>
      <ol>
        <li>
          <b>{{ t("timeline.install") }}</b
          ><span class="muted">{{ t("timeline.installSub") }}</span>
        </li>
        <li>
          <b>{{ t("timeline.scan") }}</b
          ><span class="muted">{{ t("timeline.scanSub") }}</span>
        </li>
        <li>
          <b>{{ t("timeline.wear") }}</b
          ><span class="muted">{{ t("timeline.wearSub") }}</span>
        </li>
      </ol>
      <RouterLink class="btn btn-primary" to="/connect">{{ t("timeline.goConnect") }}</RouterLink>
    </div>
    <template v-else>
      <template v-if="top">
        <LiveCard v-if="going" />
        <DueTodos :due="due" />
      </template>
      <div ref="mark" />
      <div ref="find" class="find">
        <DayPicker :day="seen || day || today()" @pick="pick">
          <RouterLink class="cal" to="/calendar">{{ t("title.calendar") }}</RouterLink>
        </DayPicker>
        <div class="q">
          <input v-model.trim="q" type="search" :placeholder="t('timeline.search')" />
          <AskVoice
            v-if="canTalk"
            toggle
            @heard="(s) => (s ? askAI(s) : ((question = ''), (askErr = t('timeline.notHeard'))))"
            @fail="(why) => ((question = ''), (askErr = why))"
          />
          <button :disabled="!q || asking" @click="askAI(q)">{{ t("timeline.ask") }}</button>
        </div>
        <div class="tags">
          <button
            v-for="[tg, n] in tags"
            :key="tg"
            class="tag"
            :class="{ on: tag === tg }"
            @click="tag = tag === tg ? '' : tg"
          >
            {{ tg }} <span class="n">{{ n }}</span>
          </button>
        </div>
      </div>
      <!-- a phone: the picker again, over the top, as the page is pulled back (above) -->
      <div ref="pull" class="pull">
        <DayPicker :day="seen || day || today()" @pick="pick">
          <RouterLink class="cal" to="/calendar">{{ t("title.calendar") }}</RouterLink>
        </DayPicker>
      </div>
      <div v-if="asking || reply || askErr" ref="askCard" class="card ask">
        <div class="meta">
          {{ t("timeline.ask")
          }}{{
            !asking && reply
              ? " · " +
                t("timeline.askStats", { n: reply.calls.length, s: reply.secs }, reply.calls.length)
              : ""
          }}
          <button
            class="x btn-ghost"
            :title="t('timeline.close')"
            @click="
              reply = null;
              askErr = '';
            "
          >
            <Icon name="x" :size="18" />
          </button>
        </div>
        <p v-if="question" class="said">{{ question }}</p>
        <div v-if="asking" class="muted">{{ t("timeline.asking") }}</div>
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
      <div v-if="failed" class="empty" :title="failed.title">
        <Icon name="alert-circle" :size="48" />{{ failed.msg }}
        <button class="btn" @click="init">{{ t("common.retry") }}</button>
      </div>
      <section v-for="g in days" :key="g.day">
        <h2 class="day" :data-day="g.day">
          {{ dayLabel(g.day) }} <span class="n">{{ t("calendar.convs", g.convs.length) }}</span>
        </h2>
        <RecapCard
          v-if="!q && !tag && recaps[g.day]"
          :recap="recaps[g.day]!"
          :open="g.day === yesterday"
        />
        <button
          v-if="fold && g.frags"
          class="frags"
          :aria-expanded="!!unfolded[g.day]"
          @click="unfolded[g.day] = !unfolded[g.day]"
        >
          <Icon :name="unfolded[g.day] ? 'chevron-down' : 'chevron-right'" :size="16" />
          {{
            t(unfolded[g.day] ? "timeline.fragsHide" : "timeline.frags", { n: g.frags }, g.frags)
          }}
        </button>
        <ConversationItem v-for="c in shown(g)" :key="c.id" :c="c" />
      </section>
      <template v-if="done && !items.length && !reply && !asking">
        <div v-if="q || tag || day" class="empty">
          <Icon name="search" :size="48" />{{ t("timeline.noMatch") }}
        </div>
        <div v-else-if="!going" class="empty">
          <Icon name="message-circle" :size="48" />
          <div>{{ t("timeline.none") }}</div>
        </div>
      </template>
      <!-- not while the first load failed: 重试 above is the way then -->
      <button
        v-show="!done && !(failed && !items.length)"
        ref="more"
        class="more"
        :disabled="loading"
        @click="page"
      >
        {{ loading ? t("timeline.loading") : t("timeline.earlier") }}
      </button>
      <!-- 问 AI on a phone: a sheet from the bottom; a tap on the dim backdrop (the dialog itself) or Escape closes it -->
      <dialog
        ref="sheet"
        class="sheet"
        :aria-label="t('timeline.ask')"
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
            <AskVoice v-if="talk" @heard="heard" @fail="talkFailed" @pointerdown="hint = ''" />
            <input
              v-show="!talk"
              ref="input"
              v-model.trim="sq"
              type="search"
              enterkeyhint="send"
              :placeholder="t('timeline.askToday')"
            />
            <button
              v-if="canTalk"
              type="button"
              class="btn-ghost switch"
              :aria-label="talk ? t('timeline.typeInstead') : t('timeline.talkInstead')"
              @click="setTalk(!talk)"
            >
              <Icon :name="talk ? 'keyboard' : 'mic'" :size="22" />
            </button>
            <button
              v-show="!talk"
              class="btn-primary"
              :disabled="!sq"
              :aria-label="t('timeline.ask')"
            >
              <Icon name="sparkles" :size="18" />
            </button>
          </div>
          <p v-if="hint" class="hint">{{ hint }}</p>
        </form>
      </dialog>
      <button ref="fab" class="fab btn-primary" :aria-label="t('timeline.ask')" @click="openSheet">
        <Icon name="sparkles" :size="24" />
      </button>
    </template>
  </main>
</template>

<style scoped>
/* sticky under the header on a wide screen (AppHeader: 45 px), a hairline under it so cards pass under an edge; --lift:
   up under the header, the day picker out of sight */
.find {
  position: sticky;
  top: 45px;
  background: var(--bg);
  padding: var(--space-3) 0;
  box-shadow: 0 1px 0 var(--line);
  margin-bottom: var(--space-3);
  z-index: 1;
  transform: translateY(calc(-1 * var(--lift, 0px)));
  will-change: transform;
}
/* the picker over the top on a phone: its picker exactly where the page's is in .find (12 px down, the search box 8 px
   under it), so the two swap unseen; up by --lift, out of sight */
.pull {
  display: none;
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 2;
  padding: var(--space-3) max(var(--space-4), 50% - 430px + var(--space-4)) var(--space-2);
  background: var(--bg);
  border-bottom: 1px solid var(--line);
  visibility: hidden;
  transform: translateY(calc(-1 * var(--lift, 0px)));
  will-change: transform;
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
/* the filter: white chips edged like the search box and the picker's buttons, accent once picked, so the row does
   not outshout the cards under it */
.tags .tag:not(.on) {
  background: var(--surface);
  color: var(--fg-2);
  box-shadow: inset 0 0 0 1px var(--line);
}
.tags .n {
  margin-left: 2px;
  color: var(--muted);
}
.tags .on .n {
  color: inherit;
}
/* a day: 10月24日 周六  3 段对话 — the strongest line of its section */
.day {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--fg);
  margin: var(--space-6) 0 var(--space-3);
}
.day .n {
  margin-left: var(--space-1);
  font-size: var(--fs-sm);
  font-weight: 400;
  color: var(--muted);
}
/* 另有 3 段零碎对话: a quiet line, not a card */
.frags {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-height: 44px;
  margin-top: calc(-1 * var(--space-3));
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
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
/* the question, as a bubble on the right */
.said {
  width: fit-content;
  max-width: 85%;
  margin: var(--space-2) 0 var(--space-2) auto;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius);
  background: var(--accent-soft);
  white-space: pre-wrap;
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
.row .btn-primary,
.row .switch {
  flex: none;
  width: 44px;
  padding: 0;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.row .switch {
  align-self: center;
  color: var(--fg-2);
}
.sheet form:has(.rec) .chips {
  visibility: hidden; /* under 松手发送，上移取消 */
}
.sheet .hint {
  margin: var(--space-2) 0 0;
  font-size: var(--fs-sm);
  color: var(--muted);
  text-align: center;
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
@media (prefers-reduced-motion: no-preference) {
  .sheet[open] {
    animation: up 0.2s ease-out;
  }
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
    box-shadow: none;
    margin-bottom: 0;
    transform: none;
    will-change: auto;
  }
  .pull {
    display: block;
    padding-inline: var(--space-3);
  }
  .q button {
    display: none;
  }
  .fab {
    display: inline-flex;
  }
  main {
    padding-top: var(
      --space-3
    ); /* no header above on a phone: the first card off the screen's edge */
    padding-bottom: 88px; /* the last conversation scrolls clear of the button */
  }
}
:global(.in-app .find) {
  position: static;
  box-shadow: none;
  margin-bottom: 0;
  transform: none;
  will-change: auto;
}
html.in-app .pull {
  display: block;
}
html.in-app .q button {
  display: none;
}
html.in-app .fab {
  display: inline-flex;
}
html.in-app main {
  padding-top: var(--space-3);
  padding-bottom: 88px;
}
</style>
