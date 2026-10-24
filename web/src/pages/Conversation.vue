<script setup lang="ts">
// One conversation: its title, time and people, then two tabs. 摘要 (the default): the summary, todos, decisions,
// each person's points, topics; every time on them plays the transcript from that sentence. 原文: who spoke how
// much, the sentences, a player at the bottom that keeps the sentence playing lit and in view. The tab is in the
// query (?tab=transcript) so back and forward step through it; ?at=<time> or #<time> from another page opens 原文 there.
// Renaming, the speaker suggestions, a new summary and a correction are in the ⋯ menu
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import Avatar from "../components/Avatar.vue";
import Icon from "../components/Icon.vue";
import MediaFold from "../components/MediaFold.vue";
import PlayerBar from "../components/PlayerBar.vue";
import Timeline from "../components/Timeline.vue";
import TodoMarks from "../components/TodoMarks.vue";
import TranscriptRow from "../components/TranscriptRow.vue";
import {
  fold,
  get,
  inSpans,
  hhmm,
  hhmmss,
  minutes,
  post,
  type Conversation,
  type Feedback,
  type LabelSuggestions,
  type LiveState,
  type Row,
} from "../lib/api";
import { play, player, track, trackClose, trackOpen, trackSeek } from "../lib/audio";
import { dayLabel } from "../lib/calendar";
import { useSocket } from "../lib/socket";
import { useMutation } from "../lib/mutation";
import { likeness, speakerColor, speakerLabel } from "../lib/speakers";
import { toast } from "../lib/toast";
import { markOf } from "../lib/todos";
import { rowAt, talkShare } from "../lib/track";

const props = defineProps<{ id: string }>();
const route = useRoute(),
  router = useRouter();
const c = ref<
    (Conversation & { rows: Row[]; timeline?: LiveState | null; feedback: Feedback[] }) | null
  >(null),
  missing = ref(false);
const s = computed(() => c.value?.summary ?? null);
const media = (r: Row) => inSpans(r, s.value?.media_spans);
const segs = computed(() => fold(c.value?.rows ?? [], media));

async function load() {
  try {
    const x = await get<NonNullable<typeof c.value>>(`/api/conversation/${encodeURIComponent(props.id)}`);
    c.value = x;
    trackOpen(Date.parse(x.start), Date.parse(x.end), x.device ?? x.rows.find((r) => r.source)?.source ?? "");
  } catch {
    missing.value = true;
  }
}

// the tabs
type Tab = "summary" | "transcript";
const tab = computed<Tab>(() =>
  route.query.tab === "transcript" || (!route.query.tab && route.hash) ? "transcript" : "summary",
);
const setTab = (t: Tab) =>
  t === tab.value ? Promise.resolve() : router.push({ query: t === "transcript" ? { tab: t } : {} });

// the sentence the player is at (lit): once it has played or been moved
const starts = computed(() => (c.value?.rows ?? []).map((r) => Date.parse(r.start)));
const hit = computed(() =>
  track.playing || track.at !== track.from
    ? (c.value?.rows[Math.max(0, rowAt(starts.value, track.at))]?.start ?? "")
    : "",
);
const rowEl = (start: string) => document.querySelector(`[data-start="${CSS.escape(start)}"]`);
const seekRow = (r: Row) => trackSeek(Date.parse(r.start), true);
// a time on the summary (or a suggestion): 原文 at that sentence, playing from it (a link from another page: only
// standing there)
async function listen(at: string | null, start = true) {
  if (!at) return;
  trackSeek(Date.parse(at), start); // first: inside the tap, where iOS lets the audio start
  await setTab("transcript");
  await nextTick();
  if (hit.value) rowEl(hit.value)?.scrollIntoView({ block: "center", behavior: start ? "smooth" : "auto" });
}
watch(hit, async (h) => {
  if (!h || !track.playing || !track.follow || tab.value !== "transcript") return;
  await nextTick();
  rowEl(h)?.scrollIntoView({ block: "center", behavior: "smooth" });
});
// a scroll by hand stops the page following the player, until the next seek (the slider is not one)
const byHand = (e: Event) => {
  if (!(e.target as Element | null)?.closest?.(".player")) track.follow = false;
};
onMounted(() => {
  addEventListener("wheel", byHand, { passive: true });
  addEventListener("touchmove", byHand, { passive: true });
});
onUnmounted(() => {
  removeEventListener("wheel", byHand);
  removeEventListener("touchmove", byHand);
  trackClose();
});

