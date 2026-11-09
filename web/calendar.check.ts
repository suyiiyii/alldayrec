// node --experimental-strip-types calendar.check.ts: heard (src/lib/calendar.ts), as 人物 and a person's page show
// when someone was last heard, both languages
import assert from "node:assert/strict";
import { i18n } from "./src/i18n.ts";
import { heard, shiftDay, today } from "./src/lib/calendar.ts";

const at = (day: string) => `${day}T09:05:30`;
const old = shiftDay(today(), -2);
const words = { zh: ["今天 09:05", "昨天 09:05"], en: ["Today 09:05", "Yesterday 09:05"] };
for (const [l, [now, before]] of Object.entries(words) as [keyof typeof words, string[]][]) {
  i18n.global.locale.value = l;
  assert.equal(heard(at(today())), now);
  assert.equal(heard(at(shiftDay(today(), -1))), before);
  assert.equal(heard(at(old)), old.slice(5, 10));
}
assert.equal(shiftDay("2026-03-01", -1), "2026-02-28");
console.log("calendar.check ok");
