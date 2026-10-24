// The pages' words: locales/zh.json (the default) and en.json. Nothing here touches the browser when imported, as
// the lib files that use t also run under plain node (*.check.ts); main.ts picks the language, the 我的 page changes it
import { createI18n } from "vue-i18n";
import en from "./locales/en.json" with { type: "json" };
import zh from "./locales/zh.json" with { type: "json" };

export type Lang = "zh" | "en";
export const i18n = createI18n<[typeof zh], Lang, false>({
  legacy: false,
  locale: "zh",
  fallbackLocale: "zh",
  messages: { zh, en },
});
export const t = i18n.global.t;
export const lang = () => i18n.global.locale.value;
// the locale for Intl and toLocaleString: dates and times as the page's language writes them
export const intl = () => (lang() === "zh" ? "zh-CN" : "en-US");

export function setLang(l: Lang) {
  i18n.global.locale.value = l;
  document.documentElement.lang = intl();
}
