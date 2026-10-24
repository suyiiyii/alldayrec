// The recorder's event log (firmware/devkit/src/lr_log.h), put in words as lr_sync.py's `log` does; kept in step with
// lr_log.h by hand: a code it does not know shows its raw arguments.
import { intl, t } from "../i18n.ts";
import type { Upgrade } from "./api";

export interface Rec {
  boot: number;
  t: number; // the codec frame, or uptime ms when code has UPTIME
  code: number;
  x: number;
  a: number;
  b: number;
}
export const UPTIME = 0x80;
export const BOOT = 1,
  FAULT = 2,
  DISCONNECT = 10,
  DFU = 21,
  HEARTBEAT = 22,
  SD_WRITES = 25,
  POWER = 29;

// the reset reasons' bits; each its words in locales/*.json (rec.reset)
const RESETREAS: [number, string][] = [
  [0, "RESETPIN"],
  [1, "DOG"],
  [2, "SREQ"],
  [3, "LOCKUP"],
  [16, "OFF"],
  [17, "LPCOMP"],
  [18, "DIF"],
  [19, "NFC"],
  [20, "VBUS"],
];
// the HCI disconnect reasons with words in rec.hci; the SECURITY (0..9) and FAULTS (0..4) codes: rec.security, rec.fault
const HCI = new Set([0x05, 0x06, 0x08, 0x13, 0x14, 0x15, 0x16, 0x1a, 0x1f, 0x22, 0x28, 0x2a, 0x3b, 0x3d, 0x3e]);
const security = (v: number) => (v >= 0 && v <= 9 ? t(`rec.security.${v}`) : String(v));
const fault = (v: number) => (v >= 0 && v <= 4 ? t(`rec.fault.${v}`) : String(v));
const ERRNO: Record<number, string> = {
  1: "EPERM",
  2: "ENOENT",
  5: "EIO",
  11: "EAGAIN",
  12: "ENOMEM",
  13: "EACCES",
  16: "EBUSY",
  19: "ENODEV",
  22: "EINVAL",
  28: "ENOSPC",
  77: "EBADMSG",
  105: "ENOBUFS",
  116: "ETIMEDOUT",
  120: "EALREADY",
  128: "ENOTCONN",
  134: "ENOTSUP",
};

const hex = (v: number, n: number) => v.toString(16).padStart(n, "0");
const err = (v: number) => {
  const s = v | 0; // an errno, negative
  return s ? `${s}${ERRNO[-s] ? " -" + ERRNO[-s] : ""}` : t("rec.ok");
};
const reas = (v: number) =>
  RESETREAS.filter(([b]) => (v >>> b) & 1)
    .map(([, n]) => t(`rec.reset.${n}`))
    .join("+") || t("rec.powerOn");
const param = (a: number, b: number) =>
  t("rec.param", { interval: (a & 0xffff) * 1.25, latency: a >>> 16, timeout: b * 10 });
// USB_DISK x 0's b (lr_log.h): why the card came back, rec.back.1..3 (0: firmware older than this field)
const back = (v: number) => (v >= 1 && v <= 3 ? t(`rec.back.${v}`) : String(v));
// APPEND_ERR's x (lr_log.h): which part of an append failed, rec.append.1..3
const append = (v: number) => (v >= 1 && v <= 3 ? t(`rec.append.${v}`) : "");
const stack = (v: number) => (v === 0xffff ? "-" : String(v));
const phy = (v: number) => ({ 1: "1M", 2: "2M", 4: "Coded" })[v] ?? String(v);
const clock = (ms: number) => new Date(ms).toLocaleString(intl(), { hour12: false });
const kb = (b: number) => (b / 1024).toFixed(1);
function sdWrites(r: Rec) {
  const n = [r.b & 0x3ff, (r.b >>> 10) & 0x3ff, (r.b >>> 20) & 0x3ff, r.x];
  return t("rec.sdWrites", {
    n: n[0] + n[1] + n[2] + n[3],
    ms: r.a & 0xfffff,
    max: r.a >>> 20,
    a: n[0],
    b: n[1],
    c: n[2],
    d: n[3],
  });
}
const peak = (p: number) =>
  p ? `${Math.round(20 * Math.log10(p / 256))} dBFS` : t("rec.quiet");
const ascii = (...c: number[]) => String.fromCharCode(...c.map((v) => v & 0xff));
function health(r: Rec) {
  return t("rec.health", {
    temp: (r.x << 24) >> 24,
    zero: r.a & 0xfffff,
    pdm: r.a >>> 20,
    full: r.b & 0xffffff,
    peak: peak(r.b >>> 24),
  });
}
function cardId(r: Rec) {
  return t("rec.cardId", {
    maker: hex(r.x, 2),
    oem: ascii(r.b >>> 8, r.b >>> 16),
    name: ascii(r.a, r.a >>> 8, r.a >>> 16, r.a >>> 24, r.b),
    ver: `${r.b >>> 28}.${(r.b >>> 24) & 0xf}`,
  });
}
const cardSize = (r: Rec) =>
  t("rec.cardSize", {
    kind: r.x ? "SDHC/SDXC" : "SDSC",
    gb: ((r.a * 512) / 1e9).toFixed(2),
    sectors: r.a,
    year: 2000 + (r.b >>> 4),
    month: r.b & 0xf,
  });
