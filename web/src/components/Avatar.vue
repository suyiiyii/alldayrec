<script setup lang="ts">
import { computed } from "vue";
import { initial, speakerColor } from "../lib/speakers";

// a speaker as a coloured circle with the first character of the name: 24 px in lists, 32 in a page's head
const { name, size = 24 } = defineProps<{ name?: string | null; size?: number }>();
const bg = computed(() => speakerColor(name));
</script>

<template>
  <span
    class="avatar"
    :style="{
      width: size + 'px',
      height: size + 'px',
      fontSize: Math.round(size * 0.5) + 'px',
      background: bg,
      /* the palette is dark in both themes; the tokens (我, unnamed) turn light in the dark one */
      color: bg.startsWith('var(') ? 'var(--surface)' : '#fff',
    }"
    aria-hidden="true"
    >{{ initial(name) }}</span
  >
</template>

<style scoped>
.avatar {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-weight: 600;
  line-height: 1;
}
</style>