// who spoke how much (not counting media played aloud)
const share = computed(() => talkShare((c.value?.rows ?? []).filter((r) => !media(r))));
const pct = (x: number) => `${Math.max(1, Math.round(x * 100))}%`;

// suggestions for the labels (names they were called, merges, voices like a named speaker): applied only by a click
const sug = ref<LabelSuggestions | null>(null);
async function loadSug() {
  sug.value = await get<LabelSuggestions>(
    `/api/conversation/${encodeURIComponent(props.id)}/speakers`,
  ).catch(() => null);
}
const anon = (p: string) => /^S(\d+|\?)$/.test(p);
const allMerges = ref(false);
function hear(label: string) {
  const v = sug.value?.clips[label];
  if (!v) return;
  play(
    `${props.id}|${label}`,
    v.clip
      ? `/api/voice-clip/${v.clip}`
      : `/api/audio?${new URLSearchParams({ start: v.clip_start!, end: v.clip_end!, ...(c.value?.device && { device: c.value.device }) })}`,
  );
}
const said = { addressed: "叫作", self: "自称", referred: "称为" } as const;
const sugCount = computed(() =>
  sug.value
    ? sug.value.names.length + sug.value.merge.length + sug.value.like.length + sug.value.elsewhere.length
    : 0,
);

// the ⋯ menu and the panel it opened
const menu = ref(false),
  panel = ref<"" | "rename" | "sug" | "note">("");
function open(p: typeof panel.value) {
  menu.value = false;
  panel.value = p;
  editing.value = "";
}

watch(
  () => props.id,
  async () => {
    c.value = null;
    sug.value = null;
    allMerges.value = false;
    panel.value = "";
    void loadSug();
    await load();
    await nextTick();
    const at = route.query.at || decodeURIComponent(route.hash.slice(1)); // ?at= (a todo's sentence) or #<time>
    if (typeof at === "string" && at) void listen(at, false);
  },
  { immediate: true },
);
useSocket((m) => {
  if (m.type === "summary" && m.conversation === props.id) {
    load();
    loadSug();
  }
}, load);

const { busy: regenerating, error: regenError, detail: regenDetail, run: runRegen } = useMutation();
async function regen() {
  menu.value = false;
  await runRegen(async () => {
    await post(`/api/conversation/${props.id}/summarize`);
    toast("摘要重新生成中");
    await load();
  });
}
// who wrote the summary, when: on 重新生成's title, not on the page
const made = computed(() =>
  s.value?.model
    ? `AI 摘要 · ${(s.value.created_at ?? "").slice(5, 16).replace("T", " ")}，由 ${s.value.model} 生成` +
      (s.value.via === "live" ? "，会中实时分析写的" : "")
    : "",
);

// a correction in the owner's words, kept for a later read-back across many of them
const note = ref("");
const { busy: sending, error: noteError, detail: noteDetail, run: runNote } = useMutation();
async function sendNote() {
  if (!note.value.trim()) return;
  await runNote(async () => {
    c.value!.feedback = await post<Feedback[]>(`/api/conversation/${props.id}/feedback`, {
      text: note.value.trim(),
    });
    note.value = "";
    toast("收到，会用来改进");
  });
}
// a todo marked 完成 or 不对 (TodoMarks) says how
const MARKED = { done: "已完成", wrong: "已标不对" } as const;

// renaming one speaker of this conversation; a plain rename can be undone (renamed back), a merge cannot: the two
// labels' sentences are one label's after it
const editing = ref(""),
  newName = ref(""),
  names = ref<string[]>([]);
