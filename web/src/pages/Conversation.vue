<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useRoute } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import MediaFold from "../components/MediaFold.vue";
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
  TODO_MARK,
  type Conversation,
  type Feedback,
  type LabelSuggestions,
  type LiveState,
  type Row,
} from "../lib/api";
import { play, player } from "../lib/audio";
import { useSocket } from "../lib/socket";
import { useMutation } from "../lib/mutation";

const props = defineProps<{ id: string }>();
const route = useRoute();
const c = ref<
    (Conversation & { rows: Row[]; timeline?: LiveState | null; feedback: Feedback[] }) | null
  >(null),
  missing = ref(false),
  hit = ref("");
const s = computed(() => c.value?.summary ?? null);
const segs = computed(() => fold(c.value?.rows ?? [], (r) => inSpans(r, s.value?.media_spans)));

async function load() {
  try {
    c.value = await get(`/api/conversation/${encodeURIComponent(props.id)}`);
  } catch {
    missing.value = true;
  }
}
function jumpTo(iso: string | null) {
  if (!iso) return;
  hit.value = iso;
  document.querySelector(`[data-start="${CSS.escape(iso)}"]`)?.scrollIntoView({ block: "center" });
}
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
const anySug = computed(
  () =>
    !!sug.value &&
    sug.value.names.length + sug.value.merge.length + sug.value.like.length + sug.value.elsewhere.length > 0,
);

watch(
  () => props.id,
  async () => {
    c.value = null;
    sug.value = null;
    allMerges.value = false;
    void loadSug();
    await load();
    await nextTick();
    if (route.hash) jumpTo(decodeURIComponent(route.hash.slice(1)));
  },
  { immediate: true },
);
useSocket((m) => {
  if (m.type === "summary" && m.conversation === props.id) {
    load();
    loadSug();
  }
}, load);

const { busy: regenerating, error: regenError, run: runRegen } = useMutation();
async function regen() {
  await runRegen(async () => {
    await post(`/api/conversation/${props.id}/summarize`);
    await load();
  });
}

// a correction in the owner's words, kept for a later read-back across many of them
const note = ref("");
const { busy: sending, error: noteError, run: runNote } = useMutation();
async function sendNote() {
  if (!note.value.trim()) return;
  await runNote(async () => {
    c.value!.feedback = await post<Feedback[]>(`/api/conversation/${props.id}/feedback`, {
      text: note.value.trim(),
    });
    note.value = "";
  });
}
// a todo marked 完成 or 不对 (TodoMarks) shows how instead of the buttons
const marked = (text: string) =>
  c.value?.feedback.some((f) => f.text === TODO_MARK.done + text)
    ? "已完成"
    : c.value?.feedback.some((f) => f.text === TODO_MARK.wrong + text)
      ? "已标不对"
      : "";

// renaming one speaker of this conversation
const editing = ref(""),
  newName = ref(""),
  names = ref<string[]>([]),
  flash = ref("");
const { busy: saving, error, run: runSpeaker } = useMutation();
const named = (p: string) => p !== "我" && !/^S(\d+|\?)$/.test(p);
const labels = computed(() =>
  (c.value?.participants ?? []).filter((p) => p !== "我" && p !== "S?"),
);
async function edit(label: string) {
  editing.value = label;
  newName.value = "";
  error.value = "";
  names.value = (await get<string[]>("/api/names")).slice(0, 12);
}
async function rename(from: string, to: string) {
  await runSpeaker(async () => {
    flash.value = "";
    const r = await post<{ renamed: number; voiceprints: number }>(
      `/api/conversation/${props.id}/rename`,
      { from, to },
    );
    flash.value =
      `${from} → ${to}，改了 ${r.renamed} 句` +
      (anon(to) || to === "我"
        ? ""
        : r.voiceprints
          ? `，记住了 ${r.voiceprints} 句的声音，之后的对话会自动认出`
          : "（这段没有录音，不记声音）") +
      "；摘要重新生成中";
    editing.value = "";
    await Promise.all([load(), loadSug()]);
  });
}
const save = () => rename(editing.value, newName.value.trim());
async function alias(name: string, of: string) {
  await runSpeaker(async () => {
    flash.value = "";
    await post("/api/aliases", { alias: name, name: of });
    flash.value = `已记下「${name}」是 ${of} 的别名，以后被这样叫会建议 ${of}`;
    await loadSug();
  });
}
</script>

