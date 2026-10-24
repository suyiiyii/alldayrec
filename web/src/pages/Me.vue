<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Avatar from "../components/Avatar.vue";
import DeviceCard from "../components/DeviceCard.vue";
import Icon, { type IconName } from "../components/Icon.vue";
import { lang, setLang, t, type Lang } from "../i18n";
import { engines, get, hhmm, post, type Due, type Status } from "../lib/api";
import { dayLabel } from "../lib/calendar";
import { useSocket } from "../lib/socket";
import { speakerLabel } from "../lib/speakers";
import { loadStars, stars } from "../lib/stars";
import { open } from "../lib/todos";
import { toast } from "../lib/toast";

// 我的: each device as a status card (the details on its own page), the starred sentences (a long press on one
// stars it; each opens its conversation's 原文 there), then the pages that are not a daily read
const status = ref<Status | null>(null);
const due = ref<Due[] | null>(null);
const load = async () => (status.value = await get<Status>("/api/status"));
onMounted(load);
onMounted(async () => (due.value = await get<Due[]>("/api/todos/due").catch(() => null)));
useSocket((m) => m.type === "status" && (status.value = m), load);
const STARS = 3; // the newest; the rest folded
const allStars = ref(false);
onMounted(() => void loadStars().catch(() => {}));

const LINKS = computed<{ to: string; icon: IconName; title: string; sub: string }[]>(() => [
  {
    to: "/todos",
    icon: "check",
    title: t("me.todos"),
    sub: !due.value
      ? t("me.todosSub")
      : open(due.value).length
        ? t("me.todosDue", open(due.value).length)
        : t("me.todosNone"),
  },
  { to: "/connect", icon: "smartphone", title: t("me.connect"), sub: t("me.connectSub") },
  { to: "/progress", icon: "activity", title: t("me.progress"), sub: t("me.progressSub") },
  { to: "/connect#ai", icon: "sparkles", title: t("me.ai"), sub: t("me.aiSub") },
  { to: "/record", icon: "mic", title: t("me.record"), sub: t("me.recordSub") },
]);

// the page's language, kept in this browser; the server's too: the models write summaries, recaps and answers in it
const LANGS: [Lang, string][] = [
  ["zh", "中文"],
  ["en", "English"],
];
function pick(l: Lang) {
  localStorage.lang = l;
  setLang(l);
  post("/api/lang", { lang: l }, "PUT").catch((e: Error) => toast(e.message));
}
</script>

<template>
  <AppHeader />
  <main>
    <h1>{{ t("title.me") }}</h1>
    <DeviceCard v-for="d in status?.devices ?? []" :key="d.device" :d="d" />
    <section class="card stars">
      <h2><Icon name="star" :size="18" />{{ t("me.stars") }}</h2>
      <p v-if="!stars.length" class="muted">{{ t("me.starsEmpty") }}</p>
      <RouterLink
        v-for="s in allStars ? stars : stars.slice(0, STARS)"
        :key="s.id"
        class="star"
        :to="{ path: `/c/${s.conv}`, query: { at: s.start } }"
      >
        <Avatar :name="s.speaker" />
        <span class="text"
          ><span class="said">{{ s.text }}</span
          ><span class="muted sub"
            >{{ dayLabel(s.start.slice(0, 10)) }} {{ hhmm(s.start) }} · {{ speakerLabel(s.speaker)
            }}{{ s.title ? " · " + s.title : "" }}</span
          ></span
        >
      </RouterLink>
      <button v-if="stars.length > STARS" class="btn-ghost all" @click="allStars = !allStars">
        {{ allStars ? t("me.starsLess") : t("me.starsAll", { n: stars.length }) }}
      </button>
    </section>
    <div class="card list">
      <RouterLink v-for="l in LINKS" :key="l.to" class="link" :to="l.to">
        <Icon :name="l.icon" :size="22" />
        <span class="text"
          ><b>{{ l.title }}</b
          ><span class="muted">{{ l.sub }}</span></span
        >
        <Icon name="chevron-right" :size="18" class="go" />
      </RouterLink>
      <div class="link">
        <Icon name="globe" :size="22" />
        <span class="text"
          ><b>{{ t("me.lang") }}</b
          ><span class="muted">{{ t("me.langSub") }}</span></span
        >
        <span class="langs">
          <button
            v-for="[l, name] in LANGS"
            :key="l"
            class="chip"
            :class="{ on: lang() === l }"
            @click="pick(l)"
          >
            {{ name }}
          </button>
        </span>
      </div>
      <div class="link">
        <Icon name="alert-circle" :size="22" />
        <span class="text"
          ><b>{{ t("me.about") }}</b
          ><span class="muted"
            >alldayrec · {{ t("me.version", { commit: status?.commit || "—" })
            }}{{ status?.engine ? " · " + t("me.engine", { engine: engines(status) }) : "" }}</span
          ></span
        >
      </div>
    </div>
  </main>
</template>

<style scoped>
h1 {
  margin: var(--space-3) 0 var(--space-4);
}
/* one card, a row each, a line between */
.list {
  padding: 0 var(--space-4);
}
.link {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) 0;
}
.link + .link {
  border-top: 1px solid var(--line);
}
a.link {
  color: var(--fg);
}
.link > .icon:first-child {
  color: var(--accent);
}
.text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.text .muted {
  font-size: var(--fs-sm);
}
.go {
  color: var(--muted);
}
.langs {
  display: flex;
  gap: var(--space-2);
}
.stars h2 {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0 0 var(--space-1);
  font-size: var(--fs-md);
}
.stars h2 .icon {
  color: var(--warn);
  fill: currentColor;
}
.stars > p {
  margin: 0;
  font-size: var(--fs-sm);
}
.star {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  color: var(--fg);
}
.star + .star {
  border-top: 1px solid var(--line);
}
.star .avatar {
  margin-top: 2px;
}
.said {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.text .sub {
  font-size: var(--fs-xs);
}
.all {
  width: 100%;
  margin-top: var(--space-1);
}
</style>