const { busy: saving, error, detail, run: runSpeaker } = useMutation();
const named = (p: string) => p !== "我" && !anon(p);
const labels = computed(() =>
  (c.value?.participants ?? []).filter((p) => p !== "我" && p !== "S?"),
);
async function edit(label: string) {
  editing.value = label;
  newName.value = "";
  error.value = "";
  names.value = (await get<string[]>("/api/names")).slice(0, 12);
}
async function rename(from: string, to: string, undoing = false) {
  const merge = !!c.value && (c.value.participants.includes(to) || c.value.rows.some((r) => r.speaker === to));
  await runSpeaker(async () => {
    const r = await post<{ renamed: number; voiceprints: number }>(
      `/api/conversation/${props.id}/rename`,
      { from, to },
    );
    editing.value = "";
    if (undoing) toast(`已改回 ${speakerLabel(to)}`);
    else if (merge) toast(`已把 ${speakerLabel(from)} 合并到 ${speakerLabel(to)}`);
    else
      toast(
        `已改名为 ${speakerLabel(to)}，改了 ${r.renamed} 句` + (r.voiceprints ? "，之后会自动认出" : ""),
        // renamed back; S? cannot be a rename's target
        from === "S?" ? {} : { undo: () => void rename(to, from, true) },
      );
    await Promise.all([load(), loadSug()]);
  });
}
const save = () => rename(editing.value, newName.value.trim());
async function alias(name: string, of: string) {
  await runSpeaker(async () => {
    await post("/api/aliases", { alias: name, name: of });
    toast(`已记下「${name}」是 ${of} 的别名`, {
      undo: () =>
        void runSpeaker(async () => {
          await post("/api/aliases", { alias: name, name: "" }); // no name drops it
          toast(`已删除别名「${name}」`);
          await loadSug();
        }),
    });
    await loadSug();
  });
}
</script>

