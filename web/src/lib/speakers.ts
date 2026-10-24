// One colour per speaker, the same on every page: 我 the accent, an unnamed label (S1, S?) grey, a name one of
// PALETTE by a hash of it. No Vue here: speakers.check.ts runs it under plain node
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

// what the avatar shows: a name's first character (a letter in capitals), an unnamed label's number, S? a ?
export const initial = (name?: string | null) =>
  !name ? "?" : anonymous(name) ? name.slice(1) : [...name][0].toUpperCase();
