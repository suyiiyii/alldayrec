// node --experimental-strip-types progress.check.ts: historical imports have audio progress, live recorders lag.
import assert from "node:assert/strict";
import { stripTypeScriptTypes } from "node:module";
import { createServer } from "vite-plus";
import { createRenderer, h, markRaw, nextTick } from "vue";
import { compileScript, parse } from "vue/compiler-sfc";
import { i18n } from "./src/i18n.ts";
import type { DeviceStatus } from "./src/lib/api.ts";
import { importLine, showLiveLag } from "./src/lib/progress.ts";

const imported = {
  device: "import-synthetic",
  recording: false,
  import_progress: { done_s: 120, total_s: 600 },
  arrival: { at: Date.now(), lag_s: 86400 * 100 },
} as DeviceStatus;
i18n.global.locale.value = "zh";
assert.equal(importLine(imported), "已转写 2 / 10 分钟音频");
assert.equal(showLiveLag(imported), false);
assert.equal(showLiveLag({ ...imported, recording: true }), false);
assert.equal(showLiveLag({ device: "recorder-demo", recording: true } as DeviceStatus), true);
assert.equal(showLiveLag({ device: "recorder-demo", recording: false } as DeviceStatus), false);
i18n.global.locale.value = "en";
assert.equal(importLine(imported), "Transcribed 2 / 10 min of audio");
assert.equal(
  importLine({ ...imported, import_progress: { done_s: 600, total_s: 600 } }),
  "Transcribed 10 / 10 min of audio",
);
assert.equal(
  importLine({ ...imported, import_progress: { done_s: 30, total_s: 90 } }),
  "Transcribed 0.5 / 1.5 min of audio",
);
assert.equal(importLine({ device: "recorder-demo" } as DeviceStatus), "");

// Compile the real page's client render function into a small Vue host renderer: no browser dependency in CI.
type Node = { tag: string; text: string; children: Node[]; parent?: Node };
const node = (tag = "", text = ""): Node => markRaw({ tag, text, children: [] });
const renderer = createRenderer<Node, Node>({
  createElement: (tag) => node(tag),
  createText: (text) => node("text", text),
  createComment: () => node(),
  setText: (n, text) => {
    n.text = text;
  },
  setElementText: (n, text) => {
    n.text = text;
    n.children = [];
  },
  patchProp() {},
  insert(n, parent, anchor) {
    if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1);
    n.parent = parent;
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    parent.children.splice(index < 0 ? parent.children.length : index, 0, n);
  },
  remove(n) {
    n.parent?.children.splice(n.parent.children.indexOf(n), 1);
  },
  parentNode: (n) => n.parent ?? null,
  nextSibling: (n) => n.parent?.children[n.parent.children.indexOf(n) + 1] ?? null,
});
const text = (n: Node): string => n.text + n.children.map(text).join(" ");
const sections = (n: Node): Node[] => (n.tag === "section" ? [n] : n.children.flatMap(sections));
const live = {
  ...imported,
  device: "recorder-demo",
  import_progress: null,
  recording: true,
  left_s: 60,
  queued: 1,
  rate: 2,
  eta_s: 30,
  arrival: { at: Date.now(), lag_s: 8 },
};
let status = { recording: true, devices: [imported, live] };
class Socket {
  send() {}
  close() {}
}
Object.assign(globalThis, {
  window: { setInterval: () => 0 },
  document: { hidden: false, addEventListener() {}, removeEventListener() {} },
  location: { protocol: "https:", host: "example.test" },
  cancelAnimationFrame() {},
  WebSocket: Socket,
});
globalThis.fetch = async (input) =>
  Response.json(
    String(input).startsWith("/api/status")
      ? status
      : String(input).startsWith("/api/calls")
        ? { calls: [] }
        : [],
  );
const server = await createServer({
  configFile: false,
  server: { middlewareMode: true },
  plugins: [
    {
      name: "check-client-vue",
      transform(source, id) {
        if (!id.endsWith(".vue")) return;
        if (!/\/pages\/(Progress|Import)\.vue$/.test(id))
          return "export default { render: () => null }";
        const { descriptor } = parse(source, { filename: id });
        const compiled = compileScript(descriptor, { id: "progress-check", inlineTemplate: true });
        return { code: stripTypeScriptTypes(compiled.content), map: null };
      },
    },
  ],
});
try {
  const { default: Page } = await server.ssrLoadModule("/src/pages/Progress.vue");
  const { default: ImportPage } = await server.ssrLoadModule("/src/pages/Import.vue");
  const { i18n: pageI18n } = await server.ssrLoadModule("/src/i18n.ts");
  for (const [locale, estimate] of [
    ["zh", null],
    ["en", null],
    ["zh", 120],
    ["en", 120],
  ] as const) {
    status = { recording: true, devices: [{ ...imported, left_s: 480, eta_s: estimate }, live] };
    pageI18n.global.locale.value = locale;
    const root = node("root");
    const app = renderer.createApp(Page);
    app.component("RouterLink", { template: "", render: () => null });
    app.mount(root);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await nextTick();
    const cards = sections(root);
    const batch = cards.find((c) => text(c).includes("import-synthetic"))!;
    const recorder = cards.find((c) => text(c).includes("recorder-demo"))!;
    assert.ok(batch && recorder, "the real progress page renders both sources");
    assert.match(
      text(batch),
      locale === "zh" ? /已转写 2 \/ 10 分钟音频/ : /Transcribed 2 \/ 10 min of audio/,
    );
    assert.doesNotMatch(text(batch), /实时延迟|Live lag|待处理|Left/);
    if (estimate) assert.match(text(batch), locale === "zh" ? /预计完成.*约 2 分钟/ : /ETA.*2 min/);
    else assert.doesNotMatch(text(batch), /预计完成|ETA/);
    assert.match(text(recorder), locale === "zh" ? /实时延迟/ : /Live lag/);
    assert.match(text(recorder), /8/);
    app.unmount();
    const history = node("root");
    Object.assign(status, {
      imports: [
        {
          device: imported.device,
          audio_start: "2026-01-01T10:00:00",
          audio_end: "2026-01-01T10:10:00",
        },
      ],
    });
    const historyApp = renderer.createApp(ImportPage);
    historyApp.component("RouterLink", {
      setup:
        (_, { slots }) =>
        () =>
          h("a", slots.default?.()),
    });
    historyApp.mount(history);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await nextTick();
    assert.match(
      text(history),
      locale === "zh" ? /已转写 2 \/ 10 分钟音频/ : /Transcribed 2 \/ 10 min of audio/,
    );
    assert.doesNotMatch(text(history), /还剩|Transcribing, about/);
    historyApp.unmount();
  }
} finally {
  await server.close();
}
console.log("progress check ok: import audio progress and live delay, zh/en page rendering");