<template>
  <AppHeader />
  <main :class="{ playing: tab === 'transcript' || track.playing }">
    <div class="top">
      <RouterLink class="back" to="/conversations"><Icon name="chevron-left" :size="18" />全部对话</RouterLink>
      <div v-if="c" class="more">
        <button
          class="btn-ghost"
          aria-label="更多"
          aria-haspopup="menu"
          :aria-expanded="menu"
          @click="menu = !menu"
        >
          <Icon name="more-horizontal" />
        </button>
        <template v-if="menu">
          <div class="scrim" @click="menu = false"></div>
          <div class="menu" role="menu">
            <button role="menuitem" :disabled="!labels.length" @click="open('rename')">
              <Icon name="user" :size="18" />改名
            </button>
            <button role="menuitem" :disabled="!sugCount" @click="open('sug')">
              <Icon name="users" :size="18" />说话人建议<span class="n">{{ sugCount || "暂无" }}</span>
            </button>
            <button role="menuitem" :disabled="regenerating" :title="made" @click="regen">
              <Icon name="sparkles" :size="18" />重新生成摘要
            </button>
            <button role="menuitem" @click="open('note')">
              <Icon name="alert-circle" :size="18" />哪里不对
            </button>
          </div>
        </template>
      </div>
    </div>
    <div v-if="missing" class="empty">
      <Icon name="message-circle" :size="48" />找不到这段对话
      <RouterLink class="btn btn-primary" to="/conversations">看全部对话</RouterLink>
    </div>
    <template v-else-if="c">
      <h1>{{ s?.status === "done" ? s.title || "（无标题）" : "对话 " + hhmm(c.start) }}</h1>
      <div class="muted meta">
        {{ dayLabel(c.start.slice(0, 10)) }} {{ hhmm(c.start) }}–{{ hhmm(c.end) }} · {{ minutes(c) }} 分钟
      </div>
      <div v-if="c.participants.length" class="people">
        <template v-for="p in c.participants" :key="p">
          <RouterLink v-if="named(p)" class="person" :to="`/p/${encodeURIComponent(p)}`"
            ><Avatar :name="p" :size="32" />{{ p }}</RouterLink
          >
          <span v-else class="person"><Avatar :name="p" :size="32" />{{ speakerLabel(p) }}</span>
        </template>
      </div>
      <div v-if="s?.tags?.length" class="tags">
        <RouterLink
          v-for="t in s.tags"
          :key="t"
          class="chip"
          :to="{ path: '/conversations', query: { tag: t } }"
          >{{ t }}</RouterLink
        >
      </div>

      <section v-if="panel === 'rename'" class="card panel">
        <div class="phead">
          <h3>改名</h3>
          <button class="btn-ghost x" aria-label="关闭" @click="panel = ''"><Icon name="x" /></button>
        </div>
        <div class="rename">
          <template v-if="!editing">
            <span class="muted">改谁：</span>
            <button v-for="l in labels" :key="l" class="chip" @click="edit(l)">{{ speakerLabel(l) }}</button>
          </template>
          <template v-else>
            <input
              v-model="newName"
              :placeholder="`${speakerLabel(editing)} 是谁`"
              autofocus
              @keydown.enter="save"
            />
            <button :disabled="saving || !newName.trim()" @click="save">保存</button>
            <button class="btn-ghost" @click="editing = ''">取消</button>
            <button v-for="n in names" :key="n" class="chip" @click="newName = n">{{ n }}</button>
          </template>
        </div>
      </section>

      <section v-if="panel === 'sug' && sug" class="card panel sug">
        <div class="phead">
          <h3>说话人建议 <span class="hint">点了才会改</span></h3>
          <button class="btn-ghost x" aria-label="关闭" @click="panel = ''"><Icon name="x" /></button>
        </div>
        <ul class="items">
          <li v-for="h in sug.names" :key="`n${h.who}${h.name}${h.at}`">
            <span class="who">{{ speakerLabel(h.who) }}</span
            >{{ h.kind === "self" ? "" : `被 ${speakerLabel(h.by)} ` }}{{ said[h.kind] }}「{{ h.name }}」<template
              v-if="h.canon !== h.name"
              >（{{ h.name }} 是 {{ h.canon }} 的别名）</template
            >
            <a class="at" :href="'#' + h.at" @click.prevent="listen(h.at)"
              ><Icon name="play" :size="12" />{{ hhmmss(h.at) }}</a
            >
            <button v-if="anon(h.who)" :disabled="saving" @click="rename(h.who, h.canon)">
              改名为 {{ h.canon }}
            </button>
            <button v-else-if="h.canon === h.name" :disabled="saving" @click="alias(h.name, h.who)">
              记为 {{ h.who }} 的别名
            </button>
            <div class="quote">「{{ h.quote }}」</div>
          </li>
          <li v-for="l in sug.like" :key="`l${l.who}`">
            <button v-if="sug.clips[l.who]" class="play" title="听一听" @click="hear(l.who)">
              <Icon :name="player.playing === `${id}|${l.who}` ? 'square' : 'play'" :size="14" /></button
            ><span class="who">{{ speakerLabel(l.who) }}</span>的声音{{ likeness(l.score) }} {{ l.name }}
            <button :disabled="saving" @click="rename(l.who, l.name)">是 {{ l.name }}</button>
          </li>
          <li v-for="e in sug.elsewhere" :key="`e${e.who}${e.name}`">
            <button v-if="sug.clips[e.who]" class="play" title="听一听" @click="hear(e.who)">
              <Icon :name="player.playing === `${id}|${e.who}` ? 'square' : 'play'" :size="14" /></button
            ><span class="who">{{ speakerLabel(e.who) }}</span>的声音在另外 {{ e.convs }} 段对话里被叫作「{{ e.name }}」
            <button :disabled="saving" @click="rename(e.who, e.name)">改名为 {{ e.name }}</button>
          </li>
          <li v-for="m in allMerges ? sug.merge : sug.merge.slice(0, 5)" :key="`m${m.from}${m.to}`">
            <span class="who"
              ><template v-for="(l, j) in [m.from, m.to]" :key="l"
                >{{ j ? " 和 " : "" }}<button
                  v-if="sug.clips[l]"
                  class="play"
                  :title="`听 ${speakerLabel(l)}`"
                  @click="hear(l)"
                >
                  <Icon :name="player.playing === `${id}|${l}` ? 'square' : 'play'" :size="14" /></button
                >{{ speakerLabel(l) }}</template
              ></span
            >可能是同一个人：{{
              m.name ? `都被叫作「${m.name}」` : `声音${likeness(m.score ?? 0)}`
            }}
            <button :disabled="saving" @click="rename(m.from, m.to)">合并到 {{ speakerLabel(m.to) }}</button>
          </li>
          <li v-if="sug.merge.length > 5 && !allMerges">
            <button class="btn-ghost" @click="allMerges = true">再显示 {{ sug.merge.length - 5 }} 条合并建议</button>
          </li>
        </ul>
      </section>

      <section v-if="panel === 'note'" class="card panel note">
        <div class="phead">
          <h3>哪里不对</h3>
          <button class="btn-ghost x" aria-label="关闭" @click="panel = ''"><Icon name="x" /></button>
        </div>
        <textarea
          v-model="note"
          rows="3"
          placeholder="哪里判断错了？比如：这段不是视频、说话人 2 其实是我、摘要漏了某件事"
        ></textarea>
        <button class="btn-primary" :disabled="sending || !note.trim()" @click="sendNote">提交纠正</button>
        <p v-if="noteError" class="bad" role="alert" :title="noteDetail">{{ noteError }}</p>
        <div v-for="(f, i) in c.feedback" :key="i" class="muted said">
          {{ f.at.slice(5, 16).replace("T", " ") }} {{ f.text }}
        </div>
      </section>

      <p v-if="error" class="bad" role="alert" :title="detail">{{ error }}</p>
      <p v-if="regenError" class="bad" role="alert" :title="regenDetail">{{ regenError }}</p>

      <div class="tabs" role="tablist">
        <button role="tab" :aria-selected="tab === 'summary'" @click="setTab('summary')">摘要</button>
        <button role="tab" :aria-selected="tab === 'transcript'" @click="setTab('transcript')">
          原文<span class="n">{{ c.rows.length }} 句</span>
        </button>
      </div>

      <template v-if="tab === 'summary'">
        <template v-if="s?.status === 'done'">
          <div v-if="s.summary" class="card summary">{{ s.summary }}</div>
          <div v-else-if="!s.model" class="card muted">语音不足 60 秒，没有生成摘要</div>
          <section v-if="s.todos?.length" class="card">
            <h3>待办</h3>
            <ul class="items">
              <li v-for="(t, i) in s.todos" :key="i" class="todo">
                <TodoMarks v-if="t.id" :id="t.id" :mark="t.mark" />
                <div class="body">
                  <div>{{ t.text }}</div>
                  <div class="sub">
                    <span class="who"
                      ><Avatar :name="t.owner" :size="20" />{{ t.owner ? speakerLabel(t.owner) : "未知"
                      }}{{ t.to ? ` → ${speakerLabel(t.to)}` : "" }}</span
                    ><span v-if="t.due" class="due"
                      >{{ t.due }}{{ t.due_date ? " · " + t.due_date.slice(5) : "" }}</span
                    ><a v-if="t.at" class="at" :href="'#' + t.at" @click.prevent="listen(t.at)"
                      ><Icon name="play" :size="12" />{{ hhmmss(t.at) }}</a
                    ><span v-if="markOf(t)" class="done">{{ MARKED[markOf(t)!] }}</span>
                  </div>
                  <div v-if="t.quote" class="quote">「{{ t.quote }}」</div>
                </div>
              </li>
            </ul>
          </section>
          <section v-if="s.ai_requests?.length" class="card">
            <h3>让 AI 做的事</h3>
            <ul class="items ai">
              <li v-for="(t, i) in s.ai_requests" :key="i">
                {{ t.text
                }}<a v-if="t.at" class="at" :href="'#' + t.at" @click.prevent="listen(t.at)"
                  ><Icon name="play" :size="12" />{{ hhmmss(t.at) }}</a
                >
              </li>
            </ul>
          </section>
          <section v-if="s.decisions?.length" class="card">
            <h3>决定</h3>
            <ul class="items">
              <li v-for="(d, i) in s.decisions" :key="i">
                {{ d.text
                }}<a v-if="d.at" class="at" :href="'#' + d.at" @click.prevent="listen(d.at)"
                  ><Icon name="play" :size="12" />{{ hhmmss(d.at) }}</a
                >
                <div v-if="d.quote" class="quote">「{{ d.quote }}」</div>
              </li>
            </ul>
          </section>
          <section v-if="s.people?.length" class="card">
            <h3>各人要点</h3>
            <div v-for="p in s.people" :key="p.who" class="points-of">
              <div class="who">
                <Avatar :name="p.who" />
                <RouterLink v-if="named(p.who)" :to="`/p/${encodeURIComponent(p.who)}`">{{ p.who }}</RouterLink>
                <span v-else>{{ speakerLabel(p.who) }}</span>
              </div>
              <ul class="points">
                <li v-for="x in p.points" :key="x">{{ x }}</li>
              </ul>
            </div>
          </section>
          <section v-if="s.topics?.length" class="card">
            <h3>议题</h3>
            <div v-for="(t, i) in s.topics" :key="i" class="topic">
              <b>{{ t.title }}</b
              ><a v-if="t.at" class="at" :href="'#' + t.at" @click.prevent="listen(t.at)"
                ><Icon name="play" :size="12" />{{ hhmmss(t.at) }}</a
              >
              <ul class="points">
                <li v-for="p in t.points" :key="p">{{ p }}</li>
              </ul>
            </div>
          </section>
        </template>
        <div v-else-if="s?.status === 'failed'" class="card bad failed">
          <span :title="s.error ?? ''">摘要没能生成</span>
          <button :disabled="regenerating" @click="regen">重新生成</button>
        </div>
        <div v-else class="card muted">摘要生成中…</div>
        <section v-if="c.timeline?.topics.length" class="card">
          <h3>会中实时议题</h3>
          <Timeline :topics="c.timeline.topics" @jump="listen" />
        </section>
      </template>

      <template v-else>
        <div v-if="share.length" class="card share">
          <div class="bar" aria-hidden="true">
            <span
              v-for="x in share"
              :key="x.who"
              :style="{ flexGrow: x.share, background: speakerColor(x.who) }"
            ></span>
          </div>
          <ul>
            <li v-for="x in share" :key="x.who">
              <Avatar :name="x.who" :size="20" />{{ speakerLabel(x.who) }}<b>{{ pct(x.share) }}</b>
            </li>
          </ul>
        </div>
        <div class="rows">
          <template v-for="g in segs" :key="g.k === 'row' ? g.r.id : g.key">
            <TranscriptRow
              v-if="g.k === 'row'"
              :row="g.r"
              :media="g.media"
              :hit="hit === g.r.start"
              :seek="seekRow"
            />
            <MediaFold v-else :rows="g.rows" :hit="hit" :seek="seekRow" />
          </template>
        </div>
      </template>
      <!-- on 摘要 too while it plays: what plays can always be stopped -->
      <PlayerBar v-if="tab === 'transcript' || track.playing" />
    </template>
  </main>