<template>
  <AppHeader />
  <main>
    <RouterLink to="/conversations">← 全部对话</RouterLink>
    <div v-if="missing" class="empty">找不到这段对话</div>
    <template v-else-if="c">
      <h2>{{ s?.status === "done" ? s.title || "（无标题）" : "对话 " + hhmm(c.start) }}</h2>
      <div class="muted">
        {{ c.start.slice(0, 10) }} {{ hhmm(c.start) }}–{{ hhmm(c.end) }} · {{ minutes(c) }} 分钟 ·
        {{ c.rows.length }} 句 ·
        <template v-for="(p, i) in c.participants" :key="p"
          >{{ i ? "、" : ""
          }}<RouterLink v-if="named(p)" :to="`/p/${encodeURIComponent(p)}`">{{ p }}</RouterLink
          ><template v-else>{{ p }}</template></template
        ><template v-if="!c.participants.length">—</template>
      </div>

      <div v-if="labels.length" class="rename">
        <template v-if="!editing">
          <span class="muted">改名：</span>
          <button v-for="l in labels" :key="l" class="tag" @click="edit(l)">{{ l }}</button>
        </template>
        <template v-else>
          <input
            v-model="newName"
            :placeholder="`${editing} 是谁`"
            autofocus
            @keydown.enter="save"
          />
          <button :disabled="saving || !newName.trim()" @click="save">保存</button>
          <button @click="editing = ''">取消</button>
          <button v-for="n in names" :key="n" class="tag" @click="newName = n">{{ n }}</button>
        </template>
      </div>
      <div v-if="flash" class="flash">{{ flash }}</div>
      <p v-if="error" class="bad" role="alert">{{ error }}</p>

      <div v-if="anySug" class="sug">
        <h3>说话人建议 <span class="hint">点了才会改</span></h3>
        <ul class="items">
          <li v-for="h in sug!.names" :key="`n${h.who}${h.name}${h.at}`">
            <span class="who">{{ h.who }}</span
            >{{ h.kind === "self" ? "" : `被 ${h.by} ` }}{{ said[h.kind] }}「{{ h.name }}」<template
              v-if="h.canon !== h.name"
              >（{{ h.name }} 是 {{ h.canon }} 的别名）</template
            >
            <a class="at" :href="'#' + h.at" @click.prevent="jumpTo(h.at)">{{ hhmmss(h.at) }}</a>
            <button v-if="anon(h.who)" :disabled="saving" @click="rename(h.who, h.canon)">
              改名为 {{ h.canon }}
            </button>
            <button v-else-if="h.canon === h.name" :disabled="saving" @click="alias(h.name, h.who)">
              记为 {{ h.who }} 的别名
            </button>
            <div class="quote">「{{ h.quote }}」</div>
          </li>
          <li v-for="l in sug!.like" :key="`l${l.who}`">
            <button v-if="sug!.clips[l.who]" class="play" @click="hear(l.who)">
              {{ player.playing === `${id}|${l.who}` ? "■" : "▶" }}</button
            ><span class="who">{{ l.who }}</span>的声音像 {{ l.name }}（相似度 {{ l.score.toFixed(2) }}）
            <button :disabled="saving" @click="rename(l.who, l.name)">是 {{ l.name }}</button>
          </li>
          <li v-for="e in sug!.elsewhere" :key="`e${e.who}${e.name}`">
            <button v-if="sug!.clips[e.who]" class="play" @click="hear(e.who)">
              {{ player.playing === `${id}|${e.who}` ? "■" : "▶" }}</button
            ><span class="who">{{ e.who }}</span>的声音在另外 {{ e.convs }} 段对话里被叫作「{{ e.name }}」
            <button :disabled="saving" @click="rename(e.who, e.name)">改名为 {{ e.name }}</button>
          </li>
          <li v-for="m in allMerges ? sug!.merge : sug!.merge.slice(0, 5)" :key="`m${m.from}${m.to}`">
            <span class="who"
              ><template v-for="(l, j) in [m.from, m.to]" :key="l"
                >{{ j ? " 和 " : "" }}<button
                  v-if="sug!.clips[l]"
                  class="play"
                  :title="`听 ${l}`"
                  @click="hear(l)"
                >
                  {{ player.playing === `${id}|${l}` ? "■" : "▶" }}</button
                >{{ l }}</template
              ></span
            >可能是同一个人：{{
              m.name ? `都被叫作「${m.name}」` : `声音相似度 ${m.score?.toFixed(2)}`
            }}
            <button :disabled="saving" @click="rename(m.from, m.to)">合并到 {{ m.to }}</button>
          </li>
          <li v-if="sug!.merge.length > 5 && !allMerges">
            <button class="btn-ghost" @click="allMerges = true">再显示 {{ sug!.merge.length - 5 }} 条合并建议</button>
          </li>
        </ul>
      </div>

      <div v-if="s?.tags?.length" class="tags">
        <RouterLink
          v-for="t in s.tags"
          :key="t"
          class="tag"
          :to="{ path: '/conversations', query: { tag: t } }"
          >{{ t }}</RouterLink
        >
      </div>

      <template v-if="s?.status === 'done'">
        <div v-if="s.summary" class="summary card">{{ s.summary }}</div>
        <template v-if="s.people?.length">
          <h3>各人要点</h3>
          <ul class="items">
            <li v-for="p in s.people" :key="p.who">
              <RouterLink v-if="named(p.who)" class="who" :to="`/p/${encodeURIComponent(p.who)}`">{{
                p.who
              }}</RouterLink>
              <span v-else class="who">{{ p.who }}</span>
              <ul class="points">
                <li v-for="x in p.points" :key="x">{{ x }}</li>
              </ul>
            </li>
          </ul>
        </template>
        <template v-if="s.todos?.length">
          <h3>待办</h3>
          <ul class="items">
            <li v-for="(t, i) in s.todos" :key="i">
              <span class="who">{{ t.owner || "未知" }}{{ t.to ? ` → ${t.to}` : "" }}</span
              >{{ t.text
              }}<span v-if="t.due" class="due"
                >{{ t.due }}{{ t.due_date ? " · " + t.due_date.slice(5) : "" }}</span
              >
              <a v-if="t.at" class="at" :href="'#' + t.at" @click.prevent="jumpTo(t.at)">{{
                hhmmss(t.at)
              }}</a>
              <span v-if="marked(t.text)" class="muted"> · {{ marked(t.text) }}</span>
              <TodoMarks v-else :conv="id" :text="t.text" @marked="(f) => (c!.feedback = f)" />
              <div v-if="t.quote" class="quote">「{{ t.quote }}」</div>
            </li>
          </ul>
        </template>
        <template v-if="s.ai_requests?.length">
          <h3>让 AI 做的事</h3>
          <ul class="items ai">
            <li v-for="(t, i) in s.ai_requests" :key="i">
              {{ t.text }}
              <a v-if="t.at" class="at" :href="'#' + t.at" @click.prevent="jumpTo(t.at)">{{
                hhmmss(t.at)
              }}</a>
            </li>
          </ul>
        </template>
        <template v-if="s.decisions?.length">
          <h3>决定</h3>
          <ul class="items">
            <li v-for="(d, i) in s.decisions" :key="i">
              {{ d.text
              }}<a v-if="d.at" class="at" :href="'#' + d.at" @click.prevent="jumpTo(d.at)">{{
                hhmmss(d.at)
              }}</a>
              <div v-if="d.quote" class="quote">「{{ d.quote }}」</div>
            </li>
          </ul>
        </template>
        <template v-if="s.topics?.length">
          <h3>议题</h3>
          <div v-for="(t, i) in s.topics" :key="i" class="topic">
            <b>{{ t.title }}</b
            ><a v-if="t.at" class="at" :href="'#' + t.at" @click.prevent="jumpTo(t.at)">{{
              hhmmss(t.at)
            }}</a>
            <ul>
              <li v-for="p in t.points" :key="p">{{ p }}</li>
            </ul>
          </div>
        </template>
        <div class="muted foot">
          {{
            s.model
              ? `由 ${s.model}${s.via === "live" ? "（会中实时 Agent）" : ""} 生成 · ${(s.created_at ?? "").slice(5, 16).replace("T", " ")}`
              : "语音不足 60 秒，没有生成摘要"
          }}
          <button :disabled="regenerating" @click="regen">重新生成</button>
        </div>
      </template>
      <div v-else-if="s?.status === 'failed'" class="summary card bad">
        摘要生成失败：{{ s.error }} <button :disabled="regenerating" @click="regen">重新生成</button>
      </div>
      <div v-else class="summary card muted">摘要生成中…</div>
      <p v-if="regenError" class="bad" role="alert">{{ regenError }}</p>

      <div class="note">
        <textarea
          v-model="note"
          rows="2"
          placeholder="哪里判断错了？比如：这段不是视频、S2 其实是我、摘要漏了某件事"
        ></textarea>
        <button :disabled="sending || !note.trim()" @click="sendNote">提交纠正</button>
        <p v-if="noteError" class="bad" role="alert">{{ noteError }}</p>
        <div v-for="(f, i) in c.feedback" :key="i" class="muted">
          {{ f.at.slice(5, 16).replace("T", " ") }} {{ f.text }}
        </div>
      </div>

      <template v-if="c.timeline?.topics.length">
        <h3>会中实时议题</h3>
        <Timeline :topics="c.timeline.topics" @jump="jumpTo" />
      </template>

      <h3>原文</h3>
      <template v-for="g in segs" :key="g.k === 'row' ? g.r.id : g.key">
        <TranscriptRow v-if="g.k === 'row'" :row="g.r" :media="g.media" :hit="hit === g.r.start" />
        <MediaFold v-else :rows="g.rows" :hit="hit" />
      </template>
    </template>
  </main>
