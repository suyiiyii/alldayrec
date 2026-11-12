import { watch } from "vue";
import { createRouter, createWebHashHistory, createWebHistory } from "vue-router";
import { i18n, t } from "./i18n";
import { toast } from "./lib/toast";

export const router = createRouter({
  // the online demo (src/demo.ts) is static files with no fallback to index.html: its paths go after a #
  history: import.meta.env.MODE === "demo" ? createWebHashHistory() : createWebHistory(),
  routes: [
    // the old /conversations shows it too (an alias, not a redirect: the 1.5.0 app's 对话 tab loads that path, and a
    // redirect would move its WebView to 时间线's path, which that app answers by reloading both tabs, again and again)
    {
      path: "/",
      alias: "/conversations",
      component: () => import("./pages/Timeline.vue"),
      meta: { title: "title.timeline" },
    },
    { path: "/live", component: () => import("./pages/Live.vue"), meta: { title: "title.live" } },
    { path: "/me", component: () => import("./pages/Me.vue"), meta: { title: "title.me" } },
    {
      path: "/todos",
      component: () => import("./pages/Todos.vue"),
      meta: { title: "title.todos" },
    },
    {
      path: "/c/:id",
      component: () => import("./pages/Conversation.vue"),
      props: true,
      meta: { title: "title.conversation" },
    },
    {
      path: "/calendar",
      component: () => import("./pages/Calendar.vue"),
      meta: { title: "title.calendar" },
    },
    {
      path: "/progress",
      component: () => import("./pages/Progress.vue"),
      meta: { title: "title.progress" },
    },
    {
      path: "/speakers",
      component: () => import("./pages/Speakers.vue"),
      meta: { title: "title.people" },
    },
    {
      path: "/p/:name",
      component: () => import("./pages/Person.vue"),
      props: true,
      meta: { title: "title.person" },
    },
    {
      path: "/device/:name",
      component: () => import("./pages/Device.vue"),
      props: true,
      meta: { title: "title.device" },
    },
    {
      path: "/import",
      component: () => import("./pages/Import.vue"),
      meta: { title: "title.import" },
    },
    {
      path: "/record",
      component: () => import("./pages/Record.vue"),
      meta: { title: "title.record" },
    },
    {
      path: "/connect",
      component: () => import("./pages/Connect.vue"),
      meta: { title: "title.connect" },
    },
    { path: "/terms", component: () => import("./pages/Terms.vue"), meta: { title: "title.terms" } },
  ],
});
// meta.title: the page's title in locales/*.json, again when the language changes
const title = () => {
  const k = router.currentRoute.value.meta.title as string | undefined;
  document.title = k ? t(k) : "AllDayRec";
};
router.afterEach(title);
// A page's chunk that a deploy since this page loaded removed (each build empties dist): a 404, and the tap did
// nothing (#327). Load the page anew at its target, once a target: a mark in this tab stops a loop when the chunk is
// missing for good, and a page that loads clears it for the next deploy. No storage: no mark, no reload.
const STALE =
  /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i;
router.onError((err, to) => {
  if (!STALE.test(String(err?.message ?? err))) return console.error(err); // as vue-router does without a handler
  const href = router.resolve(to.fullPath).href;
  try {
    if (sessionStorage.getItem("chunkReload") === href) return;
    sessionStorage.setItem("chunkReload", href);
  } catch {
    return;
  }
  location.assign(href);
});
router.afterEach((_to, _from, failure) => {
  if (failure) return;
  try {
    sessionStorage.removeItem("chunkReload");
  } catch {}
});
// the online demo has no recorder, microphone, phone or AI service to reach (src/demo.ts): those pages say so; opened
// directly, the timeline instead
if (import.meta.env.MODE === "demo")
  router.beforeEach((to, from) => {
    if (to.path !== "/record" && to.path !== "/connect") return;
    toast(t("err.code.demo"));
    return from.matched.length ? false : "/";
  });
watch(i18n.global.locale, title);
