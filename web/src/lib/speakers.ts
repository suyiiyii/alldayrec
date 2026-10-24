// One colour per speaker, the same on every page: 我 the accent, an unnamed label (S1, S?) grey, a name one of
// PALETTE by a hash of it. speakers.check.ts runs it under plain node (i18n.ts is fine there)
import { t } from "../i18n.ts";

const PALETTE = [
  "#c0392b",
  "#d35400",
  "#1f7a4d",
  "#0e7490",
  "#6d28d9",
  "#b45309",
  "#be185d",
  "#374151",
];
export const anonymous = (name?: string | null) => !name || /^S(\d+|\?)$/.test(name);

export function speakerColor(name?: string | null): string {
  if (name === "我") return "var(--accent)";
  if (anonymous(name)) return "var(--muted)";
  let h = 0;
  for (const c of name!) h = (h * 31 + c.codePointAt(0)!) >>> 0;
  return PALETTE[h % PALETTE.length];
}

// what the avatar shows: a name's first character (a letter in capitals; 我's as the page says it), an unnamed label's
// number, S? a ?
export const initial = (name?: string | null) =>
  !name ? "?" : anonymous(name) ? name.slice(1) : [...speakerLabel(name)][0].toUpperCase();

// a label as the page says it: S2 is 说话人 2, S? 未识别, 我 (the wearer, a key in the data) Me in English; names as
// they are (the API keeps the labels)
export const speakerLabel = (name?: string | null) =>
  !name
    ? "—"
    : name === "S?"
      ? t("speaker.unknown")
      : name === "我"
        ? t("speaker.me")
        : /^S\d+$/.test(name)
          ? t("speaker.n", { n: name.slice(1) })
          : name;

// a list of people in one short line: 我 first (whether I was there matters most), two names, then 「等 N 人」
export const speakerList = (names: string[]) => {
  const two = [...names.filter((n) => n === "我"), ...names.filter((n) => n !== "我")]
    .slice(0, 2)
    .map(speakerLabel)
    .join(t("common.list"));
  return names.length > 2
    ? t("speaker.andMore", { names: two, n: names.length, more: names.length - 2 })
    : two;
};

// a voiceprint score in words: no numbers on the page
export const likeness = (score: number) =>
  t(score >= 0.7 ? "speaker.veryLike" : "speaker.someLike");
