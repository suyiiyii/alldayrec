<script setup lang="ts">
import { t } from "../i18n";
import { hhmmss, type Row } from "../lib/api";
import { player, toggle } from "../lib/audio";
import { useLongPress } from "../lib/longpress";
import { speakerLabel } from "../lib/speakers";
import { starred } from "../lib/stars";
import Avatar from "./Avatar.vue";
import Icon from "./Icon.vue";

// media: played aloud, not said in the room. seek: the page has a player (the conversation page): ▶ and a tap on
// the row play from this sentence on; without it ▶ plays the sentence alone. caption: the live page's large text under
// the speaker; now: the sentence being said (the server sends it with no speaker yet), with a breathing dot. menu: a
// long press or a right click on the row opens the page's RowMenu there; on a touch screen the row's text is not
// selectable (no callout, no loupe: its 复制 copies it), with a mouse it still is
const { row, seek, menu } = defineProps<{
  row: Row;
  hit?: boolean;
  media?: boolean;
  seek?: (r: Row) => void;
  caption?: boolean;
  now?: boolean;
  menu?: (r: Row, x: number, y: number) => void;
}>();
// a tap, not the end of selecting text to copy
const tap = () => seek && !getSelection()?.toString() && seek(row);
const press = useLongPress((x, y) => menu?.(row, x, y));
</script>

<template>
  <div
    class="row"
    :class="{
      pending: row.state && row.state !== 'final',
      hit,
      media,
      seekable: seek,
      caption,
      now,
      pressable: menu,
    }"
    :data-start="row.start"
    :title="row.state === 'draft' ? t('transcriptRow.draft') : undefined"
    v-on="menu ? press : {}"
    @click="tap"
  >
    <div class="t">
      <button v-if="seek" class="play" :title="t('transcriptRow.playFrom')" @click.stop="seek(row)">
        <Icon name="play" :size="14" /></button
      ><button
        v-else
        class="play"
        :disabled="player.failed.has(row.id)"
        :title="player.failed.has(row.id) ? t('transcriptRow.failed') : t('transcriptRow.playOne')"
        @click="toggle(row)"
      >
        <Icon
          :name="player.failed.has(row.id) ? 'x' : player.playing === row.id ? 'square' : 'play'"
          :size="14"
        /></button
      >{{ hhmmss(row.start) }}
    </div>
    <div class="spk" :class="{ me: row.speaker === '我' }">
      <template v-if="now && !row.speaker">{{ t("transcriptRow.speaking") }}</template>
      <template v-else><Avatar :name="row.speaker" />{{ speakerLabel(row.speaker) }}</template>
    </div>
    <div v-if="row.error" class="text err" :title="row.error">{{ t("transcriptRow.noText") }}</div>
    <div v-else class="text">
      <span v-if="row.dictation" class="dict">{{
        t("transcriptRow.dictation", { to: row.dictation })
      }}</span
      >{{ row.text || (now ? "" : t("transcriptRow.empty")) }}<span v-if="now" class="pulse" /><span
        v-if="starred.has(row.id)"
        class="star"
        role="img"
        :aria-label="t('transcriptRow.starred')"
        ><Icon name="star" :size="12"
      /></span>
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
  box-shadow:
    -8px 0 0 var(--accent-soft),
    8px 0 0 var(--accent-soft);
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
/* the live page: who and when on a small line, the words large under it */
.row.caption {
  grid-template-columns: auto 1fr;
  grid-template-areas: "spk t" "text text";
  column-gap: var(--space-2);
  row-gap: 0;
  padding: var(--space-3) 0 var(--space-1);
  border-bottom: 0;
}
.caption .spk {
  grid-area: spk;
  font-size: var(--fs-sm);
}
.caption .t {
  grid-area: t;
}
.caption .text {
  grid-area: text;
  font-size: var(--fs-lg);
  line-height: 1.6;
}
.row.caption.pending .text {
  color: var(--fg-2);
}
.row.caption.now .text {
  color: var(--muted);
}
.now .play {
  display: none; /* its audio is still coming */
}
.text .pulse {
  margin-left: 6px;
  vertical-align: middle;
}
.star {
  margin-left: 6px;
  color: var(--warn);
}
.star .icon {
  fill: currentColor;
  vertical-align: -1px;
}
@media (pointer: coarse) {
  .row.pressable {
    -webkit-touch-callout: none;
    -webkit-user-select: none;
    user-select: none;
  }
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
