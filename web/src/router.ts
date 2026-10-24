import { createRouter, createWebHistory } from "vue-router";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // the old /conversations shows it too (an alias, not a redirect: the 1.5.0 app's 对话 tab loads that path, and a
    // redirect would move its WebView to 时间线's path, which that app answers by reloading both tabs, again and again)
    {
      path: "/",
      alias: "/conversations",
      component: () => import("./pages/Timeline.vue"),
      meta: { title: "时间线" },
    },
    { path: "/live", component: () => import("./pages/Live.vue"), meta: { title: "实时" } },
    { path: "/me", component: () => import("./pages/Me.vue"), meta: { title: "我的" } },
    { path: "/todos", component: () => import("./pages/Todos.vue"), meta: { title: "待办" } },
    {
      path: "/c/:id",
      component: () => import("./pages/Conversation.vue"),
      props: true,
      meta: { title: "对话" },
    },
    {
      path: "/calendar",
      component: () => import("./pages/Calendar.vue"),
      meta: { title: "日历" },
    },
    {
      path: "/progress",
      component: () => import("./pages/Progress.vue"),
      meta: { title: "处理进度" },
    },
    {
      path: "/speakers",
      component: () => import("./pages/Speakers.vue"),
      meta: { title: "人物" },
    },
    {
      path: "/p/:name",
      component: () => import("./pages/Person.vue"),
      props: true,
      meta: { title: "人物" },
    },
    {
      path: "/device/:name",
      component: () => import("./pages/Device.vue"),
      props: true,
      meta: { title: "设备日志" },
    },
    {
      path: "/record",
      component: () => import("./pages/Record.vue"),
      meta: { title: "alldayrec 录音" },
    },
    {
      path: "/connect",
      component: () => import("./pages/Connect.vue"),
      meta: { title: "连接手机" },
    },
  ],
});
router.afterEach((to) => {
  document.title = (to.meta.title as string | undefined) ?? "alldayrec";
});
