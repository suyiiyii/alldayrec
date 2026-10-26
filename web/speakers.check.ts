// node --experimental-strip-types speakers.check.ts: speakerColor, initial, speakerList (src/lib/speakers.ts), both languages
import assert from "node:assert/strict";
import { i18n } from "./src/i18n.ts";
import {
  initial,
  labelText,
  likeness,
  speakerColor,
  speakerLabel,
  speakerList,
} from "./src/lib/speakers.ts";

assert.equal(speakerColor("我"), "var(--accent)");
for (const s of ["S2", "S?", "", null, undefined]) assert.equal(speakerColor(s), "var(--muted)");
assert.equal(speakerColor("同事A"), speakerColor("同事A"));
assert.match(speakerColor("同事A"), /^#[0-9a-f]{6}$/);
assert.equal(
  new Set(["妈妈", "同事A", "同事B", "室友", "小王", "Alice"].map(speakerColor)).size > 1,
  true,
);
assert.deepEqual(["同事A", "alice", "S2", "S?", "𠮷野", null].map(initial), [
  "同",
  "A",
  "2",
  "?",
  "𠮷",
  "?",
]);
assert.deepEqual(["S2", "S12", "S?", "我", "同事A", null].map(speakerLabel), [
  "说话人 2",
  "说话人 12",
  "未识别",
  "我",
  "同事A",
  "—",
]);
assert.deepEqual([0.72, 0.7, 0.69].map(likeness), ["很像", "很像", "有点像"]);
assert.deepEqual(
  [[], ["S1"], ["S1", "我"], ["S1", "S2", "我"], ["S1", "S2", "S3"]].map(speakerList),
  ["", "说话人 1", "我、说话人 1", "我、说话人 1 等 3 人", "说话人 1、说话人 2 等 3 人"],
);
assert.equal(
  labelText("我和 S1 聊了猫，S12说S?没听清；S3 存储和 Galaxy S24 不是人，S1x 也不是", [
    "我",
    "S1",
    "S12",
  ]),
  "我和 说话人 1 聊了猫，说话人 12说未识别没听清；S3 存储和 Galaxy S24 不是人，S1x 也不是",
);
assert.equal(labelText("S2 提醒水开了"), "S2 提醒水开了", "no participant context: verbatim");
i18n.global.locale.value = "en"; // 我 stays the key in the data, the page says Me
assert.deepEqual(["S2", "S?", "我", "同事A"].map(speakerLabel), [
  "Speaker 2",
  "Unidentified",
  "Me",
  "同事A",
]);
assert.deepEqual(["我", "alice"].map(initial), ["M", "A"]);
assert.equal(speakerList(["S1", "S2", "我"]), "Me, Speaker 1 and 1 more");
assert.equal(
  labelText("S1 and I agreed; Amazon S3 stays", ["S1"]),
  "Speaker 1 and I agreed; Amazon S3 stays",
);
assert.equal(speakerColor("我"), "var(--accent)");
console.log("speakers ok");

// AI requests keep attribution in the existing text field; the conversation card calls labelText.
// Old summaries have no speaker prefix and stay readable without being assigned a new speaker.
for (const lang of ["zh", "en"] as const) {
  i18n.global.locale.value = lang;
  const requests =
    lang === "zh"
      ? ["S1 让 AI 查测试日志", "Alex 让 AI 翻译错误信息", "我让 AI 列出失败测试", "列出失败测试"]
      : [
          "S1 asks AI to check test logs",
          "Alex asks AI to translate errors",
          "I ask AI to list failing tests",
          "List failing tests",
        ];
  const rendered = requests.map((text) => labelText(text, ["S1", "Alex", "我"]));
  assert.equal(
    rendered[0],
    lang === "zh" ? "说话人 1 让 AI 查测试日志" : "Speaker 1 asks AI to check test logs",
  );
  assert.deepEqual(rendered.slice(1), requests.slice(1));
}
