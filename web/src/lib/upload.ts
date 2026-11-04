// What an answer to a piece's POST /api/chunks means for the piece (#328), as the apps' uploaders read it:
// - "bad": the server says it is not audio (400 with exactly this detail; it keeps the bytes in recordings/rejected/):
//   dropped, the next one goes;
// - "auth" (401, 403) and "refused" (any other 4xx: a proxy's, a wrong address): kept, no more tries until asked;
// - "retry": 5xx, 408, 429, no answer: tried again after a while.
export const BAD_AUDIO = "could not decode"; // server/alldayrec/routes_upload.py BAD_AUDIO

export type Verdict = "ok" | "bad" | "auth" | "refused" | "retry";

/** `body`: the answer's JSON, anything else (a proxy's HTML page) when it is not. */
export function verdict(status: number, body: unknown): Verdict {
  if (status >= 200 && status < 300) return "ok";
  if (status === 400 && (body as { detail?: unknown } | null)?.detail === BAD_AUDIO) return "bad";
  if (status === 401 || status === 403) return "auth";
  if (status === 408 || status === 429 || status < 400 || status >= 500) return "retry";
  return "refused";
}