const sdErrors = (r: Rec) =>
  t("rec.sdErrors", {
    crc: r.a & 0xffff,
    timeout: r.a >>> 16,
    busy: r.b & 0xffff,
    other: r.b >>> 16,
    retry: r.x,
  });
function readDone(r: Rec) {
  const ms = r.b & 0x3fffff;
  return t("rec.readDone", {
    kb: kb(r.a),
    s: (ms / 1000).toFixed(1),
    rate: kb((r.a / Math.max(ms, 1)) * 1000),
    pkt: r.b >>> 22,
    ci: r.x * 1.25,
  });
}

// code: [name, its arguments in words]; what it means is rec.ev.<name>
const EVENTS: Record<number, [string, (r: Rec) => string]> = {
  1: [
    "BOOT",
    (r) =>
      t("rec.boot", {
        reas: reas(r.a),
        mv: r.x * 20,
        fw: hex(r.b & 0xfffffff, 7) + ((r.b >>> 28) & 1 ? "+" : ""),
      }),
  ],
  2: ["FAULT", (r) => t("rec.faultText", { fault: fault(r.a), pc: hex(r.b, 8) })],
  3: [
    "MOUNT",
    // no answer (ETIMEDOUT) or no device: no card, or one not seated (LR-P3-2420 without one, 10-07)
    // x 0: a failed card tried again (the store's retry_card), said when it mounts or a reset try fails
    (r) =>
      t("rec.mount", { try: r.x ? t("rec.try", { n: r.x }) : t("rec.retry"), err: err(r.a) }) +
      ([-116, -19].includes(r.a | 0) ? t("rec.noCard") : ""),
  ],
  4: [
    "STORE",
    (r) =>
      t("rec.store", {
        batch: r.a,
        file: hex(r.a, 4).toUpperCase(),
        mb: Math.floor(r.b / 1024),
      }) +
      (r.x & 1 ? t("rec.storeUnread") : "") +
      (r.x & 2 ? t("rec.storeUnwritten") : ""),
  ],
  5: ["SLOW", (r) => t("rec.slow", { ms: r.a, b: r.b })],
  6: ["APPEND_ERR", (r) => t("rec.appendErr", { why: append(r.x), err: err(r.a), b: r.b })],
  7: ["CARD_FULL", (r) => t(r.x ? "rec.fullStop" : "rec.fullResume", { kb: r.a })],
  8: [
    "USB_DISK",
    (r) =>
      r.x
        ? t("rec.lent")
        : t(r.b ? "rec.backWhy" : "rec.backErr", { why: back(r.b), err: err(r.a) }),
  ],
  9: ["CONNECT", (r) => param(r.a, r.b)],
  10: [
    "DISCONNECT",
    (r) => `0x${hex(r.x, 2)} ${HCI.has(r.x) ? t(`rec.hci.x${hex(r.x, 2)}`) : ""}`.trim(),
  ],
  11: ["PARAM", (r) => param(r.a, r.b)],
  12: [
    "MTU",
    (r) => t("rec.mtu", { what: r.x ? t("rec.dataLength") : "ATT MTU", tx: r.a, rx: r.b }),
  ],
  13: ["PHY", (r) => t("rec.phy", { tx: phy(r.a), rx: phy(r.b) })],
  14: ["TIME", (r) => clock(r.b * 2 ** 32 + r.a)],
  15: [
    "SECURITY",
    (r) => t("rec.level", { n: r.x }) + (r.a ? t("rec.levelWhy", { why: security(r.a) }) : ""),
  ],
  16: ["PAIRED", (r) => t(r.x ? "rec.bonded" : "rec.unbonded")],
  17: ["PAIR_FAIL", (r) => security(r.x)],
  18: ["FORGET", (r) => t(r.x ? "rec.forgetExpired" : "rec.forgetAsked", { n: r.a }, r.a)],
  19: ["DROPPED", (r) => t("rec.dropped", { n: r.a, total: r.b })],
  20: ["SEGMENT", (r) => `${hex(r.a, 8).toUpperCase()}.BIN`],
  21: [
    "DFU",
    (r) => ({ 0x57: "UF2 bootloader", 0xa8: "OTA bootloader" })[r.x] ?? `0x${hex(r.x, 2)}`,
  ],
  22: [
    "HEARTBEAT",
    (r) => t("rec.heartbeat", { mv: r.x * 20, mb: Math.floor(r.a / 1024), n: r.b }),
  ],
  23: [
    "STACKS",
    (r) =>
      t("rec.stacks", {
        codec: stack(r.a >>> 16),
        pusher: stack(r.a & 0xffff),
        store: stack(r.b >>> 16),
        rx: stack(r.b & 0xffff),
      }),
  ],
  24: [
    "SD_RESET",
    (r) => t(r.b === 1 ? "rec.sdIdle" : "rec.sdDead", { a: hex(r.a, 2), b: hex(r.b, 2) }),
  ],
  25: ["SD_WRITES", sdWrites],
  26: ["READ", readDone],
  27: ["READ_TIME", (r) => t("rec.readTime", { read: r.a, send: r.b })],
  28: [
    "SWITCH",
    (r) =>
      t(r.x ? "rec.switchOn" : "rec.switchOff", {
        what: r.a >= 0 && r.a <= 3 ? t(`rec.switch.${r.a}`) : r.a,
      }),
  ],
  29: [
    "POWER",
    (r) => (r.a >= 0 && r.a <= 2 ? t(`rec.power.${r.a}`, { mv: r.x * 20 }) : `${r.a}`),
  ],
  30: ["LIVE_LOST", (r) => t(r.x ? "rec.lostDown" : "rec.lostStuck", { n: r.a, b: r.b }, r.a)],
  31: ["HEALTH", health],
  32: ["CARD_ID", cardId],
  33: ["CARD_SIZE", cardSize],
  34: ["SD_ERRORS", sdErrors],
};
// every few minutes or every connection: hidden unless asked for
const ROUTINE = new Set([9, 11, 12, 13, 14, 15, 20, 22, 23, 25, 32, 33]);

