<script setup lang="ts">
import { hhmmss, type Row } from "../lib/api";
import { player, toggle } from "../lib/audio";
import { speakerLabel } from "../lib/speakers";
import Avatar from "./Avatar.vue";
import Icon from "./Icon.vue";

// media: played aloud, not said in the room. seek: the page has a player (the conversation page): ▶ and a tap on
// the row play from this sentence on; without it ▶ plays the sentence alone
const { row, seek } = defineProps<{ row: Row; hit?: boolean; media?: boolean; seek?: (r: Row) => void }>();
// a tap, not the end of selecting text to copy
const tap = () => seek && !getSelection()?.toString() && seek(row);
</script>

<template>
  <div
    class="row"
    :class="{ pending: row.state && row.state !== 'final', hit, media, seekable: seek }"
    :data-start="row.start"
    :title="row.state === 'draft' ? '实时初稿，稍后定稿' : undefined"
    @click="tap"
  >
    <div class="t">
      <button v-if="seek" class="play" title="从这一句开始播放" @click.stop="seek(row)">
        <Icon name="play" :size="14" /></button
      ><button
        v-else
        class="play"
        :disabled="player.failed.has(row.id)"
        :title="player.failed.has(row.id) ? '这段录音取不出来' : '播放这一句'"
        @click="toggle(row)"
      >
        <Icon
          :name="player.failed.has(row.id) ? 'x' : player.playing === row.id ? 'square' : 'play'"
          :size="14"
        /></button
      >{{ hhmmss(row.start) }}
    </div>
    <div class="spk" :class="{ me: row.speaker === '我' }">
      <Avatar :name="row.speaker" />{{ speakerLabel(row.speaker) }}
    </div>
    <div v-if="row.error" class="text err" :title="row.error">这一句没能识别出文字</div>
    <div v-else class="text">
      <span v-if="row.dictation" class="dict">口述→{{ row.dictation }}</span
      >{{ row.text || "（空）" }}
    </div>
  </div>
</template>

<style scoped>
/* the speaker column takes the name's width (「同事A」 on one line); at least an avatar and 「说话人 2」, so most rows line up */
.row {
  display: grid;
  grid-template-columns: 80px minmax(84px, auto) 1fr;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--line);
}
/* the sentence jumped to, or playing */
.row.hit {
  background: var(--accent-soft);
  box-shadow: -8px 0 0 var(--accent-soft), 8px 0 0 var(--accent-soft);
}
.row.seekable {
  cursor: pointer;
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
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
@media (max-width: 600px) {
  .row {
    grid-template-columns: 72px minmax(76px, auto) 1fr;
    gap: var(--space-2);
  }
  .spk {
    font-size: var(--fs-xs);
  }
  .play {
    min-width: 28px;
    margin-left: -6px;
  }
}
</style>
