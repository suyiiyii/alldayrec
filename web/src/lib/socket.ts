import { onMounted, onUnmounted, ref, watch, type Ref } from "vue";

// The server pushes status every 2 s. After a long screen-off Android freezes the WebView and the socket
// dies without onclose, so silence longer than STALE_MS counts as a drop. onResync runs after every
// reconnect: what was pushed while away has to be fetched again.
const STALE_MS = 15000;

export function useSocket(onMessage: (m: any) => void, onResync: () => void) {
  const connected = ref(false);
  let ws: WebSocket | null = null,
    last = 0,
    opened = false,
    stopped = false,
    timer = 0,
    retry = 0;

  function connect() {
    if (stopped || ws) return;
    clearTimeout(retry);
    retry = 0;
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    last = Date.now();
    ws.onopen = () => {
      connected.value = true;
      if (opened) onResync();
      opened = true;
    };
    ws.onmessage = (e) => {
      last = Date.now();
      onMessage(JSON.parse(e.data));
    };
    const current = ws;
    ws.onclose = () => {
      if (stopped || ws !== current) return;
      ws = null;
      connected.value = false;
      retry = window.setTimeout(connect, 2000);
    };
  }
  function watchdog() {
    if (stopped || !ws || document.hidden || Date.now() - last < STALE_MS) return;
    ws.onopen = ws.onmessage = ws.onclose = null;
    ws.close();
    ws = null;
    connected.value = false;
    connect();
  }
  onMounted(() => {
    connect();
    timer = window.setInterval(watchdog, 5000);
    document.addEventListener("visibilitychange", watchdog);
  });
  onUnmounted(() => {
    stopped = true;
    clearInterval(timer);
    clearTimeout(retry);
    connected.value = false;
    document.removeEventListener("visibilitychange", watchdog);
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = null;
      ws.close();
      ws = null;
    }
  });
  // false when not connected: nothing is queued
  function send(m: unknown): boolean {
    if (ws?.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(m));
    return true;
  }
  return { connected, send };
}

// The live analysis and drafts run only while a page shows today in front (server watch_until): a heartbeat every
// 20 s while on() holds, and a wake when the page comes back to the front so a stale analysis is redone at once.
export function useWatch(
  send: (m: unknown) => boolean,
  connected: Ref<boolean>,
  on: () => boolean = () => true,
) {
  let wake = true,
    timer = 0;
  const beat = () => {
    if (on() && !document.hidden && send({ type: "watch", wake })) wake = false;
  };
  const onVisible = () => {
    if (document.hidden) return;
    wake = true;
    beat();
  };
  onMounted(() => {
    timer = window.setInterval(beat, 20000);
    document.addEventListener("visibilitychange", onVisible);
  });
  onUnmounted(() => {
    clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisible);
  });
  watch([connected, on], ([c, o]) => c && o && ((wake = true), beat()));
}
