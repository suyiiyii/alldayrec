<script setup lang="ts">
// One conversation: its title, time and people, then two tabs. 摘要 (the default): how much recording it rests on, the
// summary, todos, decisions, each person's points, topics; ▶ on them plays about 10 s of the sentence they cite, its
// time opens 原文 there (QuoteAt). 原文: who spoke how
// much, the sentences, a player at the bottom that keeps the sentence playing lit and in view. The tab is in the
// query (?tab=transcript) so back and forward step through it; ?at=<time> or #<time> from another page opens 原文 there.
// Renaming, the speaker suggestions, a new summary and a correction are in the ⋯ menu; a long press on a sentence
// copies it, stars it or fixes its speaker (RowMenu)
import { computed, nextTick, onMounted, onUnmounted, ref, useTemplateRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import Avatar from "../components/Avatar.vue";
import Icon from "../components/Icon.vue";
import MediaFold from "../components/MediaFold.vue";
import PlayerBar from "../components/PlayerBar.vue";
import QuoteAt from "../components/QuoteAt.vue";
import RowMenu from "../components/RowMenu.vue";
import Timeline from "../components/Timeline.vue";
import TodoMarks from "../components/TodoMarks.vue";
import TranscriptRow from "../components/TranscriptRow.vue";
import { t } from "../i18n";
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
import { useDismiss } from "../lib/dismiss";
import { useSocket } from "../lib/socket";
import { useMutation } from "../lib/mutation";
import { likeness, speakerColor, speakerLabel } from "../lib/speakers";
import { loadStars } from "../lib/stars";
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

// the sentence the player is at (lit): once it has played or been moved, or jumped to (the first one too, where the
// player starts)
const starts = computed(() => (c.value?.rows ?? []).map((r) => Date.parse(r.start)));
const jumped = ref(false);
const hit = computed(() =>
  track.playing || track.at !== track.from || jumped.value
    ? (c.value?.rows[Math.max(0, rowAt(starts.value, track.at))]?.start ?? "")
    : "",
);
const rowEl = (start: string) => document.querySelector(`[data-start="${CSS.escape(start)}"]`);
const seekRow = (r: Row) => trackSeek(Date.parse(r.start), true);
const rowMenu = useTemplateRef("rowMenu");
const press = (r: Row, x: number, y: number) => rowMenu.value?.open(r, x, y, c.value);
const reload = () => Promise.all([load(), loadSug()]); // a sentence's speaker fixed or put back
// a time on the summary (or a suggestion): 原文 at that sentence, playing from it (a summary item's, or a link from
// another page: only standing there, lit)
async function listen(at: string | null, start = true) {
  if (!at) return;
  trackSeek(Date.parse(at), start); // first: inside the tap, where iOS lets the audio start
  jumped.value = true;
  await setTab("transcript");
  await nextTick();
  if (hit.value) rowEl(hit.value)?.scrollIntoView({ block: "center", behavior: start ? "smooth" : "auto" });
}
const jump = (at: string) => listen(at, false);
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
  void loadStars().catch(() => {});
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
const sugCount = computed(() =>
  sug.value
    ? sug.value.names.length + sug.value.merge.length + sug.value.like.length + sug.value.elsewhere.length
    : 0,
);

// the ⋯ menu and the panel it opened
const menu = ref(false),
  panel = ref<"" | "rename" | "sug" | "note">("");
useDismiss(menu, useTemplateRef("more"));
function open(p: typeof panel.value) {
  menu.value = false;
  panel.value = p;
  editing.value = "";
}

watch(
  () => props.id,
  async () => {
    c.value = null;
    jumped.value = false;
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
    toast(t("conv.regenerating"));
    await load();
  });
}
// who wrote the summary, when: on 重新生成's title, not on the page
const made = computed(() =>
  s.value?.model
    ? t(s.value.via === "live" ? "conv.madeLive" : "conv.made", {
        at: (s.value.created_at ?? "").slice(5, 16).replace("T", " "),
        model: s.value.model,
      })
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
    toast(t("conv.noteThanks"));
  });
}

