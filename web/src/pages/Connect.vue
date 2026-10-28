<script setup lang="ts">
import QRCode from "qrcode";
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { I18nT } from "vue-i18n";
import { useRoute } from "vue-router";
import AppHeader from "../components/AppHeader.vue";
import Icon from "../components/Icon.vue";
import { lang, t } from "../i18n";
import { get, post, type Status } from "../lib/api";

// Three steps for a new user: the app, the QR, the first caption. A step is ticked when the server can tell: a
// device appears once an app uploads (so it is installed and logged in), /api/days once a sentence is in
const REPO = "https://github.com/suyiiyii/alldayrec";
const DOCS = "https://suyiiyii.github.io/alldayrec";
// building the iOS app, on the docs site
const IOS_DOCS = { zh: `${DOCS}/how-to/install-app.html#ios`, en: `${DOCS}/en/how-to/install-app.html#ios` };
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
  phone.value = !!s?.recorders?.length; // a phone has sent audio here (devices: only the pipelines running now)
  heard.value = d.length > 0;
});

// 手机用哪个地址: the page's own origin is often 127.0.0.1 (opened on the server's machine), which a phone can't reach;
// /api/addresses lists the machine's Tailscale / LAN / public IPs (none in a container: then 自己填). The QR, the
// manual entry and the MCP snippets all use the one picked; remembered in localStorage (connect.address)
type Addr = { ip: string; kind: "tailscale" | "lan" | "public" };
type Addrs = { addresses: Addr[]; port: number; bind: string; in_container: boolean; proxied: boolean };
const here = location.origin;
const loopback = /^(localhost|127\.|\[::1\])/.test(location.hostname);
const found = ref<Addrs | null>(null);
const options = computed(() => [
  { url: here, label: t("connect.here") },
  ...(found.value?.addresses ?? []).map((a) => ({
    url: `http://${a.ip}:${found.value!.port}`,
    label: t(`connect.kind.${a.kind}`),
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

// The QR is this web app's URL with the token: the app's settings page scans it for server + token (no typing; manual
// entry stays), a phone camera opens it and the server signs that browser in (auth.py).
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
watch([address, token], async ([a, tok]) => {
  if (!a || error.value) return void (qr.value = "");
  const url = `${a}/?token=${encodeURIComponent(tok)}`;
  qr.value = await QRCode.toDataURL(url, { width: 320, margin: 2 });
});
const tokenCopied = ref(false);
async function copyToken() {
  await navigator.clipboard.writeText(token.value);
  tokenCopied.value = true;
  setTimeout(() => (tokenCopied.value = false), 2000);
}

// 接入 AI: the agents that take a URL and a header (server mcp_server.py), with the same token as the app.
// Shown with the token hidden; 复制 always copies the real one. Folded (few need it); /connect#ai opens it (/me links there).
// where: a key in locales/*.json
const mcp = computed(() => `${address.value || here}/mcp`);
type AiLink = {
  id: string;
  label: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
};
const aiLinks = ref<AiLink[]>([]),
  linkLabel = ref(""),
  linkError = ref(""),
  linkBusy = ref(false);
const linkNotice = ref("");
const freshLink = ref<{ id: string; path: string } | null>(null),
  linkCopied = ref(false);
const freshUrl = computed(() =>
  freshLink.value ? `${address.value || here}${freshLink.value.path}` : "",
);
watch(freshUrl, () => (linkCopied.value = false));
const linkDate = (value: string) =>
  new Date(value).toLocaleString(lang() === "zh" ? "zh-CN" : "en-US");
onMounted(async () => {
  try {
    aiLinks.value = await get<AiLink[]>("/api/mcp-links");
  } catch (e) {
    linkError.value = (e as Error).message;
  }
});
async function createLink() {
  linkBusy.value = true;
  linkError.value = "";
  try {
    const row = await post<AiLink & { path: string }>("/api/mcp-links", { label: linkLabel.value });
    const { path, ...metadata } = row;
    freshLink.value = { id: row.id, path };
    linkCopied.value = false;
    aiLinks.value.unshift(metadata);
    linkLabel.value = "";
  } catch (e) {
    linkError.value = (e as Error).message;
  } finally {
    linkBusy.value = false;
  }
}
async function copyLink() {
  try {
    await navigator.clipboard.writeText(freshUrl.value);
    linkCopied.value = true;
  } catch {
    linkError.value = t("connect.linkCopyError");
  }
}
async function revokeLink(link: AiLink) {
  if (!window.confirm(t("connect.linkConfirm", { label: link.label }))) return;
  linkBusy.value = true;
  linkError.value = "";
  linkNotice.value = "";
  try {
    const result = await post<{ pending?: boolean }>(`/api/mcp-links/${link.id}`, undefined, "DELETE");
    if (result.pending) linkNotice.value = t("connect.linkDraining");
    aiLinks.value = aiLinks.value.filter((x) => x.id !== link.id);
    if (freshLink.value?.id === link.id) freshLink.value = null;
  } catch (e) {
    linkError.value = (e as Error).message;
  } finally {
    linkBusy.value = false;
  }
}
const json = (server: object) => JSON.stringify({ mcpServers: { alldayrec: server } }, null, 2);
const CLIENTS: { name: string; where: string; text: (tok: string) => string }[] = [
  {
    name: "Claude Code",
    where: "connect.where.claude",
    text: (tok) =>
      `claude mcp add --transport http alldayrec ${mcp.value} --header "Authorization: Bearer ${tok}" --scope user`,
  },
  {
    name: "Codex",
    where: "connect.where.codex",
    text: (tok) =>
      `[mcp_servers.alldayrec]\nurl = "${mcp.value}"\nhttp_headers = { "Authorization" = "Bearer ${tok}" }`,
  },
  {
    name: "Cursor",
    where: "connect.where.cursor",
    text: (tok) => json({ url: mcp.value, headers: { Authorization: `Bearer ${tok}` } }),
  },
  {
    name: "Cherry Studio",
    where: "connect.where.cherry",
    text: (tok) =>
      json({ type: "streamableHttp", url: mcp.value, headers: { Authorization: `Bearer ${tok}` } }),
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
    <h1>{{ t("title.connect") }}</h1>
    <ol class="steps">
      <li class="card step" :class="{ done: phone }">
        <span class="num"
          ><Icon v-if="phone" name="check" :size="16" /><template v-else>1</template></span
        >
        <div class="body">
          <h2>{{ t("connect.install") }}</h2>
          <p>
            <a v-if="apk" class="btn btn-primary" href="/app.apk">{{ t("connect.apk") }}</a>
            <a
              v-else
              class="btn btn-primary"
              :href="REPO + '/releases'"
              target="_blank"
              rel="noopener"
              >{{ t("connect.apkGithub") }}</a
            >
          </p>
          <I18nT keypath="connect.ios" tag="p" class="muted" scope="global">
            <template #link
              ><a :href="IOS_DOCS[lang()]" target="_blank" rel="noopener">{{
                t("connect.iosLink")
              }}</a></template
            >
          </I18nT>
        </div>
      </li>
      <li class="card step" :class="{ done: phone }">
        <span class="num"
          ><Icon v-if="phone" name="check" :size="16" /><template v-else>2</template></span
        >
        <div class="body">
          <h2>{{ t("connect.scan") }}</h2>
          <p class="muted">{{ t("connect.scanHow") }}</p>
          <fieldset class="where" @change="remember">
            <legend>{{ t("connect.which") }}</legend>
            <label v-for="o in options" :key="o.url" class="opt">
              <input v-model="pick" type="radio" name="addr" :value="o.url" />
              <span class="what"
                ><b>{{ o.label }}</b> <code>{{ o.url }}</code>
                <span v-if="o.url === here && loopback" class="muted hint">{{
                  t("connect.unreachable")
                }}</span></span
              >
            </label>
            <label class="opt">
              <input v-model="pick" type="radio" name="addr" value="custom" />
              <span class="what"
                ><b>{{ t("connect.custom") }}</b></span
              >
            </label>
            <template v-if="pick === 'custom'">
              <input
                v-model="custom"
                class="custom"
                type="url"
                inputmode="url"
                placeholder="http://192.168.1.5:8770"
                :aria-label="t('connect.server')"
                @input="remember"
              />
              <p v-if="custom && !address" class="bad">{{ t("connect.badUrl") }}</p>
            </template>
            <p v-if="found?.in_container" class="muted">{{ t("connect.docker") }}</p>
            <p
              v-else-if="
                found && !found.addresses.length && !['0.0.0.0', '::'].includes(found.bind)
              "
              class="muted"
            >
              {{ t("connect.bind", { bind: found.bind }) }}
            </p>
          </fieldset>
          <p v-if="error" class="bad">{{ t("connect.qrFailed", { error }) }}</p>
          <template v-else-if="address">
            <div class="frame">
              <img v-if="qr" :src="qr" width="320" height="320" :alt="t('connect.qrAlt')" />
            </div>
            <p class="muted">
              <I18nT keypath="connect.manual" scope="global"
                ><template #address
                  ><code>{{ address }}</code></template
                ></I18nT
              >
              <button v-if="token" class="inline" @click="copyToken">
                {{ tokenCopied ? t("connect.copied") : t("connect.copyToken") }}
              </button>
            </p>
          </template>
          <p v-if="phone" class="ok">{{ t("connect.connected") }}</p>
        </div>
      </li>
      <li class="card step" :class="{ done: heard }">
        <span class="num"
          ><Icon v-if="heard" name="check" :size="16" /><template v-else>3</template></span
        >
        <div class="body">
          <h2>{{ t("connect.first") }}</h2>
          <p class="muted">{{ t("connect.firstHow") }}</p>
          <p>
            <RouterLink class="btn" to="/live"
              ><Icon name="mic" :size="16" />{{ t("connect.openLive") }}</RouterLink
            >
          </p>
          <I18nT keypath="connect.diy" tag="p" class="muted" scope="global">
            <template #board><span class="nw">LR-P3</span></template>
            <template #link
              ><a
                :href="REPO + '/blob/main/hardware/lr-p3/README.md'"
                target="_blank"
                rel="noopener"
                >{{ t("connect.diyLink") }}</a
              ></template
            >
          </I18nT>
        </div>
      </li>
    </ol>

    <details v-if="token" id="ai" class="card ai" :open="openAi">
      <summary>
        <Icon name="sparkles" :size="18" />{{ t("connect.ai")
        }}<Icon class="chev" name="chevron-down" :size="18" />
      </summary>
      <p>{{ t("connect.aiWhat") }}</p>
      <section class="links" aria-labelledby="link-title">
        <h2 id="link-title">{{ t("connect.linkTitle") }}</h2>
        <p class="muted">{{ t("connect.linkHow") }}</p>
        <p>
          <a
            :href="`${DOCS}/${lang() === 'en' ? 'en/' : ''}how-to/cloudflare-tunnel.html`"
            target="_blank"
            rel="noopener"
            >{{ t("connect.linkTunnel") }}</a
          >
        </p>
        <form class="link-form" @submit.prevent="createLink">
          <label for="link-label">{{ t("connect.linkLabel") }}</label>
          <div class="link-controls">
            <input
              id="link-label"
              v-model="linkLabel"
              :placeholder="t('connect.linkPlaceholder')"
              maxlength="80"
              required
              :disabled="linkBusy"
            />
            <button :disabled="linkBusy || !linkLabel.trim()">{{ t("connect.linkCreate") }}</button>
          </div>
        </form>
        <div v-if="freshLink" class="fresh-link">
          <p>{{ t("connect.linkOnce") }}</p>
          <label for="fresh-link">{{ t("connect.linkTitle") }}</label>
          <textarea id="fresh-link" :value="freshUrl" readonly rows="3" spellcheck="false" />
          <button @click="copyLink">{{ t(linkCopied ? "connect.copied" : "connect.copy") }}</button>
          <button @click="freshLink = null">{{ t("connect.linkHide") }}</button>
        </div>
        <p v-if="linkError" role="alert" class="link-error">{{ linkError }}</p>
        <p v-if="linkNotice" id="link-notice" role="status">{{ linkNotice }}</p>
        <ul v-if="aiLinks.length" class="link-list">
          <li v-for="link in aiLinks" :key="link.id">
            <div class="link-meta">
              <b>{{ link.label }}</b> <code>{{ link.prefix }}…</code>
              <small>{{ t("connect.linkCreated", { at: linkDate(link.created_at) }) }}</small>
              <small>{{
                link.last_used_at
                  ? t("connect.linkUsed", { at: linkDate(link.last_used_at) })
                  : t("connect.linkUnused")
              }}</small>
            </div>
            <button :disabled="linkBusy" @click="revokeLink(link)">
              {{ t("connect.linkRevoke") }}
            </button>
          </li>
        </ul>
        <p class="muted">{{ t("connect.linkSafety") }}</p>
      </section>
      <I18nT keypath="connect.aiUrl" tag="p" scope="global">
        <template #url
          ><code>{{ mcp }}</code></template
        >
      </I18nT>
      <ul class="warn">
        <li>{{ t("connect.warnProvider") }}</li>
        <li>{{ t("connect.warnTools") }}</li>
        <li>{{ t("connect.warnToken") }}</li>
      </ul>
      <div class="bar">
        <button @click="shown = !shown">
          {{ shown ? t("connect.hideToken") : t("connect.showToken") }}
        </button>
      </div>
      <div v-for="c in CLIENTS" :key="c.name" class="client">
        <div class="head">
          <b>{{ c.name }}</b>
          <span class="muted">{{ t(c.where) }}</span>
          <button @click="copy(c)">{{ copied === c.name ? t("connect.copied") : t("connect.copy") }}</button>
        </div>
        <pre>{{ c.text(shown ? token : "••••••••••••") }}</pre>
      </div>
    </details>
  </main>
</template>

<style scoped>
.links {
  margin: var(--space-3) 0;
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--line);
}
.link-form {
  margin: var(--space-3) 0;
  font-size: var(--fs-sm);
}
.link-controls {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-1);
}
.link-controls input {
  min-width: 0;
  flex: 1;
  width: 100%;
  padding: var(--space-2);
}
.link-controls button,
.link-list button {
  flex: none;
}
.fresh-link {
  background: var(--bg);
  padding: var(--space-2);
  border-radius: var(--radius-sm);
}
.fresh-link textarea {
  display: block;
  box-sizing: border-box;
  width: 100%;
  resize: vertical;
  margin: var(--space-2) 0;
  font-size: var(--fs-xs);
  overflow-wrap: anywhere;
}
.fresh-link button {
  margin-right: var(--space-2);
}
.link-list {
  list-style: none;
  padding: 0;
}
.link-list li {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--line);
}
.link-meta {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--fs-sm);
}
.link-meta small {
  display: block;
  color: var(--muted);
  font-size: var(--fs-xs);
}
.link-error {
  color: var(--warn);
}
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
