// node --experimental-strip-types progress.check.ts: historical imports have audio progress, live recorders lag.
import assert from "node:assert/strict";
import { stripTypeScriptTypes } from "node:module";
import { createServer } from "vite-plus";
import { createRenderer, h, markRaw, nextTick } from "vue";
import { compileScript, parse } from "vue/compiler-sfc";
import { i18n } from "./src/i18n.ts";
import type { DeviceStatus } from "./src/lib/api.ts";
import { importLine, recordingNow, showLiveLag } from "./src/lib/progress.ts";

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
// the live page's status line: who records now, who stands by for one in the same place, the transcribing ones' lag
const rec = (device: string, more: Partial<DeviceStatus> = {}) =>
  ({ device, recording: true, latency_s: 3, ...more }) as DeviceStatus;
assert.deepEqual(
  recordingNow([
    rec("LR-P3-0001"),
    rec("LR-N4-0002", { recording: false, latency_s: 90 }),
    imported,
  ]),
  {
    main: ["LR-P3-0001"],
    spare: [],
    lag: 3,
  },
);
assert.deepEqual(
  recordingNow([rec("LR-P3-0001"), rec("LR-N4-0002", { standby: "LR-P3-0001", latency_s: 90 })]),
  {
    main: ["LR-P3-0001"],
    spare: ["LR-N4-0002"],
    lag: 3,
  },
);
assert.deepEqual(recordingNow([rec("LR-P3-0001"), rec("LR-N4-0002", { latency_s: 5 })]), {
  main: ["LR-P3-0001", "LR-N4-0002"],
  spare: [],
  lag: 5,
});
// all standing by, for one that stopped: named, but no lag from before they stood by
assert.deepEqual(recordingNow([rec("LR-N4-0002", { standby: "LR-P3-0001", latency_s: 90 })]), {
  main: ["LR-N4-0002"],
  spare: [],
  lag: null,
});
// standing by for one that stopped while another records elsewhere: no 「+1 备用」 beside that other one
assert.deepEqual(
  recordingNow([rec("LR-N4-0002", { standby: "LR-P3-0001", latency_s: 90 }), rec("LR-X1-0003")]),
  { main: ["LR-N4-0002", "LR-X1-0003"], spare: [], lag: 3 },
);
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
const sockets: Socket[] = [];
class Socket {
  onmessage?: (e: { data: string }) => void;
  constructor() {
    sockets.push(this);
  }
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
    status = {
      recording: true,
      devices: [
        { ...imported, left_s: 480, eta_s: estimate },
        live,
        { ...live, device: "recorder-spare", standby: "recorder-demo" },
      ],
    };
    pageI18n.global.locale.value = locale;
    const root = node("root");
    const app = renderer.createApp(Page);
    app.component("RouterLink", { template: "", render: () => null });
    app.mount(root);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await nextTick();
    const cards = sections(root);
    const batch = cards.find((c) => text(c).includes("import-synthetic"))!;
    const recorder = cards.find((c) => text(c).startsWith("recorder-demo"))!;
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
    // the live page's 「+1 备用」 opens this page: a phone sees the standby's name here
    const spare = cards.find((c) => text(c).includes("recorder-spare"))!;
    assert.match(text(spare), locale === "zh" ? /和 recorder-demo 在同一处/ : /With recorder-demo/);
    assert.doesNotMatch(text(recorder), /在同一处|With recorder/);
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
  // the cards keep their places while the status every 2 s says another device's audio came last: a tap meant for
  // one card's 日志 lands on it (11-13: the cards swapped under the finger, 日志 went nowhere or to the other device)
  pageI18n.global.locale.value = "zh";
  const card = (device: string, received_to: string) => ({ ...live, device, received_to });
  status = {
    recording: true,
    devices: [card("LR-P3-0002", "2026-01-01T10:00:02"), card("LR-N4-0001", "2026-01-01T10:00:00")],
  };
  const root = node("root");
  const app = renderer.createApp(Page);
  app.component("RouterLink", { template: "", render: () => null });
  app.mount(root);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await nextTick();
  const order = () => sections(root).flatMap((c) => text(c).match(/LR-\w+-\d+/) ?? []);
  const before = order();
  assert.deepEqual(before, ["LR-N4-0001", "LR-P3-0002"]);
  sockets.at(-1)!.onmessage!({
    data: JSON.stringify({
      type: "status",
      recording: true,
      devices: [
        card("LR-P3-0002", "2026-01-01T10:00:02"),
        card("LR-N4-0001", "2026-01-01T10:00:04"),
      ],
    }),
  });
  await nextTick();
  assert.deepEqual(order(), before);
  app.unmount();
} finally {
  await server.close();
}
console.log("progress check ok: import audio progress and live delay, zh/en page rendering");