export interface Said {
  name: string;
  label: string;
  text: string;
  level: "" | "warn" | "bad";
  routine: boolean;
}

export function describe(r: Rec): Said {
  const code = r.code & ~UPTIME;
  const [name, fmt] = EVENTS[code] ?? [`0x${hex(code, 2)}`, () => `x ${r.x} a ${r.a} b ${r.b}`];
  const label = t(EVENTS[code] ? `rec.ev.${name}` : "rec.ev.unknown");
  const bad =
    code === FAULT ||
    code === 6 ||
    code === 17 ||
    code === 19 ||
    (code === 3 && r.a !== 0) ||
    (code === 7 && r.x !== 0) ||
    (code === 24 && r.b !== 1) ||
    (code === 29 && r.a !== 0) || // the power-fail warning, a low battery's System OFF
    (code === BOOT && (r.a & 0b1010) !== 0); // the last boot ended by the watchdog or a lockup
  const warn =
    [BOOT, DISCONNECT, DFU, 5, 8, 18, 30, 34].includes(code) ||
    (code === 31 && (r.a & 0xfffff) + (r.a >>> 20) > 0); // the mic gave all-zero frames, or PDM errors
  return {
    name,
    label,
    text: fmt(r) + (r.code & UPTIME ? t("rec.uptime", { s: (r.t / 1000).toFixed(1) }) : ""),
    level: bad ? "bad" : warn ? "warn" : "",
    routine: ROUTINE.has(code) || (code === 29 && !r.a) || (code === 31 && !warn),
  };
}

/** The battery mV a BOOT, HEARTBEAT or POWER record carries (0: not read, as in the power-fail warning). */
export const millivolts = (r: Rec) =>
  [BOOT, HEARTBEAT, POWER].includes(r.code & ~UPTIME) ? r.x * 20 : 0;

/** The share of its 10 minutes the card spent on appends (%), by an SD_WRITES record; null for any other. */
export const cardBusy = (r: Rec) =>
  (r.code & ~UPTIME) === SD_WRITES ? Math.round((r.a & 0xfffff) / 600) / 10 : null;

const span = (ms: number) =>
  ms < 60_000
    ? t("common.sec", { n: Math.floor(ms / 1000) })
    : t("common.minSec", { m: Math.floor(ms / 60_000), s: Math.floor(ms / 1000) % 60 });
const hash = (v: string | null) => v?.split("-").pop() ?? "?"; // revisions are YYYYMMDD.HHMMSS-<hash>

/** A firmware update in a line: 1751f88 → c95516b，用时 3 分 11 秒，成功. */
export function upgradeText(u: Upgrade): string {
  const where = ["start", "boot", "send"].includes(u.stage ?? "")
    ? t(`rec.up.${u.stage}`, { pct: u.pct })
    : "";
  const end =
    u.ok == null
      ? t("rec.up.then", { why: u.why ?? t("rec.up.noResult") })
      : u.ok
        ? t("rec.up.ok")
        : t("rec.up.failed") +
          (where ? t("rec.up.where", { where }) : "") +
          (u.why ? t("rec.up.why", { why: u.why }) : "");
  return (
    `${hash(u.from)} → ${hash(u.to)}${u.via === "usb" ? t("rec.up.usb") : ""}` +
    `${u.ms == null ? "" : t("rec.up.took", { span: span(u.ms) })}${end}` +
    (u.back_ms == null ? "" : t("rec.up.back", { span: span(u.back_ms) }))
  );
}
