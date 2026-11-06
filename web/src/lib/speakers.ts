// One colour per speaker, the same on every page: 我 the accent, an unnamed label (S1, S?) grey, a name one of
// PALETTE by a hash of it. speakers.check.ts runs it under plain node (i18n.ts is fine there)
import { t } from "../i18n.ts";
import type { LabelSuggestions } from "./api.ts";

const PALETTE = [
  "#c0392b",
  "#d35400",
  "#1f7a4d",
  "#1d4ed8",
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

// what the model wrote (a summary, a point) with its speaker labels as the page says them: 「S2 说……」 is 「说话人 2 说……」.
// Given the conversation's labels (participants: S? is never one), only those: an S3 bucket or a Galaxy S24 stays
export const labelText = (text: string, labels?: string[]) =>
  labels
    ? text.replace(/\bS(\d+|\?)(?!\w)/g, (m) =>
        m === "S?" || labels.includes(m) ? speakerLabel(m) : m,
      )
    : text;

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

// who an unnamed label may be, likeliest first, three at most: a name it was called here (said in this conversation:
// the more times the likelier; at: the first time, to hear it), the named speaker its voice is near (the closer
// first), a name its voice was called in other conversations (the more of them first). A named label has none (a name
// it was called is an alias, on 建议)
export function guesses(
  s: Pick<LabelSuggestions, "names" | "like" | "elsewhere"> | null,
  who: string,
): { name: string; at?: string }[] {
  if (!s || !/^S\d+$/.test(who)) return [];
  const here = new Map<string, { name: string; at: string; n: number }>();
  for (const h of s.names)
    if (h.who === who) {
      const g = here.get(h.canon);
      if (g) g.n++;
      else here.set(h.canon, { name: h.canon, at: h.at, n: 1 });
    }
  const all = [
    ...[...here.values()].sort((a, b) => b.n - a.n).map(({ name, at }) => ({ name, at })),
    ...s.like.filter((l) => l.who === who).sort((a, b) => b.score - a.score),
    ...s.elsewhere.filter((e) => e.who === who).sort((a, b) => b.convs - a.convs),
  ];
  return all
    .filter((g, i) => all.findIndex((x) => x.name === g.name) === i)
    .slice(0, 3)
    .map((g) => ("at" in g ? { name: g.name, at: g.at } : { name: g.name }));
}
