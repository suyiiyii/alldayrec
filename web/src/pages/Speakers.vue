<script setup lang="ts">
import { computed, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Avatar from "../components/Avatar.vue";
import Icon from "../components/Icon.vue";
import Skeleton from "../components/Skeleton.vue";
import { errorTitle, get, hhmm, post, type Speaker, type Voice } from "../lib/api";
import { play, player } from "../lib/audio";
import { shiftDay, today } from "../lib/calendar";
import { useMutation } from "../lib/mutation";
import { likeness, speakerLabel } from "../lib/speakers";

const groups = ref<Speaker[]>([]),
  names = ref<string[]>([]),
  loaded = ref(false),
  filter = ref<"unnamed" | "all">("all"),
  shown = ref(30),
  open = ref(""),
  members = ref<Voice[]>([]),
  editing = ref(""),
  newName = ref(""),
  flash = ref(""),
  errorAt = ref(""), // the card whose name box the error is about; "" puts it at the top
  failed = ref<{ msg: string; title: string } | null>(null); // the list itself did not load
const { busy, error, detail, run } = useMutation();

async function load() {
  try {
    [groups.value, names.value] = await Promise.all([
      get<Speaker[]>("/api/speakers"),
      get<string[]>("/api/names"),
    ]);
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

const mins = (s: number) => (s < 60 ? `${Math.round(s)} 秒` : `${Math.round(s / 60)} 分钟`);
const day = (iso: string) => `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
const heard = (iso: string) =>
  iso.startsWith(today())
    ? `今天 ${hhmm(iso)}`
    : iso.startsWith(shiftDay(today(), -1))
      ? `昨天 ${hhmm(iso)}`
      : iso.slice(5, 10);
const voiceId = (v: Voice) => `${v.conv}|${v.label}`;
function listen(v: Voice) {
  const src = v.clip
    ? `/api/voice-clip/${v.clip}`
    : `/api/audio?${new URLSearchParams({ start: v.clip_start!, end: v.clip_end! })}`;
  play(voiceId(v), src);
}
const num = (g: Speaker) => groups.value.filter((x) => !x.name).indexOf(g) + 1;
const title = (g: Speaker) => g.name ?? `声音 ${num(g)}`;

function edit(id: string) {
  editing.value = id;
  newName.value = "";
  error.value = "";
}
function cancel() {
  editing.value = "";
  error.value = "";
}
async function expand(g: Speaker) {
  if (open.value === g.id) return (open.value = "");
  open.value = g.id;
  members.value = [];
  members.value = (await get<Speaker>(`/api/speakers/${encodeURIComponent(g.id)}`)).members ?? [];
}
async function name(g: Speaker, to: string, what: string, suggestion?: string) {
  to = to.trim();
  if (!to) return;
  errorAt.value = editing.value === g.id ? g.id : "";
  await run(async () => {
    flash.value = "";
    const r = await post<{ renamed: number }>("/api/speakers/name", {
      name: to,
      group: g.id,
      suggestion,
    });
    flash.value = `${what} → ${to}：改了 ${r.renamed} 段对话，摘要重新生成中`;
    editing.value = "";
    await load();
  });
}
async function addAlias(alias: string, name: string) {
  errorAt.value = "";
  await run(async () => {
    flash.value = "";
    await post("/api/aliases", { alias, name });
    flash.value = `已记下「${alias}」是 ${name} 的别名`;
    await load();
  });
}
async function detach(g: Speaker, v: Voice) {
  errorAt.value = "";
  await run(async () => {
    flash.value = "";
    await post("/api/speakers/detach", { conv: v.conv, label: v.label, group: g.name ?? g.id });
    flash.value = `已把 ${day(v.start)} 那段从「${title(g)}」移出`;
    members.value = members.value.filter((m) => m !== v);
    await load();
  });
}
</script>

<template>
  <AppHeader />
  <main>
    <h1>人物</h1>
    <div v-if="groups.length" class="tabs">
      <button class="chip" :class="{ on: filter === 'all' }" @click="filter = 'all'">
        全部 {{ groups.length }}
      </button>
      <button class="chip" :class="{ on: filter === 'unnamed' }" @click="filter = 'unnamed'">
        待命名 {{ unnamed }}
      </button>
    </div>
    <p v-if="flash" class="flash">{{ flash }}</p>
    <p v-if="error && !errorAt" class="error" role="alert" :title="detail">{{ error }}</p>
    <Skeleton v-if="!loaded" />
    <div v-else-if="failed" class="empty" :title="failed.title">
      <Icon name="alert-circle" :size="48" />{{ failed.msg }}
    </div>
    <div v-else-if="!groups.length" class="empty">
      <Icon name="users" :size="48" />
      <div>还没有认出说话人。录到对话以后，每个声音会列在这里，听一听再起名字</div>
      <RouterLink class="btn btn-primary" to="/connect">连接手机</RouterLink>
    </div>
    <p v-else-if="!list.length" class="empty">每个声音都有名字了</p>

    <section v-for="g in list.slice(0, shown)" :key="g.id" class="card">
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
            <template v-if="g.last">最近 {{ heard(g.last) }} · </template>{{ g.count }} 段对话 ·
            {{ mins(g.speech_s) }}
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
      <p v-if="g.co.length" class="meta">常一起出现：{{ g.co.join("、") }}</p>
      <p v-if="g.called.some((x) => x.name !== g.name)" class="meta called">
        {{ g.name ? "也被叫作" : "被叫作" }}：<template
          v-for="x in g.called.filter((x) => x.name !== g.name)"
          :key="x.name"
          >「{{ x.name }}」{{ x.convs.length }} 段
          <button v-if="!g.name" class="chip" :disabled="busy" @click="name(g, x.name, title(g))">
            起名 {{ x.name }}
          </button>
          <button
            v-else-if="g.name !== '我'"
            class="chip"
            :disabled="busy"
            @click="addAlias(x.name, g.name)"
          >
            记为别名
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
            placeholder="名字"
            list="names"
            @keyup.enter="name(g, newName, title(g))"
          />
          <button :disabled="busy" @click="name(g, newName, title(g))">保存</button>
          <button class="btn-ghost" @click="cancel">取消</button>
        </template>
        <button v-else :class="{ 'btn-ghost': g.name }" @click="edit(g.id)">
          {{ g.name ? "改名" : "起名" }}
        </button>
        <button class="btn-ghost" @click="expand(g)">
          {{ open === g.id ? "收起" : `对话列表（${g.count}）` }}
        </button>
      </div>
      <p v-if="error && errorAt === g.id" class="error" role="alert" :title="detail">
        {{ error }}
      </p>
      <div v-if="editing === g.id && names.length" class="chips">
        <button v-for="n in names.slice(0, 12)" :key="n" class="chip" @click="name(g, n, title(g))">
          {{ n }}
        </button>
      </div>

      <div v-for="(s, i) in g.suggested" :key="i" class="suggest">
        <span
          >可能也是 {{ g.name }}（声音{{ likeness(s.score) }}）：{{ s.count }} 段对话<template
            v-if="s.called.length"
            >，被叫作{{ s.called.map((x) => `「${x.name}」`).join("") }}</template
          ></span
        >
        <button v-for="v in s.samples" :key="voiceId(v)" class="clip" @click="listen(v)">
          <Icon :name="player.playing === voiceId(v) ? 'square' : 'play'" :size="14" />
          <span>{{ v.clip_text || day(v.start) }}</span>
        </button>
        <button :disabled="busy" @click="name(g, g.name!, `${s.count} 段`, s.id)">
          是 {{ g.name }}
        </button>
      </div>

      <table v-if="open === g.id" class="members">
        <tr v-if="!members.length">
          <td class="meta">加载中…</td>
        </tr>
        <tr v-for="v in members" :key="voiceId(v)">
          <td class="when">
            <RouterLink :to="`/c/${v.conv}`">{{ day(v.start) }}</RouterLink>
          </td>
          <td>
            <button
              class="mini"
              :title="player.playing === voiceId(v) ? '停止' : '播放'"
              @click="listen(v)"
            >
              <Icon :name="player.playing === voiceId(v) ? 'square' : 'play'" :size="14" />
            </button>
            {{ v.title || "（无标题）" }}
            <span class="meta"
              >· {{ speakerLabel(v.label) }} · {{ v.n }} 句 · 同场
              {{ v.with.join("、") || "无" }}</span
            >
          </td>
          <td>
            <button class="btn-ghost mini" :disabled="busy" @click="detach(g, v)">不是</button>
          </td>
        </tr>
      </table>
    </section>
    <button v-if="list.length > shown" class="more" @click="shown += 30">再显示 30 个</button>
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
input {
  font-size: var(--fs-sm);
  padding: 4px 8px;
  min-height: 36px;
}
/* inside the app the native bar already says 人物 */
:global(.in-app) main > h1 {
  display: none;
}
</style>
