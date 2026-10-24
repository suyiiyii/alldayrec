<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import DeviceCard from "../components/DeviceCard.vue";
import Icon, { type IconName } from "../components/Icon.vue";
import { lang, setLang, t, type Lang } from "../i18n";
import { get, post, type Due, type Status } from "../lib/api";
import { useSocket } from "../lib/socket";
import { open } from "../lib/todos";
import { toast } from "../lib/toast";

// 我的: each device as a status card (the details on its own page), then the pages that are not a daily read
const status = ref<Status | null>(null);
const due = ref<Due[] | null>(null);
const load = async () => (status.value = await get<Status>("/api/status"));
onMounted(load);
onMounted(async () => (due.value = await get<Due[]>("/api/todos/due").catch(() => null)));
useSocket((m) => m.type === "status" && (status.value = m), load);

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
            }}{{ status?.engine ? " · " + t("me.engine", { engine: status.engine }) : "" }}</span
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
</style>
