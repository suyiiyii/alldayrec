// node --experimental-strip-types importStart.check.ts: the import page's start-time guesses (src/lib/importStart.ts)
// on file names and on made-up MP4 and WAV headers
import assert from "node:assert/strict";
import { fromName, guess } from "./src/lib/importStart.ts";

const at = (s: string) => +new Date(s);
assert.equal(fromName("20261020_093000.mp3"), at("2026-10-20T09:30:00"));
assert.equal(fromName("2026-10-20 09.30.00.m4a"), at("2026-10-20T09:30:00"));
assert.equal(fromName("录音 2026-10-20 09-30.m4a"), at("2026-10-20T09:30:00"));
assert.equal(fromName("Voice 2026-10-20T21:05:07Z.wav"), at("2026-10-20T21:05:07"));
assert.equal(fromName("New Recording 3.m4a"), null);
assert.equal(fromName("meeting 2026-10-20.mp3"), null, "a date alone is no start time");
assert.equal(fromName("20261340_093000.mp3"), null, "no 13th month");
assert.equal(fromName("20261020_256000.mp3"), null, "no 25:60");

const ascii = (s: string) => [...new TextEncoder().encode(s)];
// boxes: 4-byte size, 4-byte type, body
const u32 = (n: number) => [n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
const box = (type: string, body: number[]) => [...u32(8 + body.length), ...ascii(type), ...body];
// mvhd version 0: creation and modification time (s since 1904), time scale, length
const mvhd = (made: number, len: number) =>
  box("mvhd", [
    0,
    0,
    0,
    0,
    ...u32(made + 2082844800),
    ...u32(made + 2082844800),
    ...u32(1000),
    ...u32(len * 1000),
    ...Array(80).fill(0),
  ]);
const mp4 = (made: number, len: number, saved: number) =>
  new File(
    [
      new Uint8Array([
        ...box("ftyp", [...Array(8).fill(65)]),
        ...box("mdat", Array(1000).fill(0)),
        ...box("moov", mvhd(made, len)),
      ]),
    ],
    "New Recording 3.m4a",
    { lastModified: saved * 1000 },
  );
const end = at("2026-10-20T10:30:00") / 1000; // a 1 h recording that ended here
assert.deepEqual(
  await guess(mp4(end, 3600, end + 1), null),
  { at: (end - 3600) * 1000, from: "meta" },
  "written at the close: the end",
);
assert.deepEqual(
  await guess(mp4(end, 3600, end + 9e6), null),
  { at: (end - 3600) * 1000, from: "meta" },
  "copied since: still the end",
);
assert.deepEqual(
  await guess(mp4(end - 3600, 3600, end), null),
  { at: (end - 3600) * 1000, from: "meta" },
  "saved a length later: the start",
);
assert.deepEqual(
  await guess(mp4(-2082844800, 3600, end), 3600),
  { at: (end - 3600) * 1000, from: "modified" },
  "0: no time; saved less length",
);

// Broadcast Wave: RIFF, WAVE, then chunks (4-byte id, little-endian size, body); bext's date and time at 320
const le = (n: number) => [n & 255, (n >>> 8) & 255, (n >>> 16) & 255, n >>> 24];
const chunk = (id: string, body: number[]) => [...ascii(id), ...le(body.length), ...body];
const bext = [...Array(320).fill(0), ...ascii("2026-10-2009:30:00"), ...Array(200).fill(0)];
const riff = (chunks: number[]) =>
  new File(
    [new Uint8Array([...ascii("RIFF"), ...le(4 + chunks.length), ...ascii("WAVE"), ...chunks])],
    "ZOOM0001.WAV",
  );
assert.deepEqual(
  await guess(
    riff([...chunk("fmt ", Array(16).fill(0)), ...chunk("bext", bext), ...chunk("data", [1, 2])]),
    5,
  ),
  {
    at: at("2026-10-20T09:30:00"),
    from: "meta",
  },
);
assert.equal((await guess(riff(chunk("data", [1, 2])), 5)).from, "modified", "no bext");
console.log("importStart ok");
