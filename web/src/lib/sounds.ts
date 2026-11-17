import { t } from "../i18n.ts";
import { post } from "./api.ts";
import { toast } from "./toast.ts";

// Sound events (server: sound_events.py): what the environmental sound scores say, each one a guess worded 「可能」.
export type SoundEvent = "speech" | "music" | "vehicle" | "quiet";
export const SOUND_EVENTS: SoundEvent[] = ["speech", "music", "vehicle", "quiet"];
// a conversation's tags (most minutes first, the short ones and speech left out by the server)
export interface SoundTag {
  event: SoundEvent;
  minutes: number;
  conf: number; // the share of its minutes' frames that had it, 0-1
  mostly?: boolean; // quiet over half of its minutes with scores
  wrong?: number; // the 不对 said of it (its id)
}
export interface ConvSounds {
  covered: number; // its minutes with scores
  events: SoundTag[];
  device: string;
  from: number; // unix ms: the span the tags are of (a 不对 names it)
  to: number;
}
// GET /api/sounds/day: a device's minutes as runs of the same events (a minute with none: heard, nothing of these);
// no run: no scores there
export interface SoundRun {
  from: number;
  to: number;
  events: Partial<Record<SoundEvent, number>>;
  conv: string | null;
  wrong: Partial<Record<SoundEvent, number>>;
}
export interface SoundDay {
  device: string;
  runs: SoundRun[];
}

export const tagText = (x: SoundTag) =>
  x.mostly ? t("sound.mostlyQuiet") : t(`sound.tag.${x.event}`, { n: x.minutes });

// 不对 on an event of [from, to) of device, or its undo (id): the new id, or undefined once undone
export async function markWrong(
  device: string,
  from: number,
  to: number,
  event: SoundEvent,
  conv: string | null,
  id?: number,
): Promise<number | undefined> {
  if (id) {
    await post(`/api/sounds/wrong/${id}`, undefined, "DELETE");
    return undefined;
  }
  const r = await post<{ id: number }>("/api/sounds/wrong", {
    device,
    start_ms: from,
    end_ms: to,
    event,
    ...(conv && { conv }),
  });
  toast(t("sound.marked", { what: t(`sound.ev.${event}`) }));
  return r.id;
}
