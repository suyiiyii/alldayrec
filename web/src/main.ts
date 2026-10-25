import { createApp } from "vue";
import App from "./App.vue";
import { i18n, setLang } from "./i18n";
import { get } from "./lib/api";
import { router } from "./router";
import "./style.css";

// Android WebView: the app has its own navigation
if (/; wv\)/.test(navigator.userAgent)) document.documentElement.classList.add("in-app");
// iOS WebKit applies :active (style.css's press feedback) on a touch only where some touch listener is
document.addEventListener("touchstart", () => {}, { passive: true });
// the online demo (vp build --mode demo): its snapshot, clock and socket in place before anything asks (demo.ts)
if (import.meta.env.MODE === "demo") await import("./demo");
// the language picked on 我的 in this browser; none: the browser's, then the tenant's (what the models write in)
const picked = localStorage.lang;
setLang(
  picked === "en" || picked === "zh" ? picked : navigator.language.startsWith("zh") ? "zh" : "en",
);
if (picked !== "en" && picked !== "zh")
  get<{ lang: "zh" | "en" }>("/api/lang").then(
    (r) => setLang(r.lang),
    () => {},
  );
createApp(App).use(router).use(i18n).mount("#app");
