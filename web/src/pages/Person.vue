<script setup lang="ts">
import { ref, watch } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Avatar from "../components/Avatar.vue";
import Icon from "../components/Icon.vue";
import { t } from "../i18n";
import { errorTitle, get, hhmm, post, type Person, type Profile } from "../lib/api";
import { shiftDay, today } from "../lib/calendar";
import { speakerLabel } from "../lib/speakers";

const props = defineProps<{ name: string }>();
const p = ref<Person | null>(null),
  missing = ref(false),
  shown = ref(20),
  allMine = ref(false);

watch(
  () => props.name,
  async () => {
    p.value = null;
    missing.value = false;
    try {
      p.value = await get<Person>(`/api/person/${encodeURIComponent(props.name)}`);
      document.title = props.name;
    } catch {
      missing.value = true;
    }
  },
  { immediate: true },
);
const day = (iso: string) => iso.slice(0, 10);
const heard = (iso: string) =>
  iso.startsWith(today())
    ? t("common.todayAt", { t: hhmm(iso) })
    : iso.startsWith(shiftDay(today(), -1))
      ? t("common.yesterdayAt", { t: hhmm(iso) })
      : iso.slice(5, 10);

// the profile: made on a click, then kept up to date as conversations with them close
const making = ref(false),
  profileError = ref(""),
  profileTitle = ref("");
async function makeProfile() {
  making.value = true;
  profileError.value = "";
  try {
    p.value!.profile = await post<Profile>(`/api/person/${encodeURIComponent(props.name)}/profile`);
    p.value!.profile_newer = 0;
  } catch (e) {
    profileError.value = (e as Error).message;
    profileTitle.value = errorTitle(e);
  } finally {
    making.value = false;
  }
}
// title: person.section.<k>, with {name}
const sections = ["facts", "recent", "mentions", "open"] as const;

// other ways this person is called (A哥 for A): a hint with one of them suggests this name
const newAlias = ref(""),
  aliasError = ref(""),
  aliasTitle = ref("");
async function setAlias(alias: string, name: string) {
  aliasError.value = "";
  try {
    const all = await post<Record<string, string>>("/api/aliases", { alias, name });
    p.value!.aliases = Object.keys(all).filter((a) => all[a] === props.name);
    newAlias.value = "";
  } catch (e) {
    aliasError.value = (e as Error).message;
    aliasTitle.value = errorTitle(e);
  }
}
const link = (id: string, at: string | null) =>
  `/c/${id}` + (at ? `#${encodeURIComponent(at)}` : "");
// the online demo's hash history (src/demo.ts) takes a plain #mine for a route: there the count scrolls by itself
function anchor(e: MouseEvent, id: string) {
  if (import.meta.env.MODE !== "demo") return;
  e.preventDefault();
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
}
</script>

