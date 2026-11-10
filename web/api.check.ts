// node --experimental-strip-types web/api.check.ts: a failed call says its HTTP status, 0 when nothing answered. The
// conversation page shows 找不到这段对话 on a 404 only; any other failure is a load to try again.
import assert from "node:assert/strict";
import { ApiError, beanBehind, card, caseText, get, holdLeft, micGap, type DeviceStatus } from "./src/lib/api.ts";
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
// the recorder's card: blank (the app formats it), foreign (a GPT card, a damaged volume: a computer's job, never formatted)
assert.equal(card({ at: "", store: -19, total_kb: 0, card: "blank" }), "未格式化，可以在 App 的录音设备页格式化；在那之前只能实时上传");
assert.match(card({ at: "", store: -19, total_kb: 0, card: "foreign" }), /^无法识别/);
assert.equal(card({ at: "", store: -19, total_kb: 0, card: "none" }), "没插卡或没插好，只能实时上传");
const expired = { hold: null, hold_expired: { reason: "stale", seconds: 1200 } } as DeviceStatus;
assert.equal(holdLeft(expired), "补传等了 20 分钟没有进展，先定稿；之后到的更早录音会补转写");
i18n.global.locale.value = "en";
assert.equal(holdLeft(expired), "No backfill progress for 20 min, so finals went on; older audio arriving later will be transcribed afterwards.");
assert.equal(holdLeft({ hold: null } as DeviceStatus), "");
// a bean's recording not on its phone yet: held by the live page, until the bean stops, being read; none when stale
const now = Date.parse("2026-11-07T13:25:00");
const bean = (hw: object, at = "2026-11-07T13:24:30") => ({
  device: "D3200-0000",
  hw: { at, ...hw },
});
i18n.global.locale.value = "zh";
assert.equal(
  beanBehind(bean({ backlog_s: 2529, backlog_held: "live", backlog_eta_s: 1264 }), now),
  "D3200-0000 上还有 42 分钟录音没传到手机：此页开着时只传实时，关掉此页后开始补，约 21 分钟",
);
assert.equal(
  beanBehind(bean({ backlog_s: 120, backlog_held: "recording" }), now),
  "D3200-0000 上还有 2 分钟录音没传到手机，录音豆停录后补",
);
assert.equal(
  beanBehind(bean({ backlog_s: 2160, backlog_eta_s: 1080 }), now),
  "D3200-0000 上还有 36 分钟录音没传到手机，正在补，约 18 分钟",
);
assert.equal(beanBehind(bean({ backlog_s: 2160 }, "2026-11-07T13:20:00"), now), "");
assert.equal(beanBehind(bean({ battery: 50 }), now), "");
assert.equal(beanBehind({ device: "LR-P3-0000", hw: null }, now), "");
i18n.global.locale.value = "en";
assert.equal(
  beanBehind(bean({ backlog_s: 2160, backlog_eta_s: 1080 }), now),
  "36 min of D3200-0000's audio is still only on the bean; fetching it, about 18 min",
);
console.log("api check ok");
// a silent stretch of a phone recording with its microphone: why, in words; an unknown reason is the system's
i18n.global.locale.value = "zh";
const gap = { device: "p", kind: "mic", reason: "call", from: "2026-10-24T14:05:03.000", to: "2026-10-24T14:32:51.000" } as const;
assert.equal(micGap(gap), "14:05–14:32 手机没录到声音：通话中");
assert.equal(micGap({ ...gap, reason: "other_app", to: null }, true), "14:05 起麦克风被其他 App 占用");
assert.equal(micGap({ ...gap, reason: "new_one" }, true), "14:05–14:32 系统暂停了录音");
assert.equal(micGap({ ...gap, reason: "interrupted" }, true), "14:05–14:32 通话中，或麦克风被其他 App 占用");
