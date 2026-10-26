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
