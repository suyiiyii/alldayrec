// node --experimental-strip-types web/api.check.ts: a failed call says its HTTP status, 0 when nothing answered. The
// conversation page shows 找不到这段对话 on a 404 only; any other failure is a load to try again.
import assert from "node:assert/strict";
import { ApiError, caseText, get } from "./src/lib/api.ts";
import { i18n } from "./src/i18n.ts";

async function failure(answer: () => Promise<Response>) {
  globalThis.fetch = answer;
  try {
    await get("/api/conversation/c1");
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    return e.status;
  }
  assert.fail("no failure");
}
const reply = (status: number) => async () => Response.json({ detail: "no such conversation" }, { status });

assert.equal(await failure(reply(404)), 404);
assert.equal(await failure(reply(502)), 502);
assert.equal(await failure(async () => Promise.reject(new TypeError("Failed to fetch"))), 0);
// a Feishu bean's charging case, only when its status has one
i18n.global.locale.value = "zh";
assert.equal(caseText({ at: "", battery: 50, case_battery: 80 }), "充电仓 80%");
assert.equal(caseText({ at: "", battery: 50 }), "");
assert.equal(caseText(null), "");
console.log("api check ok");
