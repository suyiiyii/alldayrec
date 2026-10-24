<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import DeviceCard from "../components/DeviceCard.vue";
import Icon, { type IconName } from "../components/Icon.vue";
import { get, type Due, type Status } from "../lib/api";
import { useSocket } from "../lib/socket";
import { open } from "../lib/todos";

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
    title: "待办",
    sub: !due.value
      ? "到期的待办，过期太久的一键清掉"
      : open(due.value).length
        ? `${open(due.value).length} 条到期未处理`
        : "没有到期的待办",
  },
  { to: "/connect", icon: "smartphone", title: "连接手机", sub: "用 App 扫码登录这台服务器" },
  {
    to: "/progress",
    icon: "activity",
    title: "处理进度",
    sub: "转写和摘要做到哪了，AI 调用和系统日志",
  },
  {
    to: "/connect#ai",
    icon: "sparkles",
    title: "接入 AI",
    sub: "让 Claude Code、Codex、Cursor 查这里的对话和待办",
  },
  {
    to: "/record",
    icon: "mic",
    title: "网页录音",
    sub: "没有 App 时，用浏览器的麦克风或蓝牙录音设备录",
  },
]);
</script>

<template>
  <AppHeader />
  <main>
    <h1>我的</h1>
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
        <Icon name="alert-circle" :size="22" />
        <span class="text"
          ><b>关于</b
          ><span class="muted"
            >alldayrec · 服务器版本 {{ status?.commit || "—"
            }}{{ status?.engine ? " · 识别引擎 " + status.engine : "" }}</span
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
</style>
