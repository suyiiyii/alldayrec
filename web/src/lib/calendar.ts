import { ref } from "vue";
import { intl, lang } from "../i18n";
import { get } from "./api";

export interface DayInfo {
  conversations: number;
  speech_min: number;
  sentences: number;
}

// days with sentences (newest first) and what they hold; fetched once, refreshed on demand
export const calendar = ref<Record<string, DayInfo>>({});
let pending: Promise<void> | null = null;
export function loadCalendar(force = false) {
  if (!pending || force)
    pending = get<Record<string, DayInfo>>("/api/calendar").then((c) => {
      calendar.value = c;
    });
  return pending;
}

export const today = () => new Date().toLocaleDateString("sv");

export function shiftDay(day: string, n: number) {
  const t = new Date(day + "T12:00:00");
  t.setDate(t.getDate() + n);
  return t.toLocaleDateString("sv");
}

/** The nearest day with sentences before (-1) or after (1) `day`. */
export function neighbour(day: string, dir: -1 | 1) {
  const days = Object.keys(calendar.value).sort();
  return dir < 0 ? days.filter((d) => d < day).at(-1) : days.find((d) => d > day);
}

export function dayLabel(day: string) {
  const t = new Date(day + "T12:00:00");
  return lang() === "zh"
    ? `${t.getMonth() + 1}月${t.getDate()}日 周${"日一二三四五六"[t.getDay()]}`
    : t.toLocaleDateString(intl(), { weekday: "short", month: "short", day: "numeric" });
}
