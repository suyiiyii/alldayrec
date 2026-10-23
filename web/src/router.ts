import { createRouter, createWebHistory } from "vue-router";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: () => import("./pages/Live.vue"), meta: { title: "alldayrec" } },
    {
      path: "/conversations",
      component: () => import("./pages/Conversations.vue"),
      meta: { title: "对话" },
    },
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
      meta: { title: "说话人" },
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