<template>
  <AppHeader />
  <main>
    <RouterLink class="back" to="/speakers"
      ><Icon name="chevron-left" :size="18" />{{ t("title.people") }}</RouterLink
    >
    <div v-if="missing" class="empty">{{ t("person.missing", { name }) }}</div>
    <template v-else-if="p">
      <!-- a card for the person: who, when last heard, and what is open between us (我答应 TA 的 first) -->
      <section class="card head">
        <div class="top">
          <Avatar :name="p.name" :size="48" />
          <div class="who">
            <h1>{{ p.name }}</h1>
            <div class="muted">
              {{
                t(
                  "person.meta",
                  { at: heard(p.last), n: p.count, since: day(p.first).slice(5) },
                  p.count,
                )
              }}
            </div>
          </div>
        </div>
        <div class="stats">
          <a
            class="stat"
            :href="p.mine.length ? '#mine' : undefined"
            @click="anchor($event, 'mine')"
            ><b>{{ p.mine.length }}</b
            >{{ t("person.mine") }}</a
          >
          <a
            class="stat"
            :href="p.theirs.length ? '#theirs' : undefined"
            @click="anchor($event, 'theirs')"
            ><b>{{ p.theirs.length }}</b
            >{{ t("person.theirs") }}</a
          >
        </div>
        <div class="aliases muted">
          {{ t("person.aliases")
          }}<span v-for="a in p.aliases" :key="a" class="tag"
            >{{ a }}
            <button class="x" :title="t('person.removeAlias')" @click="setAlias(a, '')">
              <Icon name="x" :size="14" /></button></span
          ><input
            v-model="newAlias"
            :placeholder="t('person.addAlias')"
            @keydown.enter="newAlias.trim() && setAlias(newAlias.trim(), p.name)"
          /><span class="bad" :title="aliasTitle">{{ aliasError }}</span>
        </div>
        <div v-if="p.with.length" class="muted">
          {{ t("person.with")
          }}<template v-for="(w, i) in p.with" :key="w"
            >{{ i ? t("common.list") : ""
            }}<RouterLink v-if="w !== '我'" :to="`/p/${encodeURIComponent(w)}`">{{ w }}</RouterLink
            ><template v-else>{{ speakerLabel(w) }}</template></template
          >
        </div>
        <div v-if="p.tags.length" class="tags">
          <RouterLink
            v-for="tag in p.tags"
            :key="tag"
            class="tag"
            :to="{ path: '/', query: { tag } }"
            >{{ tag }}</RouterLink
          >
        </div>
      </section>

      <section v-if="p.mine.length" id="mine" class="card">
        <h2>{{ t("person.mineTitle", { name: p.name }) }}</h2>
        <ul class="items">
          <li v-for="(x, i) in allMine ? p.mine : p.mine.slice(0, 8)" :key="i">
            {{ x.text
            }}<span v-if="x.due" class="muted">{{ t("person.due", { due: x.due }) }}</span>
            <RouterLink class="from" :to="link(x.conversation, x.at)">{{
              x.title || t("title.conversation")
            }}</RouterLink>
          </li>
        </ul>
        <button v-if="p.mine.length > 8 && !allMine" class="btn-ghost" @click="allMine = true">
          {{ t("person.allMine", { n: p.mine.length }) }}
        </button>
      </section>
      <section v-if="p.theirs.length" id="theirs" class="card">
        <h2>{{ t("person.theirsTitle", { name: p.name }) }}</h2>
        <ul class="items">
          <li v-for="(x, i) in p.theirs" :key="i">
            {{ x.text
            }}<span v-if="x.due" class="muted">{{ t("person.due", { due: x.due }) }}</span>
            <RouterLink class="from" :to="link(x.conversation, x.at)">{{
              x.title || t("title.conversation")
            }}</RouterLink>
          </li>
        </ul>
      </section>

      <section class="profile">
        <template v-if="p.profile">
          <p v-if="p.profile.who" class="whois">{{ p.profile.who }}</p>
          <template v-for="k in sections" :key="k">
            <template v-if="p.profile[k].length">
              <h3>{{ t(`person.section.${k}`, { name: p.name }) }}</h3>
              <ul class="items">
                <li v-for="(x, i) in p.profile[k]" :key="i">
                  {{ x.text }}
                  <RouterLink
                    v-for="s in x.src"
                    :key="s.id"
                    class="src"
                    :title="s.title ?? ''"
                    :to="link(s.conv, s.at)"
                    >{{ s.start.slice(5, 10) }}</RouterLink
                  >
                </li>
              </ul>
            </template>
          </template>
          <div class="muted foot">
            <span :title="p.profile.model ? t('person.madeBy', { model: p.profile.model }) : ''">{{
              t("person.profileFoot", {
                convs: t(
                  "person.nConvs",
                  { n: p.profile.inputs.conversations },
                  p.profile.inputs.conversations,
                ),
                mentions: t(
                  "person.nMentions",
                  { n: p.profile.inputs.mentions },
                  p.profile.inputs.mentions,
                ),
                at: p.profile.at.slice(5, 16).replace("T", " "),
              })
            }}</span>
            <template v-if="p.profile_newer">{{
              t("person.newer", { n: p.profile_newer }, p.profile_newer)
            }}</template>
            <button :disabled="making" @click="makeProfile">
              {{ making ? t("person.making") : t("person.update") }}
            </button>
          </div>
        </template>
        <button v-else :disabled="making" @click="makeProfile">
          {{ making ? t("person.makingLong") : t("person.make") }}
        </button>
        <span class="bad" :title="profileTitle">{{ profileError }}</span>
      </section>

      <h3>{{ t("person.conversations") }}</h3>
      <section v-for="c in p.conversations.slice(0, shown)" :key="c.id" class="conv">
        <RouterLink :to="`/c/${c.id}`" class="title">{{
          c.title || t("person.untitled")
        }}</RouterLink>
        <span class="muted">
          · {{ day(c.start) }} {{ hhmm(c.start) }} ·
          {{ c.participants.map(speakerLabel).join(t("common.list")) }}</span
        >
        <ul v-if="c.points.length" class="points">
          <li v-for="x in c.points" :key="x">{{ x }}</li>
        </ul>
        <p v-else-if="c.summary" class="summary">{{ c.summary }}</p>
      </section>
      <button v-if="p.conversations.length > shown" class="more" @click="shown += 20">
        {{ t("person.more") }}
      </button>
    </template>
  </main>
</template>

<style scoped>
.top {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.who {
  min-width: 0;
}
h1 {
  margin: 0;
}
.head > * + * {
  margin-top: var(--space-3);
}
.stats {
  display: flex;
  gap: var(--space-2);
}
.stat {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--fg-2);
  font-size: var(--fs-sm);
}
.stat:first-child {
  background: var(--accent-soft);
  color: var(--accent);
}
.stat b {
  font-size: var(--fs-xl);
  line-height: 1.2;
}
.card h2 {
  margin: 0 0 var(--space-2);
  font-size: var(--fs-md);
}
#mine,
#theirs {
  scroll-margin-top: 64px; /* under the sticky header */
}
h3 {
  margin: 20px 0 6px;
}
.muted {
  color: var(--muted);
  font-size: var(--fs-sm);
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.items {
  padding-left: 18px;
  margin: 0;
}
.items li {
  margin: 4px 0;
}
.items .muted {
  margin-left: 6px;
}
/* the conversation a todo came from: its own small line under the todo */
.items .from {
  display: block;
  font-size: var(--fs-xs);
}
.conv {
  border-top: 1px solid var(--line);
  padding: 10px 0;
}
.conv .title {
  font-weight: 600;
}
.points {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: var(--fs-sm);
}
.summary {
  margin: 4px 0 0;
  font-size: var(--fs-sm);
  color: var(--muted);
}
.more {
  width: 100%;
}
.profile {
  margin-top: 12px;
}
.whois {
  background: var(--surface);
  box-shadow: var(--shadow);
  border-radius: var(--radius);
  padding: 10px 14px;
  margin: 0;
}
.src {
  font-size: var(--fs-xs);
  margin-left: 6px;
  font-variant-numeric: tabular-nums;
}
.foot button {
  margin-left: 8px;
}
.aliases {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.aliases input {
  font: inherit;
  padding: 2px 8px;
  width: 9em;
}
.back {
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  margin-left: -4px;
}
.aliases .x {
  display: inline-flex;
  align-items: center;
  border: 0;
  background: none;
  min-height: 28px;
  padding: 0 2px;
  color: var(--muted);
  cursor: pointer;
}
</style>
