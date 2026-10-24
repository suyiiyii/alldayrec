<script setup lang="ts">
import QRCode from "qrcode";
import { computed, nextTick, onMounted, ref, watch } from "vue";
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

// 手机用哪个地址: the page's own origin is often 127.0.0.1 (opened on the server's machine), which a phone can't reach;
// /api/addresses lists the machine's Tailscale / LAN / public IPs (none in a container: then 自己填). The QR, the
// manual entry and the MCP snippets all use the one picked; remembered in localStorage (connect.address)
type Addr = { ip: string; kind: "tailscale" | "lan" | "public" };
type Addrs = { addresses: Addr[]; port: number; bind: string; in_container: boolean; proxied: boolean };
const KIND = { tailscale: "Tailscale", lan: "局域网", public: "公网" };
const here = location.origin;
const loopback = /^(localhost|127\.|\[::1\])/.test(location.hostname);
const found = ref<Addrs | null>(null);
const options = computed(() => [
  { url: here, label: "这个网页的地址" },
  ...(found.value?.addresses ?? []).map((a) => ({
    url: `http://${a.ip}:${found.value!.port}`,
    label: KIND[a.kind],
  })),
]);
const pick = ref(""), // an option's url, or "custom"
  custom = ref("");
function valid(s: string): string {
  // http(s)://host[:port], nothing after it: the app appends the paths itself
  try {
    const u = new URL(s.trim());
    return /^https?:$/.test(u.protocol) && u.pathname === "/" && !u.search && !u.hash ? u.origin : "";
  } catch {
    return "";
  }
}
const address = computed(() => (pick.value === "custom" ? valid(custom.value) : pick.value));
function load(key: string) {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}
function remember() {
  // only what was picked by hand: a default stays a default (the LAN address may change)
  try {
    if (address.value) localStorage.setItem("connect.address", address.value);
  } catch {}
}
onMounted(async () => {
  found.value = await get<Addrs>("/api/addresses").catch(() => null);
  const saved = load("connect.address"),
    urls = options.value.map((o) => o.url);
  if (saved && !urls.includes(saved)) [pick.value, custom.value] = ["custom", saved];
  else pick.value = saved || (loopback && urls[1]) || here; // not 127.0.0.1 when there is something better
});

// The app's settings page scans this: server + token, so they don't need typing in by hand (manual entry stays).
// The token comes from the server, not from the cookie the browser holds (it's HttpOnly, page JS can't read it).
const qr = ref(""),
  error = ref(""),
  token = ref("");
onMounted(async () => {
  try {
    token.value = (await get<{ token: string }>("/api/app-setup")).token;
    if (openAi) nextTick(() => document.getElementById("ai")?.scrollIntoView());
  } catch (e) {
    error.value = (e as Error).message;
  }
});
watch([address, token], async ([a, t]) => {
  if (!a || error.value) return void (qr.value = "");
  const url = `liverecord://setup?server=${encodeURIComponent(a)}&token=${encodeURIComponent(t)}`;
  qr.value = await QRCode.toDataURL(url, { width: 320, margin: 2 });
});
const tokenCopied = ref(false);
async function copyToken() {
  await navigator.clipboard.writeText(token.value);
  tokenCopied.value = true;
  setTimeout(() => (tokenCopied.value = false), 2000);
}

// 接入 AI: the agents that take a URL and a header (server mcp_server.py), with the same token as the app.
// Shown with the token hidden; 复制 always copies the real one. Folded (few need it); /connect#ai opens it (/me links there)
const mcp = computed(() => `${address.value || here}/mcp`);
const json = (server: object) => JSON.stringify({ mcpServers: { alldayrec: server } }, null, 2);
const CLIENTS: { name: string; where: string; text: (t: string) => string }[] = [
  {
    name: "Claude Code",
    where: "在终端里运行一次",
    text: (t) =>
      `claude mcp add --transport http alldayrec ${mcp.value} --header "Authorization: Bearer ${t}" --scope user`,
  },
  {
    name: "Codex",
    where: "加进 ~/.codex/config.toml",
    text: (t) =>
      `[mcp_servers.alldayrec]\nurl = "${mcp.value}"\nhttp_headers = { "Authorization" = "Bearer ${t}" }`,
  },
  {
    name: "Cursor",
    where: "加进 ~/.cursor/mcp.json",
    text: (t) => json({ url: mcp.value, headers: { Authorization: `Bearer ${t}` } }),
  },
  {
    name: "Cherry Studio",
    where: "设置 › MCP › 添加 › 从 JSON 导入",
    text: (t) =>
      json({ type: "streamableHttp", url: mcp.value, headers: { Authorization: `Bearer ${t}` } }),
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
          <fieldset class="where" @change="remember">
            <legend>手机用哪个地址</legend>
            <label v-for="o in options" :key="o.url" class="opt">
              <input v-model="pick" type="radio" name="addr" :value="o.url" />
              <span class="what"
                ><b>{{ o.label }}</b> <code>{{ o.url }}</code>
                <span v-if="o.url === here && loopback" class="muted hint"
                  >手机连不上这个地址</span
                ></span
              >
            </label>
            <label class="opt">
              <input v-model="pick" type="radio" name="addr" value="custom" />
              <span class="what"><b>自己填</b></span>
            </label>
            <template v-if="pick === 'custom'">
              <input
                v-model="custom"
                class="custom"
                type="url"
                inputmode="url"
                placeholder="http://192.168.1.5:8770"
                aria-label="服务器地址"
                @input="remember"
              />
              <p v-if="custom && !address" class="bad">要写成 http://地址:端口 或 https://域名</p>
            </template>
            <p v-if="found?.in_container" class="muted">
              服务跑在 Docker 里，列不出这台电脑的地址：在电脑上用它的局域网或 Tailscale IP 打开这个网页（端口默认
              8770），或者选「自己填」
            </p>
            <p v-else-if="found && !found.addresses.length && !['0.0.0.0', '::'].includes(found.bind)" class="muted">
              服务只监听 {{ found.bind }}（.env 里的 HOST），别的机器连不进来：手机要直连，改成 HOST=0.0.0.0 再重启
            </p>
          </fieldset>
          <p v-if="error" class="bad">二维码生成失败：{{ error }}</p>
          <template v-else-if="address">
            <div class="frame">
              <img v-if="qr" :src="qr" width="320" height="320" alt="登录二维码" />
            </div>
            <p class="muted">
              扫不了码：在 App 里手动填服务器 <code>{{ address }}</code> 和 Token
              <button v-if="token" class="inline" @click="copyToken">
                {{ tokenCopied ? "已复制" : "复制 Token" }}
              </button>
            </p>
          </template>
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
.where {
  border: 0;
  padding: 0;
  margin: var(--space-3) 0 0;
  min-width: 0;
}
.where legend {
  padding: 0;
  font-size: var(--fs-sm);
  font-weight: 600;
}
.opt {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  min-height: 36px;
  padding: var(--space-1) 0;
  font-size: var(--fs-sm);
  cursor: pointer;
}
.opt input {
  flex: none;
  width: 18px;
  height: 18px;
  margin: 3px 0 0;
  accent-color: var(--accent);
}
.what {
  min-width: 0;
}
.what code {
  overflow-wrap: anywhere;
}
.hint {
  display: block;
  font-size: var(--fs-xs);
}
.custom {
  display: block;
  width: 100%;
  box-sizing: border-box;
  min-height: 36px;
  padding: 0 var(--space-3);
  margin-top: var(--space-1);
}
.where p {
  font-size: var(--fs-xs);
}
.inline {
  min-height: 28px;
  margin-left: var(--space-1);
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
