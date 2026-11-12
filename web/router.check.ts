// node --experimental-strip-types router.check.ts: a page whose chunk a deploy removed loads the page anew at its
// target, once (#327: a WebView open since before the deploy tapped 日志 and nothing happened).
import assert from "node:assert/strict";
import { createServer } from "vite-plus";

const assigned: string[] = [];
const stored = new Map<string, string>();
const loc = {
  protocol: "https:",
  host: "example.test",
  pathname: "/progress",
  search: "",
  hash: "",
  assign: (href: string) => assigned.push(href),
};
Object.assign(globalThis, {
  window: {
    history: { state: null, pushState() {}, replaceState() {} },
    location: loc,
    addEventListener() {},
    removeEventListener() {},
    scrollTo() {},
  },
  location: loc,
  history: { state: null, pushState() {}, replaceState() {} },
  document: {
    title: "",
    documentElement: {},
    createElement: () => ({ style: {} }),
    querySelector: () => null,
    addEventListener() {},
    removeEventListener() {},
  },
  sessionStorage: {
    getItem: (k: string) => stored.get(k) ?? null,
    setItem: (k: string, v: string) => void stored.set(k, v),
    removeItem: (k: string) => void stored.delete(k),
  },
});
const server = await createServer({
  configFile: false,
  server: { middlewareMode: true },
  plugins: [
    {
      name: "stale-chunk",
      transform(_source, id) {
        if (!id.endsWith(".vue")) return;
        // as a browser says it when the chunk is a 404 (Chrome; WebKit: "Importing a module script failed.")
        if (id.endsWith("/pages/Device.vue"))
          return 'throw new TypeError("Failed to fetch dynamically imported module: https://example.test/assets/Device-old.js")';
        return "export default { render: () => null }";
      },
    },
  ],
});
try {
  const { router } = await server.ssrLoadModule("/src/router.ts");
  await router.push("/progress");
  await router.push("/device/LR-N4-0001").catch(() => {});
  assert.deepEqual(assigned, ["/device/LR-N4-0001"], "the page is loaded anew at the log's URL");
  // loaded anew and the chunk still fails: no second reload, no loop
  await router.push("/device/LR-N4-0001").catch(() => {});
  assert.deepEqual(assigned, ["/device/LR-N4-0001"]);
  // a page that loads clears the mark: the next deploy's stale chunk reloads again
  await router.push("/me");
  await router.push("/device/LR-N4-0001").catch(() => {});
  assert.deepEqual(assigned, ["/device/LR-N4-0001", "/device/LR-N4-0001"]);
  // its styles, which Vite loads first, gone the same way
  await router.push("/me");
  router.addRoute({
    path: "/styled",
    component: () => Promise.reject(new Error("Unable to preload CSS for /assets/Device-old.css")),
  });
  await router.push("/styled").catch(() => {});
  assert.deepEqual(assigned.at(-1), "/styled");
  // any other error is not a reason to reload
  router.addRoute({
    path: "/broken",
    component: () => Promise.reject(new Error("render failed")),
  });
  await router.push("/broken").catch(() => {});
  assert.equal(assigned.length, 3);
} finally {
  await server.close();
}
console.log("router check ok: a stale chunk reloads its target once");
