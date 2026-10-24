// node --experimental-strip-types i18n.check.ts: src/locales/zh.json and en.json have the same keys, every message
// compiles in vue-i18n, and every key the pages name literally is there: t("a.b"), $t('a.b'), TodoItem's tr("a.b"),
// <I18nT keypath="a.b">
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { i18n, t } from "./src/i18n.ts";
import en from "./src/locales/en.json" with { type: "json" };
import zh from "./src/locales/zh.json" with { type: "json" };

const keys = (o: object, pre = ""): string[] =>
  Object.entries(o).flatMap(([k, v]) => (typeof v === "string" ? [pre + k] : keys(v, `${pre}${k}.`)));
const zhKeys = keys(zh);
assert.deepEqual(keys(en).sort(), [...zhKeys].sort(), "zh.json and en.json differ in keys");

const warned: unknown[] = [];
console.warn = console.error = (...a: unknown[]) => warned.push(a.join(" "));
for (const l of ["zh", "en"] as const) {
  i18n.global.locale.value = l;
  for (const k of zhKeys) t(k);
}
assert.deepEqual(warned, [], "a message vue-i18n cannot read ({, }, @, | and $ are its syntax: {'@'})");

const src = (d: string): string[] =>
  readdirSync(d, { withFileTypes: true }).flatMap((f) =>
    f.isDirectory() ? src(`${d}/${f.name}`) : /\.(vue|ts)$/.test(f.name) ? [`${d}/${f.name}`] : [],
  );
const used = src("src").flatMap((f) =>
  [...readFileSync(f, "utf8").matchAll(/\btr?\(\s*["'`]([\w.]+)["'`]|keypath="([\w.]+)"/g)].map((m) => [
    f,
    m[1] ?? m[2],
  ]),
);
const missing = used.filter(([, k]) => !zhKeys.includes(k));
assert.deepEqual(missing, [], "keys the pages use that locales/zh.json lacks");
console.log(`i18n ok: ${zhKeys.length} keys, ${used.length} uses`);
