<script setup lang="ts">
import { onMounted, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Icon, { type IconName } from "../components/Icon.vue";
import { card, caughtUp, CHARGE, get, type DeviceStatus, type Status } from "../lib/api";
import { useSocket } from "../lib/socket";

// 我的: each device as a status card (the details on its own page), then the pages that are not a daily read
const status = ref<Status | null>(null);
const load = async () => (status.value = await get<Status>("/api/status"));
onMounted(load);
useSocket((m) => m.type === "status" && (status.value = m), load);

// behind: its finals are short of its audio; offline: no audio in the last 2 min
const badge = (d: DeviceStatus) =>
  !caughtUp(d)
    ? { text: `落后 ${Math.max(1, Math.round(d.left_s / 60))} 分钟`, cls: "warn" }
    : d.recording
      ? { text: "已追上", cls: "ok" }
      : { text: "离线", cls: "off" };
// the card's free space when it records to it, else what is wrong with it
const cardTile = (d: DeviceStatus) =>
  !d.hw
    ? "—"
    : !d.hw.store && d.hw.total_kb
      ? `${((d.hw.free_kb ?? 0) / 1048576).toFixed(1)} GB`
      : card(d.hw);

const LINKS: { to: string; icon: IconName; title: string; sub: string }[] = [
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
];
</script>

<template>
  <AppHeader />
  <main>
    <h1>我的</h1>
    <RouterLink
      v-for="d in status?.devices ?? []"
      :key="d.device"
      class="card dev"
      :to="`/device/${encodeURIComponent(d.device)}`"
    >
      <div class="dev-top">
        <b>{{ d.device }}</b>
        <span class="badge" :class="badge(d).cls">{{ badge(d).text }}</span>
      </div>
      <div class="tiles">
        <div>
          <span class="v">{{ d.hw?.battery != null ? d.hw.battery + "%" : "—" }}</span>
          <span class="k">电量{{ d.hw?.charge ? " · " + CHARGE[d.hw.charge] : "" }}</span>
        </div>
        <div>
          <span class="v" :class="{ long: cardTile(d).length > 8, bad: d.hw?.store }">{{
            cardTile(d)
          }}</span>
          <span class="k">存储卡{{ !d.hw?.store && d.hw?.total_kb ? "剩余" : "" }}</span>
        </div>
        <div>
          <span class="v">{{ d.latency_s != null ? d.latency_s + " 秒" : "—" }}</span>
          <span class="k">延迟</span>
        </div>
      </div>
    </RouterLink>
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
.dev-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
/* big enough to read across the room: 已追上 green, 落后 amber, 离线 grey (a recorder switched off is normal) */
.badge {
  font-size: var(--fs-sm);
  font-weight: 600;
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill);
  white-space: nowrap;
}
.badge.ok {
  color: var(--ok);
  background: var(--ok-soft);
}
.badge.warn {
  color: var(--warn);
  background: var(--warn-soft);
}
.badge.off {
  color: var(--fg-2);
  background: var(--line);
}
.tiles {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.tiles > div {
  background: var(--bg);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.v {
  font-size: var(--fs-lg);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
  overflow-wrap: anywhere;
}
.v.long {
  font-size: var(--fs-sm);
}
.k {
  font-size: var(--fs-xs);
  color: var(--muted);
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
