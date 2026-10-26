import { i18n, t } from "../i18n.ts";

export interface Row {
  id: string;
  start: string;
  end: string;
  speaker?: string;
  speaker_confidence?: number | null;
  text?: string;
  state?: string;
  error?: string;
  dictation?: string;
  music?: number; // 1: the server heard music here (a song played aloud): no voiceprint, speaker S?
  source?: string; // the device that recorded it
}

export interface Span {
  from: string; // starts of the first and the last row, both included
  to: string;
}
export const inSpans = (r: Row, spans?: Span[]) =>
  !!spans?.some((x) => x.from <= r.start && r.start <= x.to);
export const MOSTLY_MEDIA = 0.8; // a conversation this much media is greyed as a whole
export const MIN_SPEECH_S = 60; // server summary.MIN_SPEECH_S: a conversation is analyzed by itself from this much speech on
// a fragment: too little speech for a summary (its title is its first sentence); the time line folds a day's away
export const isFragment = (c: Conversation) =>
  (c.speech_s ?? Infinity) < MIN_SPEECH_S && (c.summary?.media_share ?? 0) < MOSTLY_MEDIA;

// Rows in order, a run of FOLD or more media rows folded into one line; a shorter run stays, greyed
export type Seg = { k: "row"; r: Row; media: boolean } | { k: "fold"; key: string; rows: Row[] };
const FOLD = 3;
export function fold(rows: Row[], media: (r: Row) => boolean): Seg[] {
  const out: Seg[] = [];
  let run: Row[] = [];
  const flush = () => {
    if (run.length >= FOLD) out.push({ k: "fold", key: "m" + run[0].id, rows: run });
    else out.push(...run.map((r) => ({ k: "row" as const, r, media: true })));
    run = [];
  };
  for (const r of rows) {
    if (media(r)) run.push(r);
    else {
      flush();
      out.push({ k: "row", r, media: false });
    }
  }
  flush();
  return out;
}

export interface Feedback {
  conv: string;
  at: string;
  text: string;
}
// 完成 / 不对 on a todo (lib/todos.ts)
export type Mark = "done" | "wrong";

export interface At {
  at: string | null;
}
export interface Summary {
  conversation: string;
  status: "pending" | "done" | "failed";
  error?: string | null;
  title?: string;
  summary?: string;
  people?: { who: string; points: string[]; points_at?: (string | null)[] }[]; // points_at: each point's sentence, from prompt v9 on
  tags?: string[];
  topics?: (At & { title: string; points?: string[] })[];
  todos?: (At & {
    owner?: string;
    to?: string | null;
    text: string;
    due?: string | null;
    due_date?: string | null; // the due on the calendar (YYYY-MM-DD), from summary prompt v8 on
    quote?: string;
    id?: string; // in a conversation's summary: the todo's id (server db.todo_id) and its mark
    mark?: Mark | null;
  })[];
  ai_requests?: (At & { text: string })[];
  ai_spans?: Span[];
  media_spans?: Span[]; // media played aloud (video, TV, music): greyed and folded
  media_share?: number; // of the speech, the part in media_spans
  decisions?: (At & { text: string; quote?: string })[];
  model?: string | null;
  via?: "live"; // written by the conversation's live agent at its close
  created_at?: string;
}

export interface Conversation {
  id: string;
  start: string;
  end: string;
  state?: "open" | "closed";
  participants: string[];
  sentences: number;
  speech_s?: number; // seconds of speech in it
  device?: string | null; // its rows' source; none: from before per-device pipelines
  summary?: Summary | null;
}

export interface LiveTopic {
  id?: number; // the agent's handle; timelines kept before it have none
  title: string;
  start: string; // the row it starts at
  points: string[];
  decisions: string[];
  todos: { who?: string; what: string }[];
  open_questions: string[];
}
// the live analysis of an open conversation; kept as the conversation's timeline once it closes
export interface LiveState {
  participants?: string[]; // actual labels from the analyzed rows; absent: prose stays verbatim
  device: string;
  start: string;
  topics: LiveTopic[];
  at: string;
  upto: string;
  sentences: number;
  speech_s: number; // the conversation's speech it has seen
}

