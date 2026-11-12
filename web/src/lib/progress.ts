import { t } from "../i18n.ts";
import type { DeviceStatus } from "./api.ts";

export const showLiveLag = (d: DeviceStatus) => !!d.recording && !d.import_progress;

export const importLine = (d: DeviceStatus) => {
  const p = d.import_progress;
  return p
    ? t("progress.importAudio", {
        done: Number((p.done_s / 60).toFixed(1)),
        total: Number((p.total_s / 60).toFixed(1)),
      })
    : "";
};

// the live page's status line: of the recorders recording now, those transcribing and those standing by for one in
// the same place (colocate.py), and the slowest captions of those transcribing
export function recordingNow(ds: DeviceStatus[]) {
  const rec = ds.filter(showLiveLag);
  const on = new Set(rec.filter((d) => !d.standby).map((d) => d.device));
  // one standing by for a recorder that stopped takes over in a minute or two: named, but its lag is from before
  const spare = rec.filter((d) => d.standby && on.has(d.standby));
  const lags = rec.flatMap((d) => (on.has(d.device) && d.latency_s != null ? [d.latency_s] : []));
  return {
    main: rec.filter((d) => !spare.includes(d)).map((d) => d.device),
    spare: spare.map((d) => d.device),
    lag: lags.length ? Math.max(...lags) : null,
  };
}
