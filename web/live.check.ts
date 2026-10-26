// node --experimental-strip-types live.check.ts: useLive keeps pushes on the fetched day.
import assert from "node:assert/strict";
import { mock } from "node:test";
import { createServer } from "vite-plus";
import { createRenderer, defineComponent, nextTick, ref } from "vue";
import type { Row } from "./src/lib/api.ts";

const saved = Object.getOwnPropertyDescriptors(globalThis);
class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  onmessage: ((e: { data: string }) => void) | null = null;
  constructor(_url: string) {
    Socket.instances.push(this);
  }
  send() {}
  close() {}
  push(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}
Object.assign(globalThis, {
  window: { setInterval: () => 0 },
  document: { hidden: false, addEventListener() {}, removeEventListener() {} },
  location: { protocol: "https:", host: "example.test" },
  WebSocket: Socket,
});
const requests: string[] = [];
globalThis.fetch = async (input) => {
  const path = String(input);
  requests.push(path);
  return Response.json(path.startsWith("/api/status") ? { devices: [] } : []);
};
const server = await createServer({ configFile: false, server: { middlewareMode: true } });
const renderer = createRenderer<object, object>({
  patchProp() {},
  insert() {},
  remove() {},
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  setText() {},
  setElementText() {},
  parentNode: () => null,
  nextSibling: () => null,
});
let app: ReturnType<typeof renderer.createApp> | undefined;
try {
  const openedAt = new Date("2026-01-31T23:59:00").getTime();
  mock.timers.enable({ apis: ["Date"], now: openedAt });
  const { useLive } = await server.ssrLoadModule("/src/lib/live.ts");
  const { today, shiftDay } = await server.ssrLoadModule("/src/lib/calendar.ts");
  const day = ref(today());
  const yesterday = shiftDay(day.value, -1),
    tomorrow = shiftDay(day.value, 1);
  let store!: ReturnType<typeof import("./src/lib/live.ts").useLive>;
  const mount = (page: boolean) => {
    app = renderer.createApp(
      defineComponent({
        setup() {
          store = useLive(day, page);
          return () => null;
        },
      }),
    );
    app.mount({});
    return Socket.instances.at(-1)!;
  };
  const loaded = async () => {
    for (let i = 0; i < 30 && !store.ready.value; i++) await new Promise(setImmediate);
    assert.equal(store.ready.value, true);
  };
  const row = (id: string, date: string, time = "12:00:00", state = "final"): Row => ({
    id,
    start: `${date}T${time}`,
    end: `${date}T${time}.500`,
    source: "mic",
    state,
    text: id,
  });
  const final = (socket: Socket, rows: Row[], from = rows[0]!.start, to = rows.at(-1)!.end) =>
    socket.push({ type: "final", device: "mic", from, to, rows });
  for (const page of [true, false]) {
    // Live.vue and LiveCard.vue share this store.
    const socket = mount(page);
    await loaded();
    assert.ok(requests.includes(`/api/sentences?date=${day.value}`));
    final(socket, [row("today", day.value)]);
    final(socket, [row("old-import", yesterday)]);
    assert.deepEqual(
      store.rows.value.map((r) => r.id),
      ["today"],
      "useLive excludes imported finals from another day and keeps shown-day finals",
    );
    final(socket, [row("tomorrow", tomorrow)]);
    assert.deepEqual(
      store.rows.value.map((r) => r.id),
      ["today"],
    );

    // A finalization window can cross midnight: select each row by its start, like /api/sentences.
    final(socket, [
      row("before-midnight", yesterday, "23:59:59"),
      row("midnight", day.value, "00:00:00"),
    ]);
    final(socket, [
      row("last-today", day.value, "23:59:59"),
      row("next-midnight", tomorrow, "00:00:00"),
    ]);
    assert.deepEqual(
      store.rows.value.map((r) => r.id),
      ["today", "midnight", "last-today"],
    );

    const partial = row("partial", day.value, "13:00:00", "draft");
    socket.push({ type: "partial", device: "mic", row: partial });
    socket.push({
      type: "draft",
      device: "mic",
      rows: [row("old-draft", yesterday, "13:00:00", "draft")],
    });
    assert.equal(
      store.partials.value.mic?.id,
      "partial",
      "an old draft does not clear today's partial",
    );
    socket.push({ type: "partial", device: "old-mic", row: row("old-partial", yesterday) });
    socket.push({ type: "partial", device: "future-mic", row: row("future-partial", tomorrow) });
    assert.deepEqual(Object.keys(store.partials.value), ["mic"]);
    socket.push({ type: "partial", device: "mic", row: null });
    assert.deepEqual(store.partials.value, {});
    // A fresh store avoids the preceding end-of-day final cursor covering these drafts.
    app!.unmount();
    const drafts = mount(page);
    await loaded();
    drafts.push({
      type: "draft",
      device: "mic",
      rows: [
        row("old-draft", yesterday, "23:59:59", "draft"),
        row("today-draft", day.value, "00:00:00", "draft"),
        row("future-draft", tomorrow, "00:00:00", "draft"),
      ],
    });
    assert.deepEqual(
      store.rows.value.map((r) => r.id),
      ["today-draft"],
    );
    final(drafts, [], `${day.value}T00:00:00`, `${day.value}T00:01:00`);
    assert.deepEqual(store.rows.value, [], "empty finals still remove covered drafts");
    day.value = yesterday;
    await nextTick();
    await loaded();
    final(drafts, [row("history-push", yesterday)]);
    assert.deepEqual(store.rows.value, [], "historical days remain fetch-only");
    app!.unmount();
    day.value = today();
  }
  for (const page of [true, false]) {
    mock.timers.setTime(openedAt);
    day.value = today();
    const openedDay = day.value;
    const socket = mount(page);
    await loaded();
    assert.equal(store.live.value, true);
    mock.timers.setTime(openedAt + 2 * 60 * 1000); // 00:01, without changing the caller's day ref
    const currentDay = today();
    assert.notEqual(currentDay, openedDay);
    final(socket, [row("after-midnight", currentDay, "00:01:00")]);
    final(socket, [row("old-import", openedDay, "23:58:00")]);
    assert.deepEqual(
      store.rows.value.map((r) => r.id),
      ["after-midnight"],
      "a view following today accepts next-day finals after midnight and excludes old imports",
    );
    socket.push({ type: "partial", device: "mic", row: row("new-partial", currentDay) });
    socket.push({ type: "draft", device: "mic", rows: [row("old-draft", openedDay)] });
    assert.equal(store.partials.value.mic?.id, "new-partial");
    socket.push({
      type: "draft",
      device: "mic",
      rows: [row("new-draft", currentDay, "00:02:00", "draft")],
    });
    assert.deepEqual(
      store.rows.value.map((r) => r.id),
      ["after-midnight", "new-draft"],
    );
    assert.deepEqual(store.partials.value, {});

    day.value = shiftDay(openedDay, -1); // explicitly pick a past date after midnight
    await nextTick();
    await loaded();
    assert.equal(store.live.value, false);
    assert.ok(requests.includes(`/api/sentences?date=${day.value}`));
    final(socket, [row("current-push", currentDay)]);
    final(socket, [row("past-push", day.value)]);
    assert.deepEqual(store.rows.value, [], "picked past dates remain fetch-only after midnight");
    socket.push({
      type: "conversation",
      id: "past-conversation",
      start: `${day.value}T12:00:00`,
      state: "closed",
    });
    socket.push({
      type: "conversation",
      id: "current-conversation",
      start: `${currentDay}T12:00:00`,
      state: "closed",
    });
    assert.deepEqual(
      store.convs.value.map((c) => c.id),
      ["past-conversation"],
    );
    app!.unmount();
  }
  console.log("useLive date filtering checks passed");
} finally {
  app?.unmount();
  await server.close();
  mock.timers.reset();
  for (const key of ["window", "document", "location", "WebSocket", "fetch"]) {
    if (saved[key]) Object.defineProperty(globalThis, key, saved[key]!);
    else Reflect.deleteProperty(globalThis, key);
  }
}
