<script setup lang="ts">
import { t } from "../i18n";
import { dismiss, toasted } from "../lib/toast";

function undo() {
  const u = toasted.undo;
  dismiss();
  u?.();
}
</script>

<template>
  <div v-if="toasted.msg" :key="toasted.n" class="toast" role="status">
    <span>{{ toasted.msg }}</span>
    <button v-if="toasted.undo" class="btn-ghost" @click="undo">{{ t("common.undo") }}</button>
  </div>
</template>

<style scoped>
.toast {
  position: fixed;
  left: 50%;
  /* above the tabs */
  bottom: calc(var(--space-6) + max(var(--tabbar), env(safe-area-inset-bottom)));
  transform: translateX(-50%);
  z-index: 10;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: max-content; /* centred by left 50%: without it the box shrinks to half the screen */
  max-width: calc(100vw - 2 * var(--space-4));
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius);
  background: var(--fg);
  color: var(--surface);
  font-size: var(--fs-sm);
  box-shadow: var(--shadow);
}
.toast button {
  flex: none;
  color: var(--surface);
  font-weight: 600;
  text-decoration: underline;
}
</style>