export interface DeviceStatus {
  device: string;
  audio_end: string | null;
  received_to: string | null;
  done_to: string | null;
  left_s: number;
  rate: number | null;
  eta_s: number | null;
  hold?: string | null; // finals wait here for older audio the phone is still sending
  hold_seen?: string | null; // when its phone last said the hold
  hold_until?: string | null; // absolute deadline: 12 h from this hold boundary's first set
  hold_expired?: { reason: "stale"; seconds: number } | null;
  recording?: boolean; // its newest audio came in the last 2 min: behind_s and latency_s are about now
  queued: number;
  latency_s: number | null; // its newest draft row's end to its showing (the final rows' while no page watches)
  latency_med_s?: number | null; // the same over its last 10 rows, the median
  final_latency_s?: number | null; // its newest final row's end to its showing
  drafting?: boolean; // a page watches: drafts come up
  refine?: { at: string; ms: number; error?: string; backlog_s?: number } | null;
  conversation?: { start: string; end: string; sentences: number; speech_s: number } | null; // the one going on
  analyzing?: boolean; // its live analysis is running
  hw?: RecorderStatus | null; // a BLE recorder, as its phone last read it
  arrival?: { at: number; lag_s: number } | null; // its newest audio: when it came (unix ms), how long after its end
  sound?: { s: number; zero_s: number; full: number } | null; // its last 10 min of audio: s of it, s in zero runs, full-scale samples
}

// a device with audio (Status.recorders, Status.imports): where its stored audio starts and ends, its recorder as its
// phone last read it
export type Recorder = Pick<DeviceStatus, "device" | "audio_end" | "hw"> & { audio_start?: string };

export type Charge = "battery" | "charging" | "full";
export const chargeText = (c: Charge) => t(`api.charge.${c}`);
// a Feishu bean's charging case, as the bean last read it: out of the case it keeps the level the case had then
export const caseText = (h?: RecorderStatus | null) =>
  h?.case_battery != null ? t("api.caseBattery", { n: h.case_battery }) : "";

export interface RecorderStatus {
  at: string;
  model?: string;
  fw?: string;
  battery?: number;
  charge?: Charge; // what it runs on (firmware lr_power.h); none from an older firmware
  store?: number; // 0 recording to the card; an errno otherwise: with total_kb 0 no card (-19, -116), -28 full, -16 lent
  free_kb?: number;
  total_kb?: number;
  backlog_kb?: number; // left on the card for the phone to read in the background
  backlog_eta_s?: number; // at the phone's own reading rate
  rssi?: number | null; // the link's signal, dBm
  case_battery?: number; // a Feishu bean's charging case (caseText)
  case_charging?: boolean; // that case on USB power
  // its phone (App 1.4.6 on): the build (1.4.6+21), system, model identifier; its battery, power saving and heat
  // (the system slows or ends apps then), the app's memory; this recorder's pieces waiting to go up and how uploads go
  app?: string;
  os?: string;
  phone?: string;
  phone_battery?: number | null;
  phone_charging?: boolean | null;
  low_power?: boolean;
  thermal?: string | null; // Android none..shutdown, iOS nominal..critical
  app_mb?: number;
  queue_n?: number;
  queue_kb?: number;
  queue_age_s?: number | null; // its oldest piece's start, s before `at`
  upload_fails?: number; // in a row
  upload_ok_s?: number | null; // the last upload the server took, s before `at` (null: none since the app started)
}

// the system's heat levels that slow or end apps, both platforms' words
const THERMAL: Record<string, "warm" | "hot" | "overheated"> = {
  light: "warm",
  fair: "warm",
  moderate: "hot",
  serious: "hot",
  severe: "overheated",
  critical: "overheated",
  emergency: "overheated",
  shutdown: "overheated",
};
export const build = (app?: string) => Number(app?.split("+")[1] ?? 0);
const clock = (h: RecorderStatus, s: number) =>
  new Date(Date.parse(h.at) - s * 1000).toTimeString().slice(0, 8);
// its phone as it said with the status: the build, system and model; battery, power saving, heat and the app's memory
export const phoneLine = (h: RecorderStatus) =>
  [`App ${h.app}`, h.os, h.phone].filter(Boolean).join(" · ");
export const phoneState = (h: RecorderStatus) =>
  [
    h.phone_battery != null &&
      t(h.phone_charging ? "api.phoneBatteryCharging" : "api.phoneBattery", { n: h.phone_battery }),
    h.low_power && t("api.lowPower"),
    h.thermal && THERMAL[h.thermal] && t(`api.thermal.${THERMAL[h.thermal]}`),
    h.app_mb != null && t("api.appMemory", { n: h.app_mb }),
  ]
    .filter(Boolean)
    .join(" · ");
