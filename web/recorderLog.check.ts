// Check of src/lib/recorderLog.ts against lr_log.h's packed events (the same values as lr_sync.py's selftest), and
// firmware updates in a line (the server's upgrades() as test_device_log.py has them):
//   node --experimental-strip-types web/recorderLog.check.ts
import { i18n } from "./src/i18n.ts";
import { cardBusy, describe, millivolts, upgradeText } from "./src/lib/recorderLog.ts";

const said = (code: number, x: number, a: number, b: number) =>
  describe({ boot: 1, t: 100, code, x, a, b }).text;
const check = (got: string, want: string) => {
  if (got !== want) throw new Error(`${got} != ${want}`);
};
check(
  said(25, 1, 601 | (250 << 20), 4 | (2 << 10) | (2 << 20)),
  "9 次，共 601 ms，最长 250 ms；<20 ms 4，20–49 ms 2，50–200 ms 2，>200 ms 1",
);
check(
  said(25, 0, (0xfffff | (0xfff << 20)) >>> 0, 0),
  "0 次，共 1048575 ms，最长 4095 ms；<20 ms 0，20–49 ms 0，50–200 ms 0，>200 ms 0",
); // a ≥ 2^31: unsigned
check(
  said(26, 12, 409600, (10000 | (491 << 22)) >>> 0),
  "400.0 KB，用时 10.0 s（40.0 KB/s），每包 491 B，连接间隔 15 ms",
);
check(said(27, 0, 3000, 6500), "读卡 3000 ms，蓝牙发送 6500 ms");
check(
  said(31, 0xf6, 230 | (3 << 20), (17 | (128 << 24)) >>> 0),
  "芯片 -10 °C，麦克风全 0 230 ms，PDM 错误 3 次，满幅样本 17 个，最响 -6 dBFS",
);
check(said(31, 31, 0, 0), "芯片 31 °C，麦克风全 0 0 ms，PDM 错误 0 次，满幅样本 0 个，最响 低于 -48 dBFS");
check(
  said(32, 3, 0x32334353, (0x47 | (0x53 << 8) | (0x44 << 16) | (0x80 << 24)) >>> 0),
  "厂商 0x03，OEM SD，型号 SC32G，版本 8.0",
);
check(said(3, 0, -116 >>> 0, 0), "重试：-116 -ETIMEDOUT（没插卡或没插好）");
check(said(3, 2, -116 >>> 0, 0), "第 2 次：-116 -ETIMEDOUT（没插卡或没插好）");
check(said(33, 1, 62333952, (23 << 4) | 7), "SDHC/SDXC，31.91 GB（62333952 扇区），2023 年 7 月生产");
check(said(34, 4, 2 | (5 << 16), 3 | (1 << 16)), "CRC 错 2 次，超时 5 次（其中忙 3 次），其他错 1 次，重试 4 次");
check(said(6, 3, -5, 4096), "文件长度没写上卡：-5 -EIO（4096 B）");
check(said(6, 0, -5, 512), "-5 -EIO（512 B）");
check(said(4, 3, 4212, 1810000), "音频批次 4212（文件 1074xxxx），剩 1767 MB；卡上的计数读不出；新计数没写进卡");  // a boot number counted before
const level = (code: number, a: number) => describe({ boot: 1, t: 100, code, x: 30, a, b: 0 });
check(String([level(31, 0).routine, level(31, 0).level]), "true,"); // a quiet 10 minutes: hidden
check(String([level(31, 230).routine, level(31, 230).level]), "false,warn"); // all-zero frames: shown
check(said(30, 0, 12, 4800), "12 条记录（4800 B）没能实时发出（蓝牙卡住），卡上照存，手机从卡里补");
check(said(30, 1, 3, 1100), "3 条记录（1100 B）没能实时发出（蓝牙断开），卡上照存，手机从卡里补");
const up = {
  at: "",
  app: true,
  from: "20261006.120000-1751f88",
  to: "20261007.023223-c95516b",
  why: null,
};
check(
  upgradeText({ ...up, app: false, from: "1751f88", to: "c95516b", ms: 192_000, ok: true }),
  "1751f88 → c95516b，用时 3 分 12 秒，成功",
);
check(
  upgradeText({ ...up, ms: 180_000, ok: true, back_ms: 30_000 }),
  "1751f88 → c95516b，用时 3 分 0 秒，成功，30 秒后连回",
);
check(
  upgradeText({ ...up, ms: 60_000, ok: false, why: "Device disconnected", stage: "send", pct: 40 }),
  "1751f88 → c95516b，用时 1 分 0 秒，失败（传输到 40%）：Device disconnected",
);
check(
  upgradeText({ ...up, ms: 20_500, ok: false, why: "连不上录音设备", stage: "start", pct: 0 }),
  "1751f88 → c95516b，用时 20 秒，失败（进入升级模式前）：连不上录音设备",
);
check(upgradeText({ ...up, from: null, ms: null, ok: null }), "? → c95516b，还没有结果");
check(
  upgradeText({
    ...up,
    app: false,
    from: "28758c4",
    to: "28758c4",
    ms: 60_000,
    ok: null,
    why: "重启后版本没变",
    via: "usb",
  }),
  "28758c4 → 28758c4（USB），用时 1 分 0 秒，重启后版本没变",
);

const rec = (code: number, x: number, a = 0) => ({ boot: 1, t: 100, code, x, a, b: 0 });
check(String([1, 22, 29].map((c) => millivolts(rec(c, 195)))), "3900,3900,3900"); // BOOT, HEARTBEAT, LR-P3's POWER
check(String(millivolts(rec(29, 0, 1))), "0"); // the power-fail warning: not measured
check(String(cardBusy(rec(25, 0, 84_000 | (713 << 20)))), "14"); // 84 s of 10 min (LR-P1, 10-05)
check(String(cardBusy(rec(22, 195))), "null");

i18n.global.locale.value = "en"; // the same records in English
check(said(3, 2, -116 >>> 0, 0), "try 2: -116 -ETIMEDOUT (no card, or not seated)");
check(said(30, 1, 3, 1100), "3 records (1100 B) not sent live (Bluetooth disconnected); kept on the card, the phone catches up from it");
check(describe({ boot: 1, t: 2500, code: 1 | 0x80, x: 195, a: 0b10, b: 0x1751f88 }).text,
  "last run ended by watchdog DOG, battery 3900 mV, firmware 1751f88 (2.5 s after boot)");
check(describe({ boot: 1, t: 0, code: 99, x: 1, a: 2, b: 3 }).label, "Unknown event");
check(
  upgradeText({ ...up, ms: 60_000, ok: false, why: "Device disconnected", stage: "send", pct: 40, back_ms: 30_000 }),
  "1751f88 → c95516b, took 1 min 0 s, failed (at 40% of the transfer): Device disconnected, reconnected 30 s later",
);
check(upgradeText({ ...up, from: null, ms: null, ok: null }), "? → c95516b, no result yet");
console.log("recorderLog check ok");
