import { computed, ref, watch, type Ref } from "vue";
import { get, type Conversation, type LiveState, type Row, type Status } from "./api";
import { shiftDay, today } from "./calendar";
import { useSocket, useWatch } from "./socket";

// One day's sentences as the server pushes them (final rows replacing drafts, the sentence being said as a partial),
// its closed conversations, the live analyses and the status. The live page and 时间线's live card both use it; a
// day other than today is loaded once and gets no pushes. page: the live page (its watch asks for short pieces).
export function useLive(day: Ref<string>, page = false) {
  const now = today(); // as the page opened: one left open past midnight keeps following the stream
  const live = computed(() => day.value === now);
  const shownDay = () => (live.value ? today() : day.value);
  const rows = ref<Row[]>([]),
    partials = ref<Record<string, Row>>({}), // per device, the sentence being said now (DRAFT_ENGINE qwen-stream)
    convs = ref<Conversation[]>([]),
    status = ref<Status | null>(null),
    lives = ref<LiveState[]>([]),
    ready = ref(false);
  // a conversation going on somewhere: what 时间线 shows the live card for, and the live page captions for
  const going = computed(() => !!status.value?.devices?.some((d) => d.recording && d.conversation));

  // per device, up to where final rows cover the time: its drafts before that are no longer shown (they stay on the server)
  const finalTo: Record<string, string> = {};
  // a device's finalized time: the status's, or its last final message's (it comes before the next status, every 2 s)
  function doneTo(device: string) {
    const s = status.value?.devices?.find((d) => d.device === device)?.done_to ?? "",
      f = finalTo[device] ?? "";
    return s > f ? s : f;
  }
  function finalize(m: { device?: string; from: string; to: string; rows: Row[] }) {
    if (m.device) finalTo[m.device] = m.to;
    rows.value = rows.value
      .filter((r) =>
        r.state === "draft"
          ? !(r.source === m.device && r.start < m.to)
          : !(m.from <= r.start && r.start < m.to && (!m.device || r.source === m.device)),
      )
      .concat(m.rows);
  }
  function addDrafts(m: { device: string; rows: Row[] }) {
    const to = finalTo[m.device] || "";
    delete partials.value[m.device]; // the sentence it showed has ended: now a draft row
    rows.value = rows.value.concat(m.rows.filter((r) => r.start >= to));
  }
  function addConv(c: Conversation) {
    if (c.state === "open") return;
    const i = convs.value.findIndex((x) => x.id === c.id);
    if (i >= 0) convs.value[i] = c;
    else convs.value.push(c);
  }

  async function load() {
    const d = shownDay();
    const [rs, cs, ls, st] = await Promise.all([
      get<Row[]>("/api/sentences", { date: d }),
      get<Conversation[]>("/api/conversations", { date: d }),
      get<LiveState[]>("/api/live"),
      get<Status>("/api/status"), // at once, not at the first push 2 s later
    ]);
    if (d !== shownDay()) return; // another day was picked meanwhile
    rows.value = rs;
    lives.value = ls;
    status.value = st;
    convs.value = [];
    cs.forEach(addConv);
    ready.value = true;
  }
  watch(day, () => {
    ready.value = false;
    void load();
  });
  void load();

  const { connected, send } = useSocket((m) => {
    // Read the clock on every push: a view following today rolls over without remounting.
    const d = shownDay(),
      nextDay = shiftDay(d, 1);
    // /api/sentences selects row starts in [date, next date), in the recording's local wall time.
    const inDay = (r: { start: string }) => d <= r.start && r.start < nextDay;
    // rows and conversations of another day than the one shown are not for this page
    if (m.type === "final") {
      if (live.value && m.from < nextDay && m.to > d)
        finalize({ ...m, rows: m.rows.filter(inDay) });
    } else if (m.type === "draft") {
      const rs = m.rows.filter(inDay);
      if (live.value && rs.length) addDrafts({ ...m, rows: rs });
    } else if (m.type === "partial") {
      if (live.value && m.row && inDay(m.row)) partials.value[m.device] = m.row;
      else delete partials.value[m.device];
    } else if (m.type === "conversation") {
      if (inDay(m)) addConv(m);
    } else if (m.type === "summary") {
      const c = convs.value.find((x) => x.id === m.conversation);
      if (c) c.summary = { ...m };
    } else if (m.type === "live") {
      lives.value = lives.value.filter((l) => l.device !== m.device).concat(m.live ? [m.live] : []);
    } else if (m.type === "status") status.value = m;
  }, load);
  useWatch(send, connected, () => live.value, page);

  return { live, rows, partials, convs, status, lives, ready, going, connected, doneTo };
}
