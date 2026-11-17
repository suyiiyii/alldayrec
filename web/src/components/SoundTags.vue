<script setup lang="ts">
import { ref } from "vue";
import { t } from "../i18n";
import { markWrong, tagText, type ConvSounds, type SoundTag } from "../lib/sounds";
import { toast } from "../lib/toast";

// A conversation's sound tags (lib/sounds.ts): 「可能」 each, most minutes first. On the conversation page (editable)
// each has 不对 beside it, kept with its span and model for the gold labels; a marked one reads struck through, a tap
// again takes it back. On a card: the words alone (the card is a link)
const {
  s,
  conv = null,
  editable = false,
} = defineProps<{
  s: ConvSounds;
  conv?: string | null;
  editable?: boolean;
}>();
const busy = ref(false);
async function wrong(x: SoundTag) {
  if (busy.value) return;
  busy.value = true;
  try {
    x.wrong = await markWrong(s.device, s.from, s.to, x.event, conv, x.wrong);
  } catch (e) {
    toast((e as Error).message);
  }
  busy.value = false;
}
</script>

<template>
  <div class="sounds" :title="t('sound.hint')">
    <span v-for="x in s.events" :key="x.event" class="snd" :class="{ off: x.wrong }">
      <span class="w">{{ tagText(x) }}</span>
      <button
        v-if="editable"
        class="btn-ghost no"
        :aria-pressed="!!x.wrong"
        :aria-label="x.wrong ? t('sound.undo') : t('sound.wrongLabel', { what: tagText(x) })"
        :disabled="busy"
        @click="wrong(x)"
      >
        {{ x.wrong ? t("sound.undo") : t("sound.wrong") }}
      </button>
    </span>
  </div>
</template>

<style scoped>
.sounds {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-top: var(--space-2);
}
/* grey, not the accent: a guess, not a link */
.snd {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  min-height: 28px;
  font-size: var(--fs-xs);
  color: var(--fg-2);
  border: 1px solid var(--line); /* on a card or on the page alike */
  border-radius: var(--radius-pill);
  padding: 0 var(--space-3);
  white-space: nowrap;
}
.off .w {
  text-decoration: line-through;
  color: var(--muted);
}
.no {
  font-size: var(--fs-xs);
  min-height: 28px;
  padding: 0 0 0 var(--space-2);
  color: var(--accent);
}
</style>