export const hot = (h: RecorderStatus) => !!h.low_power || !!(h.thermal && THERMAL[h.thermal]);
// the recorder's pieces still on the phone and how uploads go, at the status's time; a piece waiting over 2 min is a
// backlog, as the app's notification says it (RecorderService.BACKLOG_MS)
export const queueLine = (h: RecorderStatus) =>
  [
    h.queue_n && (h.queue_age_s ?? 0) > 120
      ? t("api.queued", {
          n: h.queue_n,
          mb: ((h.queue_kb ?? 0) / 1024).toFixed(1),
          at: clock(h, h.queue_age_s ?? 0),
        })
      : t("api.noBacklog"),
    h.upload_fails && t("api.uploadFails", { n: h.upload_fails }, h.upload_fails),
    h.upload_ok_s != null && t("api.lastUpload", { at: clock(h, h.upload_ok_s) }),
  ]
    .filter(Boolean)
    .join(" · ");

// GET /api/device-log: a device's events and the status reports its phones sent, in a time range
export interface DeviceEvent {
  src: string; // "rec": a record of the recorder's own event log (lib/recorderLog.ts); else the app that wrote it
  seq: number;
  at: string | null; // null: the phone could not put it on the clock
  data: Record<string, unknown>;
}
export interface DeviceLog {
  events: DeviceEvent[];
  status: RecorderStatus[];
  audio: AudioMinute[];
}
// a minute of a device's audio, how it sounded and arrived (the server's audio.health() of each piece; no content)
export interface AudioMinute {
  t: string; // the minute
  s: number; // seconds of audio in it
  rms: number; // dBFS
  hum: number; // dBFS: mains hum, 50..250 Hz
  zero_s: number; // in runs of exact zeros >= 10 ms: the mic sent nothing
  full: number; // full-scale samples: clipping
  lag_s: number | null; // how long after its end it arrived (null: before 10-07)
}

// GET /api/upgrades: a device's firmware updates, an app's "dfu" events joined with the recorder's DFU and BOOT records
export interface Upgrade {
  at: string; // begun: the app's start, else the recorder's DFU
  app: boolean; // an app logged it (DfuLog); else only the recorder's records tell (lr_dfu.py, apps before 1.4.1)
  from: string | null; // the app's revisions, else the BOOT records' firmware hashes
  to: string | null;
  ms: number | null; // to done or fail, else to the recorder's next BOOT
  ok: boolean | null; // null: no end and no BOOT yet; with no app, back on the same firmware (why says so)
  why: string | null;
  via?: string | null; // the bootloader the recorder went to: ota (Bluetooth), usb (UF2, lr.sh flash)
  stage?: string; // where a failure was: start (before the bootloader), boot (in it), send (with pct)
  pct?: number;
  back_ms?: number; // the end to the app's first status of the recorder
}

export interface Status {
  balance?: Balance | null; // StepFun's prepaid account
  device?: string | null;
  behind_s?: number | null;
  queued?: number; // uploaded pieces the server has not fed yet, all devices'
  latency_s?: number | null; // to arrival: of the device whose audio came in last, as its DeviceStatus
  latency_med_s?: number | null;
  final_latency_s?: number | null;
  drafting?: boolean;
  arrival?: { at: number; lag_s: number } | null;
  recording?: boolean; // some device's audio came in the last 2 min; false: behind_s is how long ago it last did
  last_audio?: string | null; // where the newest stored audio of any device ends
  refine?: { at: string; ms: number; error?: string } | null;
  conversation?: { start: string; sentences: number } | null;
  engine?: string; // the speech engines: the final layer's, the drafts', the one a failed window goes to (engines())
  draft_engine?: string;
  fallback_engine?: string | null;
  commit?: string;
  devices?: DeviceStatus[]; // the devices whose pipeline runs
  recorders?: Recorder[]; // every device with audio, its newest recording first (我的)
  imports?: Recorder[]; // the batches 导入录音 sent (import-*), apart from the recorders
  summaries_pending?: number;
  lang?: "zh" | "en"; // what the models write in (GET/PUT /api/lang)
}

export const engines = (s: Status) =>
  [
    s.engine,
    s.draft_engine && t("api.draftEngine", { e: s.draft_engine }),
    s.fallback_engine && t("api.fallbackEngine", { e: s.fallback_engine }),
  ]
    .filter(Boolean)
    .join(" · ");

