// The online demo (`vp build --mode demo`, served from the docs site under /demo/): the real pages with no server.
// - GETs read a recorded snapshot, snap/<the request>.json next to the page (fictional sample data);
//   search runs over the snapshot's conversations and their sentences as the server's does; what it keeps no file for
//   (a device's log, the calls) answers empty, with no request;
// - every write, AI button and setting answers 403 [demo]: the page says 这是在线体验，部署自己的实例后可用;
// - the clock stands at the snapshot's evening (snap/replay.json's now, the viewer's wall clock) and runs on from there,
//   never past that day: at 23:59 the page starts over (a day the snapshot lacks would leave the timeline empty);
// - /ws is a scripted socket: the snapshot's last conversation said again now, sentence by sentence, on a loop.
// Elsewhere under the same switch: no 网页录音, 连接手机 or 接入 AI (router.ts, Me.vue), no listening in or voice question,
// and a label on every page (AppHeader.vue).
import type { LiveState, Row, Status } from "./lib/api";
import { isoLocal } from "./lib/track";

const snap = (key: string) =>
  `${import.meta.env.BASE_URL}snap/${encodeURIComponent(key).replace(/[^\w.-]/g, "_")}.json`;
const realFetch = window.fetch.bind(window);
const L: {
  now: string;
  device: string;
  start: string;
  rows: Row[];
  topics: LiveState["topics"];
  recorders: Record<string, string>;
} = await (await realFetch(snap("replay"))).json();

// the clock: Date and Date.now from the snapshot's now on (a zone-less time: the viewer's own wall clock), held at
// 23:59 of that day (a sleeping laptop wakes past it) until the status tick below reloads the page
const RealDate = Date,
  off = RealDate.parse(L.now) - RealDate.now(),
  end = RealDate.parse(L.now.slice(0, 10) + "T23:59:00"),
  clock = () => Math.min(RealDate.now() + off, end);