</template>

<style scoped>
main.playing {
  padding-bottom: 120px; /* the last sentence clears the player */
}
.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.back {
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  margin-left: -4px;
}
.more {
  position: relative;
}
.more > .btn-ghost {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  min-height: 44px;
  padding: 0;
  margin-right: -10px;
  color: var(--fg-2);
}
.scrim {
  position: fixed;
  inset: 0;
  z-index: 4;
}
.menu {
  position: absolute;
  right: 0;
  top: 100%;
  z-index: 5;
  min-width: 200px;
  padding: var(--space-1) 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: 0 4px 16px rgb(0 0 0 / 0.12);
}
.menu button {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-height: 44px;
  padding: 0 var(--space-4);
  border: 0;
  border-radius: 0;
  background: none;
  color: var(--fg);
  font-size: var(--fs-sm);
  text-align: left;
}
.menu button:disabled {
  color: var(--muted);
}
.menu .n {
  margin-left: auto;
  color: var(--muted);
  font-size: var(--fs-xs);
}
h1 {
  margin: var(--space-2) 0 var(--space-1);
}
.meta {
  font-size: var(--fs-sm);
}
.people {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  margin-top: var(--space-3);
}
.person {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-sm);
  color: var(--fg-2);
}
a.person {
  color: var(--fg);
}
.tags {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  padding-top: var(--space-3);
}
.panel {
  margin-top: var(--space-4);
}
.phead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: calc(-1 * var(--space-2)) calc(-1 * var(--space-2)) var(--space-2) 0;
}
.phead h3 {
  margin: 0;
}
.phead .x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  padding: 0;
  color: var(--muted);
}
.rename {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-sm);
}
.rename input {
  border-color: var(--accent);
  min-height: 36px;
  padding: 0 10px;
  width: 9em;
}
.note textarea {
  display: block;
  width: 100%;
  padding: var(--space-2) 10px;
  resize: vertical;
  margin-bottom: var(--space-2);
}
.note .said {
  font-size: var(--fs-sm);
  margin-top: var(--space-2);
}
.tabs {
  display: flex;
  gap: var(--space-5);
  margin: var(--space-4) 0 var(--space-3);
  border-bottom: 1px solid var(--line);
}
.tabs button {
  min-height: 44px;
  padding: 0 2px;
  margin-bottom: -1px;
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  color: var(--muted);
  font-size: var(--fs-md);
}
.tabs button[aria-selected="true"] {
  color: var(--fg);
  font-weight: 600;
  border-bottom-color: var(--accent);
}
.tabs .n {
  margin-left: var(--space-1);
  font-size: var(--fs-xs);
  font-weight: 400;
  color: var(--muted);
}
.card h3 {
  margin: 0 0 var(--space-2);
}
.summary {
  font-size: var(--fs-md);
}
ul.items {
  list-style: none;
  padding: 0;
  margin: 0;
}
ul.items > li {
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--line);
}
ul.items > li:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
/* the todo's tick (TodoMarks) on its left */
.todo {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}
.todo > .marks {
  margin: -3px 0 0;
}
.todo .body {
  flex: 1;
  min-width: 0;
}
.sub {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  column-gap: var(--space-3);
  font-size: var(--fs-xs);
  color: var(--muted);
}
.who {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--fg-2);
  font-size: var(--fs-sm);
  margin-right: 6px;
}
.sub .who {
  font-size: var(--fs-xs);
  margin: 0;
}
.due {
  color: var(--warn);
}
.done {
  color: var(--ok);
}
.quote {
  font-size: var(--fs-xs);
  color: var(--muted);
}
/* a time to listen from: a finger-sized target around a small label */
.at {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  min-height: 32px;
  padding: 0 var(--space-1);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.items .at,
.topic .at {
  margin-left: var(--space-1);
}
.points-of + .points-of {
  margin-top: var(--space-3);
}
.points {
  margin: 2px 0 0;
  padding-left: 22px;
  font-size: var(--fs-sm);
}
.topic + .topic {
  margin-top: var(--space-3);
}
.items.ai li {
  color: var(--muted);
}
.failed {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.sug .items {
  font-size: var(--fs-sm);
}
.sug button {
  margin-left: 6px;
}
.sug button.play {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  vertical-align: middle;
  margin: 0 4px 0 0;
  min-width: 32px;
  min-height: 32px;
  padding: 0;
}
.hint {
  font-weight: 400;
  font-size: var(--fs-xs);
  color: var(--muted);
}
.share .bar {
  display: flex;
  gap: 2px;
  height: 8px;
  border-radius: var(--radius-pill);
  overflow: hidden;
}
.share .bar span {
  flex-basis: 0;
  min-width: 2px;
}
.share ul {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-4);
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
  font-size: var(--fs-sm);
  color: var(--fg-2);
}
.share li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.share b {
  font-weight: 600;
  color: var(--fg);
  font-variant-numeric: tabular-nums;
}
</style>