// GET /api/calls: the ASR and LLM calls since a time, by kind and engine, and the StepFun balance
export interface CallSum {
  kind: string; // final, draft (ASR); summary, hints, profile, recap, live, ask (LLM)
  engine: string;
  n: number;
  failed: Record<string, number>; // by HTTP code, else exception class
  ms50: number | null; // of the answered ones
  ms90: number | null;
  sent_h: number; // audio hours sent (ASR)
  prompt_tokens: number;
  completion_tokens: number;
}
export interface Balance {
  at: string;
  cny: number;
  per_day: number | null; // spent a day over the last 7 days, top-ups left out
  days_left: number | null;
}
export interface Calls {
  calls: CallSum[];
  balance: Balance | null;
}

// GET /api/recap: the newest daily recap (server recap.py, written each morning for the day before)
export interface Recap {
  date: string;
  conversations: number;
  headline: string;
  highlights: { text: string; conv: string | null; participants?: string[] }[];
  todos: TodoView[];
  people: { who: string; note: string }[];
}
// One of my todos with where it came from (server db.todo_view): GET /api/todos/due, a recap's todos
export interface TodoView {
  id?: string; // none: a recap's todo from before ids whose text names no todo of a summary (no marks then)
  mark?: Mark | null;
  conv: string;
  text: string; // the summary's
  short?: string; // a recap's wording
  to?: string | null;
  due?: string | null; // as said
  due_date?: string | null;
  at?: string | null; // when it was said
  quote?: string | null; // the sentence
  title?: string | null; // the conversation's, and its start
  start?: string;
  with?: string[]; // whom it is to, else the others in the conversation
  age_days?: number; // in the due list (Due)
  stale?: boolean;
}
// GET /api/todos/due: my todos due today or before (summary prompt v8 dates them), not marked done or wrong; age_days
// past due, stale: over 7 days, over 3 for one said as 待会儿, 晚上, 明天… (server db.SAME_DAY)
export interface Due extends TodoView {
  id: string;
  due_date: string;
  age_days: number;
  stale: boolean;
}

// a device's finals are within 30 s of its newest audio; one with no pipeline (a Recorder) has all its audio final
export const caughtUp = (d: { left_s?: number }) => (d.left_s ?? 0) < 30;
// its recorder's card: store is an errno; with nothing mounted this boot (total_kb 0) no card or one not seated (-19,
// -116: a mount that timed out), as the app's cardState says it
export const card = (h: RecorderStatus) =>
  h.store === -28
    ? t("api.card.full")
    : h.store === -16
      ? t("api.card.usb")
      : h.store && !h.total_kb
        ? t("api.card.none")
        : h.store
          ? t("api.card.error")
          : h.total_kb
            ? t("api.card.free", {
                free: ((h.free_kb ?? 0) / 1048576).toFixed(1),
                total: (h.total_kb / 1048576).toFixed(1),
              })
            : "—";

export interface LogEvent {
  at: string;
  text: string;
  kind: string;
}

// A failed request in words for the page (401, 5xx and no network said plainly; another refusal says the server's
// own detail when that is Chinese, many are English for scripts); what actually came back is in `detail`, for a title.
// A detail may start with a stable code, "[no_summaries] 那天没有摘要": the message leaves it out, `code` keeps it, and
// a code locales/*.json know (err.code.*) is said in the page's language
export class ApiError extends Error {
  detail: string;
  code: string;
  status: number; // 0: no answer (the network)
  constructor(message: string, detail: string, code = "", status = 0) {
    super(message);
    this.detail = detail;
    this.code = code;
    this.status = status;
  }
}
export const errorTitle = (e: unknown) => (e instanceof ApiError ? e.detail : "");

export async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(path, init);
  } catch (e) {
    throw new ApiError(t("err.network"), `${path}: ${e}`);
  }
  const j = await r.json().catch(() => ({}));
  if (r.ok) return j;
  const raw = typeof j.detail === "string" ? j.detail : "";
  const code = /^\[([a-z_]+)\] /.exec(raw)?.[1] ?? "";
  const said = code ? raw.slice(code.length + 3) : raw;
  throw new ApiError(
    r.status === 401
      ? t("err.auth")
      : r.status >= 500
        ? t("err.server")
        : code && i18n.global.te(`err.code.${code}`)
          ? t(`err.code.${code}`)
          : /\p{Script=Han}/u.test(said)
            ? said
            : t("err.http", { status: r.status }),
    `${path}: HTTP ${r.status}${raw && " " + raw}`,
    code,
    r.status,
  );
}

export function get<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const q = new URLSearchParams(params).toString();
  return call<T>(path + (q ? "?" + q : ""));
}