window.Date = new Proxy(RealDate, {
  construct: (D, a) => Reflect.construct(D, a.length ? a : [clock()]),
  get: (D, k) => (k === "now" ? clock : Reflect.get(D, k)),
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const demo = () => json({ detail: "[demo] demo" }, 403); // api.ts says err.code.demo
// GETs the snapshot keeps no answer for: what a server with no such history says
const EMPTY = new Map<string, unknown>([
  ["device-log", { events: [], status: [], audio: [] }],
  ["upgrades", []],
  ["events", []],
  ["calls", { calls: [], balance: null }],
]);

// GET /api/search as db.search does it, over every closed conversation (limit 0): q in its summary (any case) or in
// one of its sentences (as written), every day's sentences fetched at the first q
const got = (key: string) => realFetch(snap(key)).then((r) => r.json());
let all: Promise<
  {
    start: string;
    end: string;
    device: string | null;
    participants: string[];
    summary?: Record<string, unknown> | null;
  }[]
>;
let spoken: Promise<Row[]>;
async function search(p: URLSearchParams) {
  all ??= got("search?limit=0");
  const [q, tag, who, after, before] = ["q", "tag", "who", "after", "before"].map(
    (k) => p.get(k) ?? "",
  );
  const words = (s: Record<string, unknown> | null | undefined) =>
    JSON.stringify([s?.title, s?.summary, s?.tags, s?.todos, s?.decisions]).toLowerCase();
  const n = Number(p.get("limit") ?? 30);
  const hits = q
    ? (
        await (spoken ??= got("days").then((days: string[]) =>
          Promise.all(days.map((d) => got(`sentences?date=${d}`))).then((a) => a.flat()),
        ))
      ).filter((r) => r.text?.includes(q))
    : [];
  return (await all)
    .filter(
      (c) =>
        (!after || c.start >= after) &&
        (!before || c.start < before) &&
        (!who || c.participants.includes(who)) &&
        (!tag || (c.summary?.tags as string[] | undefined)?.includes(tag)) &&
        (!q ||
          words(c.summary).includes(q.toLowerCase()) ||
          hits.some(
            (r) => r.start >= c.start && r.start <= c.end && (!c.device || r.source === c.device),
          )),
    )
    .slice(0, n > 0 ? n : undefined);
}

window.fetch = async (input, init) => {
  const u = new URL(input instanceof Request ? input.url : String(input), location.href);
  if (u.origin !== location.origin || !u.pathname.startsWith("/api/"))
    return realFetch(input, init);
  const path = u.pathname.slice(5);
  // the language: this browser's own (我的 keeps a pick in localStorage), no hint for a pick
  if (path === "lang") return json({ lang: navigator.language.startsWith("zh") ? "zh" : "en" });
  if ((init?.method ?? "GET") !== "GET") return demo();
  if (EMPTY.has(path)) return json(EMPTY.get(path));
  if (path === "app/latest" || path.endsWith("/halves")) return demo(); // no app build; 拆分's first step
  if (u.pathname === "/api/status") return json(status());
  if (u.pathname === "/api/search") return json(await search(u.searchParams));
  const q = [...u.searchParams].sort(([a], [b]) => (a < b ? -1 : 1));
  const key =
    decodeURIComponent(path) + (q.length ? "?" : "") + q.map(([k, v]) => `${k}=${v}`).join("&");
  const r = await realFetch(snap(key));
  return r.ok ? r : demo();
};

// The conversation going on: its rows before now are final when a page opens; the rest are said from then on, each as
// it starts (a partial growing over its own length, then a draft, finals five at a time), the pauses at most 1.5 s.
// Said through, it starts again a few seconds later as a new conversation. Every socket hears the same one.
const DEV = L.device,
  sleep = (ms: number) => new Promise((ok) => setTimeout(ok, ms)),
  ms = (iso: string) => RealDate.parse(iso);
let finals = L.rows.filter((r) => r.start < L.now).map((r) => ({ ...r, state: "final" })),
  drafts: Row[] = [],
  partial: Row | null = null,
  convStart = L.start,
  topics = L.topics.filter((t) => t.start < L.now);
const said = () => [...finals, ...drafts].filter((r) => r.start >= convStart);
const lastEnd = () => [...finals, ...drafts].at(-1)?.end ?? convStart;
const talk = () => ({
  start: convStart,
  end: lastEnd(),
  sentences: said().length,
  speech_s: Math.round(said().reduce((s, r) => s + (ms(r.end) - ms(r.start)) / 1000, 0)),
});
function status(): Status {
  const now = isoLocal(Date.now()),
    conversation = talk();
  // the recorder as its phone reads it: a made-up battery and card (the snapshot keeps no device history)
  const hw = {
    at: now,
    battery: 76,
    charge: "battery" as const,
    store: 0,
    free_kb: 27e6,
    total_kb: 30.4e6,
    rssi: -61,
  };
  const d = {
    hw,
    device: DEV,
    audio_end: now,
    received_to: now,
    done_to: finals.at(-1)?.end ?? convStart,
    left_s: 0,
    rate: null,
    eta_s: null,
    recording: true,
    queued: 0,
    latency_s: 1.6,
    latency_med_s: 1.8,
    drafting: true,
    conversation,
  };
  return {
    device: DEV,
    recording: true,
    behind_s: 0,
    queued: 0,
    latency_s: 1.6,
    latency_med_s: 1.8,
    drafting: true,
    conversation,
    devices: [d],
    recorders: Object.entries(L.recorders).map(([device, end]) => ({
      device,
      audio_end: device === DEV ? now : end,
      hw: device === DEV ? hw : null,
    })),
  };
}
const live = () => ({
  type: "live",
  device: DEV,
  // a second behind what was said: 立即更新 stays pressable (and answers with the hint)
  live: {
    device: DEV,
    ...talk(),
    speech_s: Math.max(0, talk().speech_s - 1),
    at: isoLocal(Date.now()),
    upto: lastEnd(),
    topics,
  },
});

type Listener = (m: unknown) => void;
const ears = new Set<Listener>();
const emit = (m: unknown) => ears.forEach((f) => f(m));
const final = () => {
  if (!drafts.length) return;
  const rows = drafts.map((r) => ({ ...r, state: "final" }));
  finals = finals.concat(rows);
  drafts = [];
  emit({ type: "final", device: DEV, from: rows[0].start, to: rows.at(-1)!.end, rows });
};
setInterval(() => {
  if (clock() >= end) location.reload();
  emit({ type: "status", ...status() });
  emit(live());
}, 2000);
void (async () => {
  let rows = L.rows.filter((r) => r.start >= L.now);
  for (;;) {
    for (const [i, r] of rows.entries()) {
      const start = isoLocal(Date.now()),
        len = ms(r.end) - ms(r.start),
        text = r.text ?? "";
      const t = L.topics.find((t) => t.start === r.start);
      if (t) {
        topics = [...topics, { ...t, start }];
        emit(live());
      }
      for (let k = 2; k < text.length + 2; k += 2) {
        partial = { id: "p" + start, start, end: start, text: text.slice(0, k), source: DEV };
        emit({ type: "partial", device: DEV, row: partial });
        await sleep(Math.min(Math.max(len, 600), 6000) / Math.ceil(text.length / 2));
      }
      partial = null;
      const row = {
        ...r,
        id: "f" + start.replace(/\D/g, ""),
        start,
        end: isoLocal(ms(start) + len),
        state: "draft",
      };
      drafts.push(row);
      emit({ type: "draft", device: DEV, rows: [row] });
      if (drafts.length >= 5) final();
      await sleep(Math.min(rows[i + 1] ? ms(rows[i + 1].start) - ms(r.end) : 0, 1500) + 300);
    }
    final();
    await sleep(8000);
    convStart = isoLocal(Date.now());
    topics = [];
    rows = L.rows;
  }
})();

// A socket to /ws hears the conversation: on opening, where it is (status, analysis, rows so far); then each step
const RealWS = window.WebSocket;
class DemoSocket {
  static OPEN = 1;
  readyState = 0;
  onopen: ((e: unknown) => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: unknown) => void) | null = null;
  private ear: Listener = (m) => this.onmessage?.({ data: JSON.stringify(m) });
  constructor(url: string | URL, protocols?: string | string[]) {
    if (new URL(url, location.href).pathname !== "/ws") return new RealWS(url, protocols) as never; // vite's own
    setTimeout(() => {
      if (this.readyState) return;
      this.readyState = 1;
      this.onopen?.({});
      this.ear({ type: "status", ...status() });
      this.ear(live());
      if (finals.length)
        this.ear({
          type: "final",
          device: DEV,
          from: finals[0].start,
          to: finals.at(-1)!.end,
          rows: finals,
        });
      if (drafts.length) this.ear({ type: "draft", device: DEV, rows: drafts });
      if (partial) this.ear({ type: "partial", device: DEV, row: partial });
      ears.add(this.ear);
    }, 50);
  }
  send() {}
  close() {
    this.readyState = 3;
    ears.delete(this.ear);
  }
}
window.WebSocket = DemoSocket as unknown as typeof WebSocket;
