<script setup lang="ts">
import QRCode from "qrcode";
import { onMounted, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import { get } from "../lib/api";

// The app's settings page scans this: server + token, so they don't need typing in by hand (manual entry stays).
// The token comes from the server, not from the cookie the browser holds (it's HttpOnly, page JS can't read it).
const qr = ref(""),
  error = ref(""),
  token = ref("");
onMounted(async () => {
  try {
    token.value = (await get<{ token: string }>("/api/app-setup")).token;
    const url = `liverecord://setup?server=${encodeURIComponent(location.origin)}&token=${encodeURIComponent(token.value)}`;
    qr.value = await QRCode.toDataURL(url, { width: 320, margin: 2 });
  } catch (e) {
    error.value = (e as Error).message;
  }
});

// 接入 AI: the agents that take a URL and a header (server mcp_server.py), with the same token as the app.
// Shown with the token hidden; 复制 always copies the real one.
const mcp = `${location.origin}/mcp`;
const json = (server: object) => JSON.stringify({ mcpServers: { "alldayrec": server } }, null, 2);
const CLIENTS: { name: string; where: string; text: (t: string) => string }[] = [
  {
    name: "Claude Code",
    where: "在终端里运行一次",
    text: (t) =>
      `claude mcp add --transport http alldayrec ${mcp} --header "Authorization: Bearer ${t}" --scope user`,
  },
  {
    name: "Codex",
    where: "加进 ~/.codex/config.toml",
    text: (t) =>
      `[mcp_servers.alldayrec]\nurl = "${mcp}"\nhttp_headers = { "Authorization" = "Bearer ${t}" }`,
  },
  {
    name: "Cursor",
    where: "加进 ~/.cursor/mcp.json",
    text: (t) => json({ url: mcp, headers: { Authorization: `Bearer ${t}` } }),
  },
  {
    name: "Cherry Studio",
    where: "设置 › MCP › 添加 › 从 JSON 导入",
    text: (t) =>
      json({ type: "streamableHttp", url: mcp, headers: { Authorization: `Bearer ${t}` } }),
  },
];
const shown = ref(false),
  copied = ref("");
async function copy(c: (typeof CLIENTS)[number]) {
  await navigator.clipboard.writeText(c.text(token.value));
  copied.value = c.name;
  setTimeout(() => copied.value === c.name && (copied.value = ""), 2000);
}
</script>

<template>
  <AppHeader />
  <main class="page">
    <p v-if="error" class="bad">二维码生成失败：{{ error }}</p>
    <template v-else>
      <div class="frame"><img v-if="qr" :src="qr" width="320" height="320" alt="登录二维码" /></div>
      <p class="muted">在 App 的「设置 › 扫码登录」里扫这个二维码，服务器地址和 Token 会自动填好</p>
    </template>

    <section v-if="token" class="ai">
      <h2>接入 AI</h2>
      <p>
        让 Claude Code、Codex、Cursor、Cherry Studio 这类 AI
        助手直接查这里的对话、摘要和待办，也能把待办标成完成或不对。
      </p>
      <p>
        地址是 <code>{{ mcp }}</code
        >，用的是和 App 一样的 Token。
      </p>
      <ul class="warn">
        <li>接入后，AI 查到的转写和摘要（包括别人说的话）会发给你自己选的 AI 厂商。</li>
        <li>
          用这个连接的会话里，别对联网和 Shell
          工具开自动批准：录音里的话可能被当成指令，借这些工具把内容发出去。
        </li>
        <li>Token 能读写这里的全部数据，只放在自己的电脑上。</li>
      </ul>
      <div class="bar">
        <button @click="shown = !shown">{{ shown ? "隐藏 Token" : "显示 Token" }}</button>
      </div>
      <div v-for="c in CLIENTS" :key="c.name" class="client">
        <div class="head">
          <b>{{ c.name }}</b>
          <span class="muted">{{ c.where }}</span>
          <button @click="copy(c)">{{ copied === c.name ? "已复制" : "复制" }}</button>
        </div>
        <pre>{{ c.text(shown ? token : "••••••••••••") }}</pre>
      </div>
    </section>
  </main>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding-top: 40px;
}
.frame {
  /* the QR is always black-on-white (scanners need that contrast); a fixed white box keeps it readable in dark mode too */
  background: #fff;
  padding: 16px;
  border-radius: 12px;
}
.frame img {
  display: block;
}
.ai {
  align-self: stretch;
  border-top: 1px solid var(--line);
  margin-top: 24px;
  min-width: 0;
}
h2 {
  font-size: 17px;
  margin: 16px 0 4px;
}
.ai p {
  margin: 4px 0;
  font-size: 14px;
}
code {
  overflow-wrap: anywhere;
}
.warn {
  background: var(--soft);
  border-radius: 10px;
  padding: 8px 14px 8px 30px;
  margin: 8px 0;
  font-size: 13px;
  color: var(--warm);
}
.bar {
  margin: 8px 0;
}
.client {
  margin-top: 12px;
}
.head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 14px;
}
.head .muted {
  flex: 1;
  font-size: 12px;
}
pre {
  background: var(--soft);
  border-radius: 8px;
  padding: 8px 12px;
  margin: 4px 0 0;
  font-size: 12px;
  white-space: pre-wrap; /* the phone shows it all; 复制 copies it as it is */
  overflow-wrap: anywhere;
}
</style>
