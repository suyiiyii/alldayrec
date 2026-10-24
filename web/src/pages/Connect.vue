<script setup lang="ts">
import QRCode from "qrcode";
import { nextTick, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import Icon from "../components/Icon.vue";
import { get, type Status } from "../lib/api";

// Three steps for a new user: the app, the QR, the first caption. A step is ticked when the server can tell: a
// device appears once an app uploads (so it is installed and logged in), /api/days once a sentence is in
const REPO = "https://github.com/suyiiyii/alldayrec";
const apk = ref(false),
  phone = ref(false),
  heard = ref(false);
onMounted(async () => {
  const [a, s, d] = await Promise.all([
    get("/api/app/latest").catch(() => null), // 404: no APK on this server, the Releases page has one
    get<Status>("/api/status").catch(() => null),
    get<string[]>("/api/days").catch(() => []),
  ]);
  apk.value = !!a;
  phone.value = !!s?.devices?.length;
  heard.value = d.length > 0;
});

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
    if (openAi) nextTick(() => document.getElementById("ai")?.scrollIntoView());
  } catch (e) {
    error.value = (e as Error).message;
  }
});

// 接入 AI: the agents that take a URL and a header (server mcp_server.py), with the same token as the app.
// Shown with the token hidden; 复制 always copies the real one. Folded (few need it); /connect#ai opens it (/me links there)
const mcp = `${location.origin}/mcp`;
const json = (server: object) => JSON.stringify({ mcpServers: { alldayrec: server } }, null, 2);
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
const openAi = useRoute().hash === "#ai";
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
  <main>
    <h1>连接手机</h1>
    <ol class="steps">
      <li class="card step" :class="{ done: phone }">
        <span class="num"
          ><Icon v-if="phone" name="check" :size="16" /><template v-else>1</template></span
        >
        <div class="body">
          <h2>安装 App</h2>
          <p>
            <a v-if="apk" class="btn btn-primary" href="/app.apk">下载安卓 App</a>
            <a
              v-else
              class="btn btn-primary"
              :href="REPO + '/releases'"
              target="_blank"
              rel="noopener"
              >去 GitHub 下载安卓 App</a
            >
          </p>
          <p class="muted">
            iPhone：App 没有上架 App Store，要用 Xcode 从源码构建，步骤见
            <a :href="REPO + '#手机-app'" target="_blank" rel="noopener">说明</a>。
          </p>
        </div>
      </li>
      <li class="card step" :class="{ done: phone }">
        <span class="num"
          ><Icon v-if="phone" name="check" :size="16" /><template v-else>2</template></span
        >
        <div class="body">
          <h2>扫码登录</h2>
          <p class="muted">
            在 App 的「设置 › 扫码登录」里扫这个二维码，服务器地址和 Token 会自动填好
          </p>
          <p v-if="error" class="bad">二维码生成失败：{{ error }}</p>
          <div v-else class="frame">
            <img v-if="qr" :src="qr" width="320" height="320" alt="登录二维码" />
          </div>
          <p v-if="phone" class="ok">已经有手机连上了；换手机或多一台手机，再扫一次就行</p>
        </div>
      </li>
      <li class="card step" :class="{ done: heard }">
        <span class="num"
          ><Icon v-if="heard" name="check" :size="16" /><template v-else>3</template></span
        >
        <div class="body">
          <h2>戴上录音设备，看到第一句字幕</h2>
          <p class="muted">
            在 App
            里连上录音设备（没有设备也可以直接用手机录），说一句话，几秒后字幕就会出现在「实时字幕」里
          </p>
          <p>
            <RouterLink class="btn" to="/live"
              ><Icon name="mic" :size="16" />打开实时字幕</RouterLink
            >
          </p>
          <p class="muted">
            还没有录音设备？可以自己做一个：开源的 <span class="nw">LR-P3</span>，零件、接线和外壳见
            <a :href="REPO + '/blob/main/hardware/lr-p3/README.md'" target="_blank" rel="noopener"
              >GitHub 上的说明</a
            >
          </p>
        </div>
      </li>
    </ol>

    <details v-if="token" id="ai" class="card ai" :open="openAi">
      <summary>
        <Icon name="sparkles" :size="18" />高级：接入 AI<Icon
          class="chev"
          name="chevron-down"
          :size="18"
        />
      </summary>
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
    </details>
  </main>
</template>

<style scoped>
h1 {
  margin: var(--space-4) 0;
}
.steps {
  list-style: none;
  padding: 0;
  margin: 0;
}
.step {
  display: flex;
  gap: var(--space-3);
}
.num {
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--accent);
  color: var(--surface);
  font-weight: 600;
  font-size: var(--fs-sm);
}
.done .num {
  background: var(--ok);
}
.body {
  flex: 1;
  min-width: 0;
}
h2 {
  margin: 0 0 var(--space-2);
  font-size: var(--fs-md);
  line-height: 28px;
}
.body p {
  margin: var(--space-2) 0 0;
  font-size: var(--fs-sm);
}
.ok {
  color: var(--ok);
}
.nw {
  white-space: nowrap;
}
.frame {
  /* the QR is always black-on-white (scanners need that contrast); a fixed white box keeps it readable in dark mode too */
  width: fit-content;
  max-width: 100%;
  margin-top: var(--space-3);
  background: #fff;
  padding: var(--space-2);
  border-radius: var(--radius);
  box-shadow: 0 0 0 1px var(--line);
}
.frame img {
  display: block;
  max-width: 100%;
  height: auto;
}
.ai {
  scroll-margin-top: 96px; /* /connect#ai: clear of the sticky header */
}
.ai summary {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 36px;
  font-weight: 600;
  cursor: pointer;
  list-style: none;
}
.ai summary::-webkit-details-marker {
  display: none;
}
.ai .chev {
  margin-left: auto;
  color: var(--muted);
  transition: transform 0.15s;
}
.ai[open] .chev {
  transform: rotate(180deg);
}
.ai p {
  margin: var(--space-1) 0;
  font-size: var(--fs-sm);
}
code {
  overflow-wrap: anywhere;
}
.warn {
  background: var(--warn-soft);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3) var(--space-2) 30px;
  margin: var(--space-2) 0;
  font-size: var(--fs-sm);
  color: var(--warn);
}
.bar {
  margin: var(--space-2) 0;
}
.client {
  margin-top: var(--space-3);
}
.head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--fs-sm);
}
.head .muted {
  flex: 1;
  font-size: var(--fs-xs);
}
pre {
  background: var(--bg);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  margin: var(--space-1) 0 0;
  font-size: var(--fs-xs);
  white-space: pre-wrap; /* the phone shows it all; 复制 copies it as it is */
  overflow-wrap: anywhere;
}
</style>