</template>

<style scoped>
h2 {
  margin: 16px 0 4px;
}
h3 {
  margin: 22px 0 6px;
}
.tags {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  padding-top: 8px;
}
.summary {
  margin-top: 12px;
}
ul.items {
  list-style: none;
  padding: 0;
  margin: 0;
}
ul.items > li {
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}
.points {
  margin: 2px 0 0;
  padding-left: 18px;
  font-size: var(--fs-sm);
}
.who {
  color: var(--accent);
  font-size: var(--fs-sm);
  margin-right: 6px;
}
.due {
  color: var(--warn);
  font-size: var(--fs-xs);
  margin-left: 6px;
}
.quote {
  font-size: var(--fs-xs);
  color: var(--muted);
}
.at {
  font-size: var(--fs-xs);
  margin-left: 6px;
  font-variant-numeric: tabular-nums;
}
.topic ul {
  margin: 2px 0 0;
  padding-left: 18px;
  font-size: var(--fs-sm);
}
.foot {
  margin-top: 12px;
}
.foot button {
  margin-left: 8px;
}
.rename {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-size: var(--fs-sm);
}
.rename input {
  border-color: var(--accent);
  padding: 4px 10px;
  width: 9em;
}
.flash {
  margin-top: 10px;
  font-size: var(--fs-sm);
  color: var(--accent);
}
.note {
  margin-top: 12px;
  font-size: var(--fs-sm);
}
.note textarea {
  width: 100%;
  padding: 6px 10px;
  resize: vertical;
}
.note button {
  margin: 4px 0;
}
.items.ai li {
  color: var(--muted);
}
.sug button {
  margin-left: 6px;
}
.sug button.play {
  margin: 0 4px 0 0;
  min-width: 32px;
  padding: 0;
}
.hint {
  font-weight: 400;
  font-size: var(--fs-xs);
  color: var(--muted);
}
</style>