export function post<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
  return call<T>(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const hhmm = (iso: string) => iso.slice(11, 16);
// A persisted no-progress expiry, or a hold its phone has not said for 5 min: the phone went away with older audio still on the recorder's card
// (10-07: LR-P3-116C, unbound at 17:58); the queue waits for it until hold_until, then finalizes what it has
export const holdLeft = (d: DeviceStatus) =>
  d.hold_expired?.reason === "stale"
    ? t("api.holdStale", { minutes: d.hold_expired.seconds / 60 })
    : d.hold && d.hold_seen && Date.now() - +new Date(d.hold_seen) > 5 * 60_000
      ? t("api.holdLeft", { seen: hhmm(d.hold_seen), until: hhmm(d.hold_until ?? "") })
      : "";
export const hhmmss = (iso: string) => iso.slice(11, 19);
// a closed conversation's link text: its summary's title, or how the summary is going
export const convLink = (c: Conversation) =>
  c.summary?.status === "done"
    ? c.summary.title || t("api.view")
    : c.summary?.status === "failed"
      ? t("api.summaryFailed")
      : t("api.summaryPending");
export const minutes = (c: Conversation) =>
  Math.max(1, Math.round((+new Date(c.end) - +new Date(c.start)) / 60000));

export interface Voice {
  conv: string;
  label: string;
  start: string;
  n: number;
  speech_s: number;
  clip?: string | null;
  clip_start?: string | null;
  clip_end?: string | null;
  clip_text?: string | null;
  title?: string | null;
  points: string[];
  with: string[];
}
// GET /api/speakers: each group as its card shows it (GET /api/speakers/{id} has the group whole, its members too); a
// sample is a voice with its clip and first point
export type Sample = Pick<
  Voice,
  "conv" | "label" | "start" | "clip" | "clip_start" | "clip_end" | "clip_text" | "title" | "points"
>;
export interface VoiceSet {
  count: number;
  speech_s: number;
  last: string | null;
  samples: Sample[];
  co: string[];
  called: { name: string; n: number }[]; // what its voices were called (aliases resolved), in n conversations
}
export interface Speaker extends VoiceSet {
  id: string;
  name: string | null;
  suggested: (VoiceSet & { id: string; score: number })[];
}
// the people's cards and how many voices are folded into 其他声音; ?others=1 has the cards of those
export interface SpeakerList {
  cards: Speaker[];
  others: number;
}

export interface Todo {
  owner?: string;
  to?: string | null;
  text: string;
  due?: string | null;
  quote?: string;
  at: string | null;
  conversation: string;
  title?: string | null;
}
// GET /api/conversation/{id}/speakers: suggestions for its labels; each is applied only by a rename the owner clicks
export interface NameHint {
  who: string; // the label it was called
  name: string; // as said
  canon: string; // the person it is an alias of, else the name
  kind: "addressed" | "self" | "referred";
  by: string; // who said it
  at: string;
  quote: string;
}
export interface LabelSuggestions {
  asked: boolean; // the hints were asked for this conversation
  names: NameHint[];
  merge: { from: string; to: string; score?: number; name?: string }[];
  like: { who: string; name: string; score: number }[]; // voice near a named speaker
  elsewhere: { who: string; name: string; convs: number }[]; // its voice was called so in other conversations
  clips: Record<
    string,
    { clip?: string | null; clip_start?: string | null; clip_end?: string | null }
  >;
}

// a profile line with the evidence it came from (a conversation, a todo in it, a row that mentions the person)
export interface ProfileLine {
  text: string;
  src: { id: string; conv: string; at: string | null; start: string; title?: string | null }[];
}
export interface Profile {
  at: string;
  model?: string | null;
  upto: string | null; // the newest conversation it saw
  inputs: { conversations: number; todos: number; mentions: number };
  who: string;
  facts: ProfileLine[];
  recent: ProfileLine[];
  mentions: ProfileLine[]; // what others said about them
  open: ProfileLine[];
}

export interface Person {
  name: string;
  count: number;
  aliases: string[];
  profile: Profile | null;
  profile_newer: number; // their conversations after the profile was made
  first: string;
  last: string;
  with: string[];
  tags: string[];
  theirs: Todo[];
  mine: Todo[];
  conversations: {
    id: string;
    start: string;
    end: string;
    title?: string | null;
    summary?: string | null;
    points: string[];
    participants: string[];
    speech_s?: number;
  }[];
}
