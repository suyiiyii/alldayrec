<script setup lang="ts">
import { hhmmss, type Row } from "../lib/api";
import { player, toggle } from "../lib/audio";
import Avatar from "./Avatar.vue";

defineProps<{ row: Row; hit?: boolean; media?: boolean }>(); // media: played aloud, not said in the room
</script>

<template>
  <div
    class="row"
    :class="{ pending: row.state && row.state !== 'final', hit, media }"
    :data-start="row.start"
  >
    <div class="t">
      <button
        class="play"
        :disabled="player.failed.has(row.id)"
        :title="player.failed.has(row.id) ? '这段录音取不出来' : '播放这一句'"
        @click="toggle(row)"
      >
        {{ player.failed.has(row.id) ? "×" : player.playing === row.id ? "■" : "▶" }}</button
      >{{ hhmmss(row.start) }}
    </div>
    <div
      class="spk"
      :class="{ me: row.speaker === '我' }"
      :title="row.speaker_confidence == null ? '' : `置信度 ${row.speaker_confidence}`"
    >
      <Avatar :name="row.speaker" />{{ row.speaker || "—" }}
    </div>
    <div v-if="row.error" class="text err">识别失败：{{ row.error }}</div>
    <div v-else class="text">
      <span v-if="row.state === 'draft'" class="draft" title="页面开着时的快速转写，定稿出来后替换：通常几分钟，手机在补传卡里的积压时要等补完">初稿</span
      ><span v-if="row.dictation" class="dict">口述→{{ row.dictation }}</span
      >{{ row.text || "（空）" }}
    </div>
  </div>
</template>

<style scoped>
/* the speaker column takes the name's width (「同事A」 on one line), at least 4.5em: names up to three characters line up */
.row {
  display: grid;
  grid-template-columns: 80px minmax(4.5em, auto) 1fr;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--line);
}
.row.hit {
  background: var(--warn-soft);
}
.t,
.spk {
  color: var(--muted);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.t {
  display: flex;
  align-items: center;
  align-self: start;
  min-height: 26px;
}
.spk {
  display: flex;
  align-items: center;
  align-self: start;
  gap: 6px;
  min-height: 26px;
  font-size: var(--fs-sm);
  color: var(--fg-2);
}
.spk.me {
  color: var(--accent);
  font-weight: 600;
}
.row.media {
  opacity: 0.45;
}
.row.pending .text,
.row.pending .spk {
  color: var(--muted);
}
/* the glyph is small, the button around it a 32 px square */
.play {
  border: 0;
  background: none;
  color: var(--accent);
  min-height: 32px;
  min-width: 32px;
  margin-left: -8px;
  padding: 0;
  font-size: var(--fs-xs);
  border-radius: var(--radius-sm);
}
.play:disabled {
  color: var(--muted);
}
.text {
  word-break: break-word;
  padding-top: 1px;
}
.err {
  color: var(--err);
  font-size: var(--fs-sm);
}
.draft {
  font-size: var(--fs-xs);
  color: var(--muted);
  border: 1px dashed var(--line);
  border-radius: var(--radius-sm);
  padding: 0 var(--space-1);
  margin-right: 6px;
  white-space: nowrap;
}
@media (max-width: 600px) {
  .row {
    grid-template-columns: 72px minmax(4.5em, auto) 1fr;
    gap: var(--space-2);
  }
  .play {
    min-width: 28px;
    margin-left: -6px;
  }
}
</style>
