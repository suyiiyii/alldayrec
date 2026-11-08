// Real SFC callers: live full/mini and linked/unlinked recap prose preserve product names; a caption row's speaker
// opens the speaker sheet only where the page gives it one (who), and the sheet lists the labels to rename.
import assert from "node:assert/strict";
import { createServer } from "vite-plus";
import vue from "@vitejs/plugin-vue";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => null, setItem() {}, removeItem() {} },
  configurable: true,
});
// lib/audio.ts (TranscriptRow's ▶) listens on both as it loads
for (const name of ["document", "window"])
  Object.defineProperty(globalThis, name, {
    value: { addEventListener() {} },
    configurable: true,
  });
Object.defineProperty(globalThis, "matchMedia", {
  value: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  configurable: true,
});
const server = await createServer({
  configFile: false,
  plugins: [vue()],
  server: { middlewareMode: true },
  define: {
    __VUE_I18N_FULL_INSTALL__: true,
    __VUE_I18N_LEGACY_API__: false,
    __INTLIFY_PROD_DEVTOOLS__: false,
  },
});
try {
  const { i18n } = await server.ssrLoadModule("/src/i18n.ts");
  const { default: LivePanel } = await server.ssrLoadModule("/src/components/LivePanel.vue");
  const { default: RecapCard } = await server.ssrLoadModule("/src/components/RecapCard.vue");
  const { default: TranscriptRow } = await server.ssrLoadModule("/src/components/TranscriptRow.vue");
  const { default: SpeakerSheet } = await server.ssrLoadModule("/src/components/SpeakerSheet.vue");
  const text = "S1 uses Amazon S3 and Galaxy S24";
  const live = {
    device: "mic",
    start: "2026-10-26T10:00:00",
    upto: "2026-10-26T10:03:00",
    at: "2026-10-26T10:03:00",
    speech_s: 120,
    topics: [
      {
        title: text,
        start: "2026-10-26T10:00:00",
        points: [text],
        decisions: [],
        todos: [],
        open_questions: [],
      },
    ],
  };
  const status = {
    device: "mic",
    devices: [
      {
        device: "mic",
        recording: true,
        conversation: { start: live.start, end: live.upto, speech_s: 120 },
      },
    ],
  };
  const render = async (component: any, props: any) => {
    const app = createSSRApp(component, props);
    app.use(i18n);
    app.component("RouterLink", { props: ["to"], template: "<a><slot /></a>" });
    return await renderToString(app);
  };
  for (const lang of ["zh", "en"]) {
    i18n.global.locale.value = lang;
    for (const participants of [undefined, ["S1"]]) {
      const expected = participants ? (lang === "zh" ? "说话人 1" : "Speaker 1") : "S1";
      for (const mini of [false, true]) {
        const html = await render(LivePanel, { status, lives: [{ ...live, participants }], mini });
        assert.ok(html.includes(`${expected} uses Amazon S3 and Galaxy S24`), html);
        assert.ok(!html.includes("说话人 3") && !html.includes("Speaker 24"), html);
      }
      const html = await render(RecapCard, {
        open: true,
        recap: {
          date: "2026-10-26",
          conversations: 1,
          headline: text,
          todos: [],
          people: [],
          highlights: [
            { conv: "c1", text, participants },
            { conv: null, text },
          ],
        },
      });
      assert.ok(html.includes(`${expected} uses Amazon S3 and Galaxy S24`), html);
      assert.ok(
        html.includes("S1 uses Amazon S3 and Galaxy S24"),
        "unlinked/headline prose stays verbatim",
      );
    }
  }
  i18n.global.locale.value = "zh";
  const row = { id: "r1", start: "2026-10-26T10:00:00", end: "2026-10-26T10:00:02", speaker: "S2", state: "final", text: "嗯" };
  for (const who of [undefined, () => {}]) {
    const html = await render(TranscriptRow, { row, caption: true, who });
    assert.ok(html.includes("row caption") && html.includes("说话人 2"), html);
    assert.equal(html.includes('class="name"'), !!who, html);
  }
  const sheet = await render(SpeakerSheet, {
    conv: { id: "c1", participants: ["我", "S2", "S?", "顾明哲"], rows: [row] },
    sug: null,
    reload: async () => {},
  });
  assert.ok(sheet.includes("说话人 2") && sheet.includes("顾明哲"), sheet);
  assert.ok(!sheet.includes("未识别") && !/chip[^>]*>[^<]*<[^>]*>我</.test(sheet), sheet);
  console.log("speaker callers ok");
} finally {
  await server.close();
}
