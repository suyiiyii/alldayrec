<script setup lang="ts">
import { card, caughtUp, CHARGE, hhmm, type DeviceStatus } from "../lib/api";
import { today } from "../lib/calendar";

// A device as a status card (我的, and 实时 with nothing going on); a tap opens its own page. The third tile: how far
// behind the words come up while it records, else when its audio last came
const { d } = defineProps<{ d: DeviceStatus }>();

// behind: its finals are short of its audio; offline: no audio in the last 2 min
const badge = (d: DeviceStatus) =>
  !caughtUp(d)
    ? { text: `落后 ${Math.max(1, Math.round(d.left_s / 60))} 分钟`, cls: "warn" }
    : d.recording
      ? { text: "已追上", cls: "ok" }
      : { text: "离线", cls: "off" };
// the card's free space when it records to it, else what is wrong with it
const cardTile = (d: DeviceStatus) =>
  !d.hw
    ? "—"
    : !d.hw.store && d.hw.total_kb
      ? `${((d.hw.free_kb ?? 0) / 1048576).toFixed(1)} GB`
      : card(d.hw);
// today: the time; before: how many days ago
const seen = (iso: string | null) => {
  if (!iso) return "—";
  const days = Math.round((+new Date(today()) - +new Date(iso.slice(0, 10))) / 86400000);
  return days < 1 ? hhmm(iso) : days === 1 ? "昨天" : `${days} 天前`;
};
</script>

<template>
  <RouterLink class="card dev" :to="`/device/${encodeURIComponent(d.device)}`">
    <div class="dev-top">
      <b>{{ d.device }}</b>
      <span class="badge" :class="badge(d).cls">{{ badge(d).text }}</span>
    </div>
    <div class="tiles">
      <div>
        <span class="v">{{ d.hw?.battery != null ? d.hw.battery + "%" : "—" }}</span>
        <span class="k">电量{{ d.hw?.charge ? " · " + CHARGE[d.hw.charge] : "" }}</span>
      </div>
      <div>
        <span class="v" :class="{ long: cardTile(d).length > 8, bad: d.hw?.store }">{{
          cardTile(d)
        }}</span>
        <span class="k">存储卡{{ !d.hw?.store && d.hw?.total_kb ? "剩余" : "" }}</span>
      </div>
      <div v-if="d.recording">
        <span class="v">{{ d.latency_s != null ? d.latency_s + " 秒" : "—" }}</span>
        <span class="k">延迟</span>
      </div>
      <div v-else>
        <span class="v">{{ seen(d.audio_end) }}</span>
        <span class="k">最后录音</span>
      </div>
    </div>
  </RouterLink>
</template>

<style scoped>
.dev-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
/* big enough to read across the room: 已追上 green, 落后 amber, 离线 grey (a recorder switched off is normal) */
.badge {
  font-size: var(--fs-sm);
  font-weight: 600;
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill);
  white-space: nowrap;
}
.badge.ok {
  color: var(--ok);
  background: var(--ok-soft);
}
.badge.warn {
  color: var(--warn);
  background: var(--warn-soft);
}
.badge.off {
  color: var(--fg-2);
  background: var(--line);
}
.tiles {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.tiles > div {
  background: var(--bg);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.v {
  font-size: var(--fs-lg);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
  overflow-wrap: anywhere;
}
.v.long {
  font-size: var(--fs-sm);
}
.k {
  font-size: var(--fs-xs);
  color: var(--muted);
}
</style>
