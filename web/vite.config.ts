import vue from "@vitejs/plugin-vue";
import { defineConfig, lazyPlugins } from "vite-plus";

// dev proxies /api and /ws to a running server: a local one by default, or yours with its token from the shell:
//   LIVE_SERVER=https://your.server LIVE_RECORD_TOKEN=... pnpm dev
const target = process.env.LIVE_SERVER ?? "http://127.0.0.1:8770";
const headers = { Authorization: `Bearer ${process.env.LIVE_RECORD_TOKEN ?? ""}` };

export default defineConfig({
  fmt: {},
  lint: {
    jsPlugins: [{ name: "vite-plus", specifier: "vite-plus/oxlint-plugin" }],
    rules: { "vite-plus/prefer-vite-plus-imports": "error" },
    options: { typeAware: true, typeCheck: true },
  },
  plugins: lazyPlugins(() => [vue()]),
  // vue-i18n's build flags: the Composition API only (no legacy API in the bundle), no devtools in production
  define: {
    __VUE_I18N_FULL_INSTALL__: true,
    __VUE_I18N_LEGACY_API__: false,
    __INTLIFY_PROD_DEVTOOLS__: false,
  },
  build: { outDir: "../server/alldayrec/static/dist", emptyOutDir: true },
  server: {
    proxy: {
      "/api": { target, changeOrigin: true, headers },
      "/ws": { target, changeOrigin: true, ws: true, headers },
    },
  },
});
