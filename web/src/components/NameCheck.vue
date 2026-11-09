<script setup lang="ts">
// Before a typed name is given (server name_check): the person it may be already, the same name in use or a voice near
// another name. name: give that name (one already in use: a merge, or the typed one anyway); other: another person of
// the same name, free a name for them to start from
import { computed } from "vue";
import { t } from "../i18n";
import type { NameCheck } from "../lib/api";
import { likeness } from "../lib/speakers";

const props = defineProps<{ to: string; check: NameCheck; busy: boolean }>();
defineEmits<{ name: [string]; other: [string] }>();
const s = computed(() => props.check.same);
</script>

<template>
  <div class="ask" role="alert">
    <p v-if="s">
      {{
        t(s.convs ? "nameCheck.same" : "nameCheck.sameBank", { name: s.name, n: s.convs }, s.convs)
      }}<template v-if="s.alike !== null">{{
        t(s.alike ? "nameCheck.alike" : "nameCheck.unlike")
      }}</template>
    </p>
    <p v-for="x in check.like" :key="x.name">
      {{ t("nameCheck.like", { name: x.name, like: likeness(x.score) }) }}
    </p>
    <div class="row">
      <button v-if="s" class="btn-primary" :disabled="busy" @click="$emit('name', s.name)">
        {{ t("nameCheck.isName", { name: s.name }) }}
      </button>
      <button v-for="x in check.like" :key="x.name" :disabled="busy" @click="$emit('name', x.name)">
        {{ t("nameCheck.isName", { name: x.name }) }}
      </button>
      <button v-if="s" class="btn-ghost" :disabled="busy" @click="$emit('other', s.free)">
        {{ t("nameCheck.other") }}
      </button>
      <button v-else class="btn-ghost" :disabled="busy" @click="$emit('name', to)">
        {{ t("nameCheck.anyway", { name: to }) }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.ask {
  margin: var(--space-3) 0;
  padding: var(--space-3);
  border-radius: var(--radius);
  background: var(--bg);
  font-size: var(--fs-sm);
}
.ask p {
  margin: 0 0 var(--space-2);
}
.row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
</style>
