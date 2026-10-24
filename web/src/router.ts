import { watch } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import { i18n, t } from "./i18n";

export const router = createRouter({
  history: createWebHistory(),
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
      path: "/record",
      component: () => import("./pages/Record.vue"),
      meta: { title: "title.record" },
    },
    {
      path: "/connect",
      component: () => import("./pages/Connect.vue"),
      meta: { title: "title.connect" },
    },
  ],
});
// meta.title: the page's title in locales/*.json, again when the language changes
const title = () => {
  const k = router.currentRoute.value.meta.title as string | undefined;
  document.title = k ? t(k) : "alldayrec";
};
router.afterEach(title);
watch(i18n.global.locale, title);
