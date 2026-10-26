// When an imported recording began, the best guess first: what the file says about itself (meta), a date and time in
// its name (name), or when it was last saved less its length (modified). The import page shows which, and lets one
// change it.
export type From = "meta" | "name" | "modified";

// a date and time in a file name, local time: 20261020_093000, 2026-10-20 09.30.00, 录音 2026-10-20 09-30 (seconds
// optional); null when there is none or it is no real time
export function fromName(s: string): number | null {
  const m = /(20\d\d)[-_.]?(\d\d)[-_.]?(\d\d)[ _T-]*(\d\d)[-_.:]?(\d\d)(?:[-_.:]?(\d\d))?/.exec(s);
  if (!m) return null;
  const [y, mo, d, h, mi, sec] = m.slice(1).map((x) => Number(x ?? 0));
  const t = new Date(y, mo - 1, d, h, mi, sec);
  return t.getMonth() === mo - 1 && t.getDate() === d && t.getHours() === h && t.getMinutes() === mi
    ? +t
    : null;
}

const read = async (f: Blob, at: number, n: number) =>
  new DataView(await f.slice(at, at + n).arrayBuffer());
const tag = (v: DataView, at: number) => String.fromCharCode(...new Uint8Array(v.buffer, at, 4));
const MP4_EPOCH = 2082844800; // 1904-01-01 to 1970-01-01, in seconds

// MP4/M4A: the movie header's (mvhd) creation time (UTC) and length. Phones write it as they close the file (Apple's
// AVAudioFile, 10-26; Android's MPEG4Writer), so it is the end; unless the file was last saved one length after it,
// then it is the start. The boxes are walked by their sizes: moov is found also after the audio.
async function mp4(f: File): Promise<number | null> {
  for (let at = 0, size = 0; at + 8 <= f.size; at += size) {
    const v = await read(f, at, 48);
    const type = tag(v, 4);
    size = v.getUint32(0) === 1 ? Number(v.getBigUint64(8)) : v.getUint32(0);
    if ((at === 0 && type !== "ftyp") || size < 8) return null;
    if (type === "moov") {
      if (tag(v, 12) !== "mvhd") return null;
      const v1 = v.getUint8(16) === 1;
      const made = v1 ? Number(v.getBigUint64(20)) : v.getUint32(20);
      const len =
        ((v1 ? Number(v.getBigUint64(40)) : v.getUint32(32)) / v.getUint32(v1 ? 36 : 28)) * 1000;
      if (!made || !Number.isFinite(len)) return null; // 0: the encoder did not say (ffmpeg's default)
      const t = (made - MP4_EPOCH) * 1000;
      return Math.abs(t + len - f.lastModified) < Math.min(120_000, Math.abs(t - f.lastModified))
        ? t
        : t - len;
    }
  }
  return null;
}

// WAV from a recorder (Broadcast Wave): its bext chunk's OriginationDate and OriginationTime, local time
async function wav(f: File): Promise<number | null> {
  const h = await read(f, 0, 12);
  if (h.byteLength < 12 || tag(h, 0) !== "RIFF" || tag(h, 8) !== "WAVE") return null;
  for (let at = 12, size = 0; at + 8 <= f.size; at += 8 + size + (size & 1)) {
    const v = await read(f, at, 8 + 338);
    size = v.getUint32(4, true);
    if (tag(v, 0) === "bext")
      return fromName(new TextDecoder().decode(new Uint8Array(v.buffer, 8 + 320, 18)));
  }
  return null;
}

// the file's start time and where that came from; seconds: its length (duration), for the last guess
export async function guess(f: File, seconds: number | null): Promise<{ at: number; from: From }> {
  const meta = (await mp4(f).catch(() => null)) ?? (await wav(f).catch(() => null));
  if (meta != null) return { at: meta, from: "meta" };
  const named = fromName(f.name);
  if (named != null) return { at: named, from: "name" };
  return { at: f.lastModified - (seconds ?? 0) * 1000, from: "modified" };
}

// the file's length in seconds as the browser reads it; null when it cannot
export const duration = (f: Blob) =>
  new Promise<number | null>((ok) => {
    const a = new Audio(),
      url = URL.createObjectURL(f);
    const done = (s: number | null) => {
      URL.revokeObjectURL(url);
      ok(s);
    };
    a.preload = "metadata";
    a.onloadedmetadata = () => done(Number.isFinite(a.duration) ? a.duration : null);
    a.onerror = () => done(null);
    setTimeout(() => done(null), 10_000);
    a.src = url;
  });