// renaming one speaker of this conversation, or merging it into another one; either can be undone for 10 s (the
// server keeps what it changed: POST /api/speakers/undo)
const editing = ref(""),
  newName = ref(""),
  names = ref<string[]>([]);
const { busy: saving, error, detail, run: runSpeaker } = useMutation();
const named = (p: string) => p !== "我" && !anon(p);
// A bad recording splits into dozens of speakers: past 8 the row folds to the named ones and the first few
const FOLD = 8;
const allPeople = ref(false);
const people = computed(() => {
  const all = c.value?.participants ?? [];
  if (all.length <= FOLD || allPeople.value) return { shown: all, more: 0 };
  const shown = [...all.filter(named), ...all.filter((p) => !named(p))].slice(0, 6);
  return { shown, more: all.length - shown.length };
});
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
  const merge = !!c.value && (c.value.participants.includes(to) || c.value.rows.some((r) => r.speaker === to));
  await runSpeaker(async () => {
    const r = await post<{ renamed: number; voiceprints: number; learned?: boolean; undo: string }>(
      `/api/conversation/${props.id}/rename`,
      { from, to },
    );
    editing.value = "";
    toast(
      to === "我"
        ? t(r.learned ? "speaker.toMe" : "speaker.toMeShort")
        : merge
          ? t("conv.merged", { from: speakerLabel(from), to: speakerLabel(to) })
          : t(r.voiceprints ? "conv.renamedLearn" : "conv.renamed", { to: speakerLabel(to), n: r.renamed }, r.renamed),
      { undo: () => void undo(r.undo, from), ms: 10_000 },
    );
    await Promise.all([load(), loadSug()]);
  });
}
async function undo(id: string, from: string) {
  await runSpeaker(async () => {
    await post("/api/speakers/undo", { undo: id });
    toast(t("conv.renamedBack", { to: speakerLabel(from) }));
    await Promise.all([load(), loadSug()]);
  });
}
const save = () => rename(editing.value, newName.value.trim());
async function alias(name: string, of: string) {
  await runSpeaker(async () => {
    await post("/api/aliases", { alias: name, name: of });
    toast(t("conv.aliasSaved", { name, of }), {
      undo: () =>
        void runSpeaker(async () => {
          await post("/api/aliases", { alias: name, name: "" }); // no name drops it
          toast(t("conv.aliasRemoved", { name }));
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
      <RouterLink class="back" to="/"><Icon name="chevron-left" :size="18" />{{ t("conv.all") }}</RouterLink>
      <div v-if="c" ref="more" class="more">
        <button
          class="btn-ghost"
          :aria-label="t('conv.more')"
          aria-haspopup="menu"
          :aria-expanded="menu"
          @click="menu = !menu"
        >
          <Icon name="more-horizontal" />
        </button>
        <div v-if="menu" class="menu" role="menu">
          <button role="menuitem" :disabled="!labels.length" @click="open('rename')">
            <Icon name="user" :size="18" />{{ t("conv.rename") }}
          </button>
          <button role="menuitem" :disabled="!sugCount" @click="open('sug')">
            <Icon name="users" :size="18" />{{ t("conv.sug")
            }}<span class="n">{{ sugCount || t("conv.none") }}</span>
          </button>
          <button role="menuitem" :disabled="regenerating" :title="made" @click="regen">
            <Icon name="sparkles" :size="18" />{{ t("conv.regen") }}
          </button>
          <button role="menuitem" @click="open('note')">
            <Icon name="alert-circle" :size="18" />{{ t("conv.note") }}
          </button>
        </div>
      </div>
    </div>
    <div v-if="missing" class="empty">
      <Icon name="message-circle" :size="48" />{{ t("conv.missing") }}
      <RouterLink class="btn btn-primary" to="/">{{ t("conv.seeAll") }}</RouterLink>
    </div>
    <template v-else-if="c">
      <h1>
        {{ s?.status === "done" ? s.title || t("conv.untitled") : t("conv.at", { t: hhmm(c.start) }) }}
      </h1>
      <div class="muted meta">
        {{ dayLabel(c.start.slice(0, 10)) }} {{ hhmm(c.start) }}–{{ hhmm(c.end) }} ·
        {{ t("common.min", { n: minutes(c) }) }}
      </div>
      <div v-if="c.participants.length" class="people">
        <template v-for="p in people.shown" :key="p">
          <RouterLink v-if="named(p)" class="person" :to="`/p/${encodeURIComponent(p)}`"
            ><Avatar :name="p" :size="32" />{{ p }}</RouterLink
          >
          <span v-else class="person"><Avatar :name="p" :size="32" />{{ speakerLabel(p) }}</span>
        </template>
        <button v-if="people.more" class="btn-ghost more" @click="allPeople = true">
          {{ t("conv.morePeople", { n: people.more }) }}
        </button>
        <button v-else-if="allPeople" class="btn-ghost more" @click="allPeople = false">
          {{ t("conv.less") }}
        </button>
      </div>
      <div v-if="s?.tags?.length" class="tags">
        <RouterLink
          v-for="t in s.tags"
          :key="t"
          class="chip"
          :to="{ path: '/', query: { tag: t } }"
          >{{ t }}</RouterLink
        >
      </div>

      <section v-if="panel === 'rename'" class="card panel">
        <div class="phead">
          <h3>{{ t("conv.rename") }}</h3>
          <button class="btn-ghost x" :aria-label="t('conv.close')" @click="panel = ''"><Icon name="x" /></button>
        </div>
        <div class="rename">
          <template v-if="!editing">
            <span class="muted">{{ t("conv.renameWho") }}</span>
            <button v-for="l in labels" :key="l" class="chip" @click="edit(l)">{{ speakerLabel(l) }}</button>
          </template>
          <template v-else>
            <button v-if="anon(editing)" :disabled="saving" @click="rename(editing, '我')">
              {{ t("speaker.isMe") }}
            </button>
            <input
              v-model="newName"
              :placeholder="t('conv.whoIs', { who: speakerLabel(editing) })"
              autofocus
              @keydown.enter="save"
            />
            <button :disabled="saving || !newName.trim()" @click="save">{{ t("conv.save") }}</button>
            <button class="btn-ghost" @click="editing = ''">{{ t("conv.cancel") }}</button>
            <button v-for="n in names" :key="n" class="chip" @click="newName = n">{{ n }}</button>
          </template>
        </div>
      </section>

      <section v-if="panel === 'sug' && sug" class="card panel sug">
        <div class="phead">
          <h3>
            {{ t("conv.sug") }} <span class="hint">{{ t("conv.sugHint") }}</span>
          </h3>
          <button class="btn-ghost x" :aria-label="t('conv.close')" @click="panel = ''"><Icon name="x" /></button>
        </div>
        <ul class="items">
          <li v-for="h in sug.names" :key="`n${h.who}${h.name}${h.at}`">
            <span class="who">{{ speakerLabel(h.who) }}</span
            >{{ t(`conv.hint.${h.kind}`, { by: speakerLabel(h.by), name: h.name }) }}<template
              v-if="h.canon !== h.name"
              >{{ t("conv.aliasOf", { name: h.name, canon: h.canon }) }}</template
            >
            <a class="at" :href="'#' + h.at" @click.prevent="listen(h.at)"
              ><Icon name="play" :size="12" />{{ hhmmss(h.at) }}</a
            >
            <button v-if="anon(h.who)" :disabled="saving" @click="rename(h.who, h.canon)">
              {{ t("conv.renameTo", { name: h.canon }) }}
            </button>
            <button v-else-if="h.canon === h.name" :disabled="saving" @click="alias(h.name, h.who)">
              {{ t("conv.saveAlias", { who: h.who }) }}
            </button>
            <div class="quote">{{ t("conv.quote", { q: h.quote }) }}</div>
          </li>
          <li v-for="l in sug.like" :key="`l${l.who}`">
            <button v-if="sug.clips[l.who]" class="play" :title="t('conv.hear')" @click="hear(l.who)">
              <Icon :name="player.playing === `${id}|${l.who}` ? 'square' : 'play'" :size="14" /></button
            ><span class="who">{{ speakerLabel(l.who) }}</span
            >{{ t("conv.like", { likeness: likeness(l.score), name: l.name }) }}
            <button :disabled="saving" @click="rename(l.who, l.name)">
              {{ t("conv.isName", { name: l.name }) }}
            </button>
          </li>
          <li v-for="e in sug.elsewhere" :key="`e${e.who}${e.name}`">
            <button v-if="sug.clips[e.who]" class="play" :title="t('conv.hear')" @click="hear(e.who)">
              <Icon :name="player.playing === `${id}|${e.who}` ? 'square' : 'play'" :size="14" /></button
            ><span class="who">{{ speakerLabel(e.who) }}</span
            >{{ t("conv.elsewhere", { n: e.convs, name: e.name }, e.convs) }}
            <button :disabled="saving" @click="rename(e.who, e.name)">
              {{ t("conv.renameTo", { name: e.name }) }}
            </button>
          </li>
          <li v-for="m in allMerges ? sug.merge : sug.merge.slice(0, 5)" :key="`m${m.from}${m.to}`">
            <span class="who"
              ><template v-for="(l, j) in [m.from, m.to]" :key="l"
                >{{ j ? t("conv.and") : "" }}<button
                  v-if="sug.clips[l]"
                  class="play"
                  :title="t('conv.hearWho', { who: speakerLabel(l) })"
                  @click="hear(l)"
                >
                  <Icon :name="player.playing === `${id}|${l}` ? 'square' : 'play'" :size="14" /></button
                >{{ speakerLabel(l) }}</template
              ></span
            >{{
              t("conv.same", {
                why: m.name
                  ? t("conv.sameName", { name: m.name })
                  : t("conv.sameVoice", { likeness: likeness(m.score ?? 0) }),
              })
            }}
            <button :disabled="saving" @click="rename(m.from, m.to)">
              {{ t("conv.mergeInto", { to: speakerLabel(m.to) }) }}
            </button>
          </li>
          <li v-if="sug.merge.length > 5 && !allMerges">
            <button class="btn-ghost" @click="allMerges = true">
              {{ t("conv.moreMerges", sug.merge.length - 5) }}
            </button>
          </li>
        </ul>
      </section>

      <section v-if="panel === 'note'" class="card panel note">
        <div class="phead">
          <h3>{{ t("conv.note") }}</h3>
          <button class="btn-ghost x" :aria-label="t('conv.close')" @click="panel = ''"><Icon name="x" /></button>
        </div>
        <textarea
          v-model="note"
          rows="3"
          :placeholder="t('conv.notePlaceholder')"
        ></textarea>
        <button class="btn-primary" :disabled="sending || !note.trim()" @click="sendNote">
          {{ t("conv.noteSend") }}
        </button>
        <p v-if="noteError" class="bad" role="alert" :title="noteDetail">{{ noteError }}</p>
        <div v-for="(f, i) in c.feedback" :key="i" class="muted said">
          {{ f.at.slice(5, 16).replace("T", " ") }} {{ f.text }}
        </div>
      </section>

      <p v-if="error" class="bad" role="alert" :title="detail">{{ error }}</p>
      <p v-if="regenError" class="bad" role="alert" :title="regenDetail">{{ regenError }}</p>

      <div class="tabs" role="tablist">
        <button role="tab" :aria-selected="tab === 'summary'" @click="setTab('summary')">
          {{ t("conv.summary") }}
        </button>
        <button role="tab" :aria-selected="tab === 'transcript'" @click="setTab('transcript')">
          {{ t("conv.transcript")
          }}<span class="n">{{ t("conv.sentences", c.rows.length) }}</span>
        </button>
      </div>

      <template v-if="tab === 'summary'">
        <button v-if="c.speech_s != null" class="chip origin" @click="setTab('transcript')">
          <Icon name="volume-2" :size="14" />{{
            t("conv.origin", { min: Math.max(1, Math.round(c.speech_s / 60)), n: c.sentences }, c.sentences)
          }}
        </button>
        <template v-if="s?.status === 'done'">
          <div v-if="s.summary" class="card summary">{{ s.summary }}</div>
          <div v-else-if="!s.model" class="card muted">{{ t("conv.tooShort") }}</div>
          <section v-if="s.todos?.length" class="card">
            <h3>{{ t("conv.todos") }}</h3>
            <ul class="items">
              <li v-for="(td, i) in s.todos" :key="i" class="todo">
                <TodoMarks v-if="td.id" :id="td.id" :mark="td.mark" />
                <div class="body">
                  <div>{{ td.text }}</div>
                  <div class="sub">
                    <span class="who"
                      ><Avatar :name="td.owner" :size="20" />{{
                        td.owner ? speakerLabel(td.owner) : t("common.unknown")
                      }}{{ td.to ? ` → ${speakerLabel(td.to)}` : "" }}</span
                    ><span v-if="td.due" class="due"
                      >{{ td.due }}{{ td.due_date ? " · " + td.due_date.slice(5) : "" }}</span
                    ><QuoteAt :at="td.at" :c="c" @jump="jump" /><span v-if="markOf(td)" class="done">{{ t(`conv.marked.${markOf(td)}`) }}</span>
                  </div>
                  <div v-if="td.quote" class="quote">{{ t("conv.quote", { q: td.quote }) }}</div>
                </div>
              </li>
            </ul>
          </section>
          <section v-if="s.ai_requests?.length" class="card">
            <h3>{{ t("conv.aiRequests") }}</h3>
            <ul class="items ai">
              <li v-for="(t, i) in s.ai_requests" :key="i">
                {{ t.text }}<QuoteAt :at="t.at" :c="c" @jump="jump" />
              </li>
            </ul>
          </section>
          <section v-if="s.decisions?.length" class="card">
            <h3>{{ t("conv.decisions") }}</h3>
            <ul class="items">
              <li v-for="(d, i) in s.decisions" :key="i">
                {{ d.text }}<QuoteAt :at="d.at" :c="c" @jump="jump" />
                <div v-if="d.quote" class="quote">{{ t("conv.quote", { q: d.quote }) }}</div>
              </li>
            </ul>
          </section>
          <section v-if="s.people?.length" class="card">
            <h3>{{ t("conv.people") }}</h3>
            <div v-for="p in s.people" :key="p.who" class="points-of">
              <div class="who">
                <Avatar :name="p.who" />
                <RouterLink v-if="named(p.who)" :to="`/p/${encodeURIComponent(p.who)}`">{{ p.who }}</RouterLink>
                <span v-else>{{ speakerLabel(p.who) }}</span>
              </div>
              <ul class="points">
                <li v-for="(x, i) in p.points" :key="x">
                  {{ x }}<QuoteAt :at="p.points_at?.[i]" :c="c" @jump="jump" />
                </li>
              </ul>
            </div>
          </section>
          <section v-if="s.topics?.length" class="card">
            <h3>{{ t("conv.topics") }}</h3>
            <div v-for="(t, i) in s.topics" :key="i" class="topic">
              <b>{{ t.title }}</b><QuoteAt :at="t.at" :c="c" @jump="jump" />
              <ul class="points">
                <li v-for="p in t.points" :key="p">{{ p }}</li>
              </ul>
            </div>
          </section>
        </template>
        <div v-else-if="s?.status === 'failed'" class="card bad failed">
          <span :title="s.error ?? ''">{{ t("conv.failed") }}</span>
          <button :disabled="regenerating" @click="regen">{{ t("conv.regenShort") }}</button>
        </div>
        <div v-else class="card muted">{{ t("api.summaryPending") }}</div>
        <section v-if="c.timeline?.topics.length" class="card">
          <h3>{{ t("conv.liveTopics") }}</h3>
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
              :menu="press"
            />
            <MediaFold v-else :rows="g.rows" :hit="hit" :seek="seekRow" :menu="press" />
          </template>
        </div>
      </template>
      <!-- on 摘要 too: a fix's 撤销, tapped after a switch to it, still reloads the page -->
      <RowMenu ref="rowMenu" @changed="reload" />
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
.people .more {
  font-size: var(--fs-sm);
  padding: 0 var(--space-2);
  min-height: 32px;
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
.origin {
  gap: var(--space-1);
  margin-bottom: var(--space-3);
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
