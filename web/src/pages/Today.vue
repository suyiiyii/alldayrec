<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import ConversationItem from "../components/ConversationItem.vue";
import DueTodos from "../components/DueTodos.vue";
import Icon from "../components/Icon.vue";
import LivePanel from "../components/LivePanel.vue";
import RecapCard from "../components/RecapCard.vue";
import Skeleton from "../components/Skeleton.vue";
import {
  get,
  type Conversation,
  type Due,
  type LiveState,
  type Recap,
  type Status,
} from "../lib/api";
import { calendar, dayLabel, loadCalendar, today } from "../lib/calendar";
import { useSocket, useWatch } from "../lib/socket";

// The home page: what is due, yesterday's recap, the conversation going on, today's conversations, a question box.
// A brand-new instance (no day with sentences, no device) gets the three steps to start instead.
const router = useRouter();
const day = today();
const status = ref<Status | null>(null),
  lives = ref<LiveState[]>([]),
  convs = ref<Conversation[]>([]),
  due = ref<Due[]>([]),
  recap = ref<Recap | null>(null),
  ready = ref(false);

function addConv(c: Conversation) {
  if (c.state === "open" || c.start.slice(0, 10) !== day) return; // the open one is the live card's
  const i = convs.value.findIndex((x) => x.id === c.id);
  if (i >= 0) convs.value[i] = c;
  else convs.value.push(c);
}
async function load() {
  const [st, ls, cs, du, rc] = await Promise.all([
    get<Status>("/api/status"),
    get<LiveState[]>("/api/live"),
    get<Conversation[]>("/api/conversations", { date: day }),
    get<Due[]>("/api/todos/due"),
    get<Recap | null>("/api/recap"),
    loadCalendar(true),
  ]);
  status.value = st;
  lives.value = ls;
  convs.value = [];
  cs.forEach(addConv);
  due.value = du;
  recap.value = rc;
  ready.value = true;
}
onMounted(load);
// a todo marked 完成 or 不对 leaves both cards at once (the server leaves it out from now on)
function closed(conv: string, text: string) {
  due.value = due.value.filter((t) => t.conv !== conv || t.text !== text);
  if (recap.value)
    recap.value.todos = recap.value.todos.filter((t) => t.conv !== conv || t.text !== text);
}

const { connected, send } = useSocket((m) => {
  if (m.type === "status") status.value = m;
  else if (m.type === "live")
    lives.value = lives.value.filter((l) => l.device !== m.device).concat(m.live ? [m.live] : []);
  else if (m.type === "conversation") addConv(m);
  else if (m.type === "summary") {
    const c = convs.value.find((x) => x.id === m.conversation);
    if (c) c.summary = { ...m };
  }
}, load);
useWatch(send, connected); // the live card's analysis runs while this page is in front, as on the live page

const empty = computed(
  () => ready.value && !Object.keys(calendar.value).length && !status.value?.devices?.length,
);
const going = computed(() => !!status.value?.devices?.some((d) => d.recording && d.conversation));
const newest = computed(() => [...convs.value].sort((a, b) => (a.start < b.start ? 1 : -1)));
const hour = new Date().getHours();
const hello =
  hour < 5
    ? "夜深了"
    : hour < 11
      ? "早上好"
      : hour < 13
        ? "中午好"
        : hour < 18
          ? "下午好"
          : "晚上好";

// 问问今天: the question goes to the conversations page, which asks the AI with it
const SUGGEST = ["今天答应了谁什么", "昨天的回顾", "帮我写跟进消息"];
const q = ref("");
const ask = (text: string) => text && router.push({ path: "/conversations", query: { ask: text } });
</script>

<template>
  <AppHeader />
  <main>
    <div class="hello">
      <h1>{{ dayLabel(day) }}</h1>
      <div class="muted">
        {{ hello }}<template v-if="convs.length"> · 今天 {{ convs.length }} 段对话</template>
      </div>
    </div>
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
      <DueTodos :due="due" @closed="closed" />
      <RecapCard v-if="recap" :recap="recap" open @closed="closed" />
      <section v-if="going" class="card live">
        <RouterLink class="live-head" to="/live">
          <span class="rec" />进行中的对话<span class="more"
            >实时字幕<Icon name="chevron-right" :size="16"
          /></span>
        </RouterLink>
        <LivePanel :status="status" :lives="lives" :convs="convs" @jump="router.push('/live')" />
      </section>
      <h2 class="sec">今天的对话</h2>
      <ConversationItem v-for="c in newest" :key="c.id" :c="c" />
      <p v-if="!convs.length" class="muted none">今天还没有聊完的对话</p>
      <form class="ask" @submit.prevent="ask(q)">
        <div class="chips">
          <button v-for="s in SUGGEST" :key="s" type="button" class="chip" @click="ask(s)">
            {{ s }}
          </button>
        </div>
        <div class="row">
          <input v-model.trim="q" type="search" enterkeyhint="send" placeholder="问问今天…" />
          <button class="btn-primary" :disabled="!q" aria-label="问 AI">
            <Icon name="sparkles" :size="18" />
          </button>
        </div>
      </form>
    </template>
  </main>
</template>

<style scoped>
.hello {
  padding: var(--space-3) 0 var(--space-4);
}
h1 {
  margin: 0;
}
.sec {
  font-size: var(--fs-sm);
  font-weight: 400;
  color: var(--muted);
  margin: var(--space-5) 0 var(--space-2);
}
.none {
  font-size: var(--fs-sm);
}
/* the conversation going on: its head opens the live captions */
.live-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 600;
  color: var(--fg);
}
.rec {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--err);
}
.more {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  font-size: var(--fs-sm);
  font-weight: 400;
  color: var(--accent);
}
/* the three steps, numbered in circles */
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
/* the question box stays at the bottom, above the tabs */
.ask {
  position: sticky;
  bottom: var(--tabbar);
  background: var(--bg);
  padding: var(--space-3) 0;
  margin-top: var(--space-4);
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
</style>
