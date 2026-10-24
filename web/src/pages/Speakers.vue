<script setup lang="ts">
import { computed, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Avatar from "../components/Avatar.vue";
import Icon from "../components/Icon.vue";
import Skeleton from "../components/Skeleton.vue";
import { lang, t } from "../i18n";
import {
  errorTitle,
  get,
  hhmm,
  post,
  type Sample,
  type Speaker,
  type SpeakerList,
  type Voice,
} from "../lib/api";
import { play, player } from "../lib/audio";
import { shiftDay, today } from "../lib/calendar";
import { useDismiss } from "../lib/dismiss";
import { useMutation } from "../lib/mutation";
import { likeness, speakerLabel } from "../lib/speakers";
import { toast } from "../lib/toast";

const groups = ref<Speaker[]>([]),
  others = ref<Speaker[]>([]), // 其他声音: little speech or one conversation, loaded when the fold opens
  othersN = ref(0),
  othersOpen = ref(false),
  shownOthers = ref(30),
  names = ref<string[]>([]),
  loaded = ref(false),
  filter = ref<"unnamed" | "all">("all"),
  shown = ref(30),
  open = ref(""),
  members = ref<Voice[]>([]),
  editing = ref(""),
  newName = ref(""),
  flash = ref(""),
  failed = ref<{ msg: string; title: string } | null>(null); // the list itself did not load
// one per card: a change under way there (a rename of many conversations takes seconds) leaves the other cards usable
const cards = new Map<string, ReturnType<typeof useMutation>>();
const card = (id: string) => cards.get(id) ?? cards.set(id, useMutation()).get(id)!;

async function load() {
  try {
    const [r, n, o] = await Promise.all([
      get<SpeakerList>("/api/speakers"),
      get<string[]>("/api/names"),
      othersOpen.value ? get<SpeakerList>("/api/speakers?others=1") : null,
    ]);
    [groups.value, othersN.value, names.value] = [r.cards, r.others, n];
    if (o) others.value = o.cards;
    failed.value = null;
  } catch (e) {
    failed.value = { msg: (e as Error).message, title: errorTitle(e) };
  }
  loaded.value = true;
}
void load();

// people as cards, the one heard last first; 待命名 narrows it to the voices without a name (or with a suggestion)
const list = computed(() =>
  groups.value
    .filter((g) => filter.value === "all" || !g.name || g.suggested.length)
    .sort((a, b) => (b.last ?? "").localeCompare(a.last ?? "")),
);
const unnamed = computed(() => groups.value.filter((g) => !g.name).length);
async function fold() {
  othersOpen.value = !othersOpen.value;
  if (!othersOpen.value || others.value.length) return;
  try {
    others.value = (await get<SpeakerList>("/api/speakers?others=1")).cards;
  } catch (e) {
    othersOpen.value = false;
    toast((e as Error).message);
  }
}
// the cards, then the fold row (with 再显示 of the cards above it), then the folded cards once it is open
const FOLD = { id: "\0fold", fold: true } as const;
const rows = computed(() => [
  ...list.value.slice(0, shown.value),
  FOLD,
  ...(othersOpen.value
    ? [...others.value]
        .sort((a, b) => (b.last ?? "").localeCompare(a.last ?? ""))
        .slice(0, shownOthers.value)
    : []),
]);

const mins = (s: number) =>
  s < 60 ? t("common.sec", { n: Math.round(s) }) : t("common.min", { n: Math.round(s / 60) });
const day = (iso: string) => `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
const heard = (iso: string) =>
  iso.startsWith(today())
    ? t("common.todayAt", { t: hhmm(iso) })
    : iso.startsWith(shiftDay(today(), -1))
      ? t("common.yesterdayAt", { t: hhmm(iso) })
      : iso.slice(5, 10);
const voiceId = (v: Sample) => `${v.conv}|${v.label}`;
function listen(v: Sample) {
  const src = v.clip
    ? `/api/voice-clip/${v.clip}`
    : `/api/audio?${new URLSearchParams({ start: v.clip_start!, end: v.clip_end! })}`;
  play(voiceId(v), src);
}
const nums = computed(
  () =>
    new Map([...groups.value, ...others.value].filter((g) => !g.name).map((g, i) => [g, i + 1])),
);
const num = (g: Speaker) => nums.value.get(g) ?? 0;
const title = (g: Speaker) => (g.name ? speakerLabel(g.name) : t("speakers.voice", { n: num(g) }));
// names a voice was called, each in quotes
const quoted = (names: string[]) =>
  names.map((name) => t("speakers.quoted", { name })).join(lang() === "zh" ? "" : ", ");

function edit(id: string) {
  editing.value = id;
  newName.value = "";
  card(id).error.value = "";
}
function cancel() {
  card(editing.value).error.value = "";
  editing.value = "";
}
async function expand(g: Speaker) {
  if (open.value === g.id) return (open.value = "");
  open.value = g.id;
  members.value = [];
  members.value = (await get<{ members: Voice[] }>(`/api/speakers/${encodeURIComponent(g.id)}`)).members;
}
// 这张卡像是两个人: a card's two halves (the server's two-half test), from naming it (to: the name asked
// for) or from its ⋯ 拆开; a half's voices (conv|label) are what 只命名这一半 names and 拆开 moves out
interface Half {
  voices: string[];
  count: number;
  speech_s: number;
  samples: Sample[];
}
type Halves = { mixed: boolean; halves: Half[] };
type Sheet = { id: string; halves: Half[]; to?: string; what?: string; suggestion?: string };
const sheet = ref<Sheet | null>(null);
const menu = ref(false),
  menuFor = ref(""),
  menuEl = ref<HTMLElement | null>(null);
useDismiss(menu, menuEl);
function more(g: Speaker, e: Event) {
  menuEl.value = (e.currentTarget as HTMLElement).parentElement;
  menu.value = !(menu.value && menuFor.value === g.id);
  menuFor.value = g.id;
}
async function halves(g: Speaker) {
  menu.value = false;
  await card(g.id).run(async () => {
    const r = await get<Halves>(`/api/speakers/${encodeURIComponent(g.id)}/halves`);
    if (r.mixed) sheet.value = { id: g.id, halves: r.halves };
    else toast(t("speakers.oneVoice"));
  });
}
async function split(g: Speaker, h: Half) {
  await card(g.id).run(async () => {
    const r = await post<{ moved: number; undo: string }>(
      `/api/speakers/${encodeURIComponent(g.id)}/split`,
      { voices: h.voices },
    );
    sheet.value = null;
    toast(t("speakers.splitDone", { n: r.moved }, r.moved), {
      undo: () => void undo(r.undo),
      ms: 10_000,
    });
    await load();
  });
}
async function name(
  g: Speaker,
  to: string,
  what: string,
  suggestion?: string,
  only: { voices?: string[]; all?: boolean } = {},
) {
  to = to.trim();
  if (!to) return;
  await card(g.id).run(async () => {
    flash.value = "";
    const r = await post<Halves | { renamed: number; learned?: boolean; undo: string | null }>(
      "/api/speakers/name",
      { name: to, group: g.id, suggestion, ...only },
    );
    if ("mixed" in r) {
      sheet.value = { id: g.id, halves: r.halves, to, what, suggestion };
      return;
    }
    sheet.value = null;
    // 撤销 for 10 s: every conversation of it back as it was (the server keeps what it changed)
    // 这是我: 我's voiceprint learnt it too, unless there was too little of it (server learn_me)
    toast(
      to === "我"
        ? t(r.learned ? "speaker.toMe" : "speaker.toMeShort")
        : t("speakers.renamed", { what, to: speakerLabel(to), n: r.renamed }, r.renamed),
      r.undo ? { undo: () => void undo(r.undo!), ms: 10_000 } : {},
    );
    if (editing.value === g.id) editing.value = ""; // another card's box opened meanwhile stays
    await load();
  });
}
async function undo(id: string) {
  try {
    await post("/api/speakers/undo", { undo: id });
    toast(t("common.undone"));
    await load();
  } catch (e) {
    toast((e as Error).message);
  }
}
async function addAlias(g: Speaker, alias: string, name: string) {
  await card(g.id).run(async () => {
    flash.value = "";
    await post("/api/aliases", { alias, name });
    flash.value = t("speakers.aliasSaved", { alias, name: speakerLabel(name) });
    await load();
  });
}
async function detach(g: Speaker, v: Voice) {
  await card(g.id).run(async () => {
    flash.value = "";
    await post("/api/speakers/detach", { conv: v.conv, label: v.label, group: g.name ?? g.id });
    flash.value = t("speakers.detached", { day: day(v.start), group: title(g) });
    members.value = members.value.filter((m) => m !== v);
    await load();
  });
}
</script>

<template>
  <AppHeader />
  <main>
    <h1>{{ t("title.people") }}</h1>
    <div v-if="groups.length" class="tabs">
      <button class="chip" :class="{ on: filter === 'all' }" @click="filter = 'all'">
        {{ t("speakers.all", { n: groups.length }) }}
      </button>
      <button class="chip" :class="{ on: filter === 'unnamed' }" @click="filter = 'unnamed'">
        {{ t("speakers.unnamed", { n: unnamed }) }}
      </button>
    </div>
    <p v-if="flash" class="flash">{{ flash }}</p>
    <Skeleton v-if="!loaded" />
    <div v-else-if="failed" class="empty" :title="failed.title">
      <Icon name="alert-circle" :size="48" />{{ failed.msg }}
    </div>
    <div v-else-if="!groups.length && !othersN" class="empty">
      <Icon name="users" :size="48" />
      <div>{{ t("speakers.none") }}</div>
      <RouterLink class="btn btn-primary" to="/connect">{{ t("title.connect") }}</RouterLink>
    </div>
    <p v-else-if="!list.length && !othersN" class="empty">{{ t("speakers.allNamed") }}</p>

    <template v-for="g in rows" :key="g.id">
      <template v-if="'fold' in g">
        <button v-if="list.length > shown" class="more" @click="shown += 30">
          {{ t("speakers.more") }}
        </button>
        <button v-if="othersN" class="fold" :aria-expanded="othersOpen" @click="fold">
          <Icon :name="othersOpen ? 'chevron-down' : 'chevron-right'" :size="16" />
          <span
            >{{ t("speakers.others", { n: othersN }) }}
            <span class="meta">{{ t("speakers.othersWhy") }}</span></span
          >
        </button>
        <p v-if="othersOpen && !others.length" class="meta">{{ t("speakers.loading") }}</p>
      </template>
      <section v-else class="card">
        <div class="head">
          <Avatar :name="g.name ?? 'S' + num(g)" :size="32" />
          <div class="who">
            <RouterLink
              v-if="g.name && g.name !== '我'"
              class="name"
              :to="`/p/${encodeURIComponent(g.name)}`"
              >{{ g.name }}<Icon name="chevron-right" :size="16"
            /></RouterLink>
            <span v-else class="name" :class="{ anon: !g.name }">{{ title(g) }}</span>
            <div class="meta">
              <template v-if="g.last">{{ t("speakers.last", { at: heard(g.last) }) }} · </template
              >{{ t("speakers.convs", { n: g.count }, g.count) }} · {{ mins(g.speech_s) }}
            </div>
          </div>
          <div v-if="g.name" class="kebab">
            <button
              class="btn-ghost"
              :aria-label="t('conv.more')"
              aria-haspopup="menu"
              :aria-expanded="menu && menuFor === g.id"
              @click="more(g, $event)"
            >
              <Icon name="more-horizontal" />
            </button>
            <div v-if="menu && menuFor === g.id" class="menu" role="menu">
              <button role="menuitem" :disabled="card(g.id).busy.value" @click="halves(g)">
                <Icon name="users" :size="18" />{{ t("speakers.split") }}
              </button>
            </div>
          </div>
        </div>
        <div class="samples">
          <button
            v-for="v in g.samples"
            :key="voiceId(v)"
            class="clip"
            :class="{ playing: player.playing === voiceId(v) }"
            :disabled="player.failed.has(voiceId(v))"
            @click="listen(v)"
          >
            <Icon :name="player.playing === voiceId(v) ? 'square' : 'play'" :size="14" />
            <span>{{ v.clip_text || day(v.start) }}</span>
          </button>
        </div>
        <p v-if="g.co.length" class="meta">
          {{ t("speakers.co", { names: g.co.map(speakerLabel).join(t("common.list")) }) }}
        </p>
        <p v-if="g.called.some((x) => x.name !== g.name)" class="meta called">
          {{ g.name ? t("speakers.alsoCalled") : t("speakers.called")
          }}<template v-for="x in g.called.filter((x) => x.name !== g.name)" :key="x.name"
            >{{ t("speakers.calledIn", { name: x.name, n: x.n }) }}
            <button
              v-if="!g.name"
              class="chip"
              :disabled="card(g.id).busy.value"
              @click="name(g, x.name, title(g))"
            >
              {{ t("speakers.nameAs", { name: x.name }) }}
            </button>
            <button
              v-else-if="g.name !== '我'"
              class="chip"
              :disabled="card(g.id).busy.value"
              @click="addAlias(g, x.name, g.name)"
            >
              {{ t("speakers.asAlias") }}
            </button></template
          >
        </p>
        <ul class="points">
          <li v-for="v in g.samples.filter((v) => v.points.length).slice(0, 2)" :key="voiceId(v)">
            {{ v.points[0] }} <span class="meta">— {{ v.title }}</span>
          </li>
        </ul>

        <div class="actions">
          <template v-if="editing === g.id">
            <input
              v-model="newName"
              :placeholder="t('speakers.namePlaceholder')"
              list="names"
              @keyup.enter="name(g, newName, title(g))"
            />
            <button :disabled="card(g.id).busy.value" @click="name(g, newName, title(g))">
              {{ t("speakers.save") }}
            </button>
            <button class="btn-ghost" @click="cancel">{{ t("speakers.cancel") }}</button>
          </template>
          <template v-else>
            <button :class="{ 'btn-ghost': g.name }" @click="edit(g.id)">
              {{ g.name ? t("speakers.rename") : t("speakers.giveName") }}
            </button>
            <button v-if="!g.name" :disabled="card(g.id).busy.value" @click="name(g, '我', title(g))">
              {{ t("speaker.isMe") }}
            </button>
          </template>
          <button class="btn-ghost" @click="expand(g)">
            {{ open === g.id ? t("speakers.collapse") : t("speakers.list", { n: g.count }) }}
          </button>
          <span v-if="card(g.id).busy.value" class="meta">{{ t("speakers.saving") }}</span>
        </div>
        <p v-if="card(g.id).error.value" class="error" role="alert" :title="card(g.id).detail.value">
          {{ card(g.id).error.value }}
        </p>
        <div v-if="sheet && sheet.id === g.id" class="mixed">
          <strong>{{ t("speakers.mixed") }}</strong>
          <p class="meta">{{ t("speakers.mixedWhy") }}</p>
          <div v-for="(h, i) in sheet.halves" :key="i" class="half">
            <div>
              {{
                t(
                  i ? "speakers.halfB" : "speakers.halfA",
                  { convs: h.count, len: mins(h.speech_s) },
                  h.count,
                )
              }}
            </div>
            <div class="samples">
              <button
                v-for="v in h.samples"
                :key="voiceId(v)"
                class="clip"
                :class="{ playing: player.playing === voiceId(v) }"
                :disabled="player.failed.has(voiceId(v))"
                @click="listen(v)"
              >
                <Icon :name="player.playing === voiceId(v) ? 'square' : 'play'" :size="14" />
                <span>{{ v.clip_text || day(v.start) }}</span>
              </button>
            </div>
            <button
              v-if="sheet.to"
              :disabled="card(g.id).busy.value"
              @click="name(g, sheet.to, sheet.what!, sheet.suggestion, { voices: h.voices })"
            >
              {{ t("speakers.nameHalf") }}
            </button>
            <button v-else-if="g.name" :disabled="card(g.id).busy.value" @click="split(g, h)">
              {{ t("speakers.notHalf", { name: speakerLabel(g.name) }) }}
            </button>
          </div>
          <div class="actions">
            <button
              v-if="sheet.to"
              :disabled="card(g.id).busy.value"
              @click="name(g, sheet.to, sheet.what!, sheet.suggestion, { all: true })"
            >
              {{ t("speakers.nameAll") }}
            </button>
            <button
              v-if="sheet.to && !g.name && !sheet.suggestion"
              :disabled="card(g.id).busy.value"
              @click="split(g, sheet.halves[1])"
            >
              {{ t("speakers.split") }}
            </button>
            <button class="btn-ghost" @click="sheet = null">{{ t("speakers.cancel") }}</button>
          </div>
        </div>
        <div v-if="editing === g.id && names.length" class="chips">
          <button
            v-for="n in names.slice(0, 12)"
            :key="n"
            class="chip"
            :disabled="card(g.id).busy.value"
            @click="name(g, n, title(g))"
          >
            {{ n }}
          </button>
        </div>

        <div v-for="(s, i) in g.suggested" :key="i" class="suggest">
          <span
            >{{
              t(
                "speakers.maybe",
                { name: speakerLabel(g.name), like: likeness(s.score), n: s.count },
                s.count,
              )
            }}<template v-if="s.called.length">{{
              t("speakers.calledAs", { names: quoted(s.called.map((x) => x.name)) })
            }}</template></span
          >
          <button v-for="v in s.samples" :key="voiceId(v)" class="clip" @click="listen(v)">
            <Icon :name="player.playing === voiceId(v) ? 'square' : 'play'" :size="14" />
            <span>{{ v.clip_text || day(v.start) }}</span>
          </button>
          <button
            :disabled="card(g.id).busy.value"
            @click="name(g, g.name!, t('speakers.nConvs', { n: s.count }, s.count), s.id)"
          >
            {{ t("speakers.isThem", { name: speakerLabel(g.name) }) }}
          </button>
        </div>

        <table v-if="open === g.id" class="members">
          <tr v-if="!members.length">
            <td class="meta">{{ t("speakers.loading") }}</td>
          </tr>
          <tr v-for="v in members" :key="voiceId(v)">
            <td class="when">
              <RouterLink :to="`/c/${v.conv}`">{{ day(v.start) }}</RouterLink>
            </td>
            <td>
              <button
                class="mini"
                :title="player.playing === voiceId(v) ? t('speakers.stop') : t('speakers.play')"
                @click="listen(v)"
              >
                <Icon :name="player.playing === voiceId(v) ? 'square' : 'play'" :size="14" />
              </button>
              {{ v.title || t("speakers.untitled") }}
              <span class="meta"
                >·
                {{
                  t("speakers.member", {
                    label: speakerLabel(v.label),
                    n: v.n,
                    with:
                      v.with.map((w) => (w === "我" ? speakerLabel(w) : w)).join(t("common.list")) ||
                      t("speakers.nobody"),
                  })
                }}</span
              >
            </td>
            <td>
              <button class="btn-ghost mini" :disabled="card(g.id).busy.value" @click="detach(g, v)">
                {{ t("speakers.notThem") }}
              </button>
            </td>
          </tr>
        </table>
      </section>
    </template>
    <button
      v-if="othersOpen && others.length > shownOthers"
      class="more"
      @click="shownOthers += 30"
    >
      {{ t("speakers.more") }}
    </button>
    <datalist id="names"><option v-for="n in names" :key="n" :value="n" /></datalist>
  </main>
</template>

<style scoped>
h1 {
  margin: var(--space-4) 0 var(--space-3);
}
.meta {
  color: var(--muted);
  font-size: var(--fs-sm);
}
.tabs {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.head {
  display: flex;
  gap: var(--space-3);
  align-items: center;
}
.who {
  min-width: 0;
  line-height: 1.4;
}
.name {
  display: inline-flex;
  align-items: center;
  font-weight: 600;
}
.name.anon {
  color: var(--muted);
}
.who .meta {
  font-size: var(--fs-xs);
}
.samples,
.chips,
.actions,
.suggest {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  align-items: center;
}
.clip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.clip span {
  overflow: hidden;
  text-overflow: ellipsis;
}
.clip.playing {
  border-color: var(--accent);
  color: var(--accent);
}
.points {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: var(--fs-sm);
}
.suggest {
  background: var(--bg);
  border-radius: var(--radius-sm);
  padding: 8px;
  font-size: var(--fs-sm);
  color: var(--fg-2);
}
.actions .btn-ghost {
  color: var(--muted);
}
.members {
  width: 100%;
  margin-top: 10px;
  font-size: var(--fs-sm);
  border-collapse: collapse;
}
.members td {
  padding: 4px 4px;
  border-top: 1px solid var(--line);
  vertical-align: top;
}
.members .when {
  white-space: nowrap;
}
.mini {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  vertical-align: middle;
  min-height: 32px;
  min-width: 32px;
  padding: 0 6px;
}
.flash {
  color: var(--accent);
  font-size: var(--fs-sm);
}
.error {
  color: var(--err);
  font-size: var(--fs-sm);
}
.more {
  width: 100%;
}
.kebab {
  position: relative;
  margin-left: auto;
}
.kebab > .btn-ghost {
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
  min-width: 160px;
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
.mixed {
  margin-top: 10px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  font-size: var(--fs-sm);
}
.mixed > .meta {
  margin: 2px 0 0;
}
.half {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
.half > button {
  margin-top: 8px;
}
.fold {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  width: 100%;
  min-height: 44px;
  margin: var(--space-2) 0 var(--space-3);
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: none;
  color: var(--fg-2);
  text-align: left;
}
.fold .meta {
  display: block;
  font-size: var(--fs-xs);
}
input {
  font-size: var(--fs-sm);
  padding: 4px 8px;
  min-height: 36px;
}
/* inside the app the native bar already says 人物. Scoped CSS marks only the last part (h1), so .in-app stays the
   html's class; :global(.in-app) would make the whole rule `.in-app {display: none}` and hide the page */
.in-app main > h1 {
  display: none;
}
</style>
