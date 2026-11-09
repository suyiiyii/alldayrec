<script setup lang="ts">
import { onMounted, ref } from "vue";
import AppHeader from "../components/AppHeader.vue";
import Icon from "../components/Icon.vue";
import { t } from "../i18n";
import { errorTitle, get, post } from "../lib/api";

// 热词和术语: the names, products and jargon the speech engines listen for and the summaries spell by (GET /api/terms);
// the people bank's names go with them by themselves, shown under the list, not editable here
interface Terms {
  terms: string[];
  names: string[];
}
const data = ref<Terms | null>(null);
const text = ref(""),
  editing = ref(""), // the term being changed: Enter replaces it
  error = ref(""),
  errorTip = ref(""),
  box = ref<HTMLInputElement>();
onMounted(async () => (data.value = await get<Terms>("/api/terms")));

async function save(term: string, old = "") {
  error.value = "";
  try {
    data.value = await post<Terms>("/api/terms", { term, old });
    text.value = editing.value = "";
  } catch (e) {
    error.value = (e as Error).message;
    errorTip.value = errorTitle(e);
  }
}
function edit(term: string) {
  editing.value = text.value = term;
  box.value?.focus();
}
</script>

<template>
  <AppHeader />
  <main>
    <h1>{{ t("title.terms") }}</h1>
    <p class="muted">{{ t("terms.intro") }}</p>
    <section class="card">
      <div class="add">
        <input
          ref="box"
          v-model="text"
          :placeholder="editing ? t('terms.editing', { term: editing }) : t('terms.add')"
          maxlength="30"
          @keydown.enter="text.trim() && save(text.trim(), editing)"
          @keydown.esc="text = editing = ''"
        />
        <button
          class="btn"
          :disabled="!text.trim()"
          @click="save(text.trim(), editing)"
        >
          {{ editing ? t("terms.save") : t("terms.addButton") }}
        </button>
      </div>
      <p class="bad" :title="errorTip">{{ error }}</p>
      <p v-if="data && !data.terms.length" class="muted">{{ t("terms.empty") }}</p>
      <div class="chips">
        <span v-for="w in data?.terms" :key="w" class="tag" :class="{ on: w === editing }"
          ><button class="word" :title="t('terms.edit')" @click="edit(w)">{{ w }}</button
          ><button class="x" :title="t('terms.remove')" @click="save('', w)">
            <Icon name="x" :size="14" /></button
        ></span>
      </div>
    </section>
    <section v-if="data?.names.length" class="card">
      <h2>{{ t("terms.names") }}</h2>
      <p class="muted">{{ t("terms.namesSub") }}</p>
      <div class="chips">
        <span v-for="w in data.names" :key="w" class="tag">{{ w }}</span>
      </div>
    </section>
  </main>
</template>

<style scoped>
h1 {
  margin: var(--space-3) 0 var(--space-2);
}
h2 {
  margin: 0 0 var(--space-1);
  font-size: var(--fs-md);
}
main > .muted,
.card .muted {
  font-size: var(--fs-sm);
}
.card + .card {
  margin-top: var(--space-3);
}
.add {
  display: flex;
  gap: var(--space-2);
}
.add input {
  flex: 1;
  min-width: 0;
  font: inherit;
  padding: 6px 10px;
}
.bad:empty {
  display: none;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: var(--space-2);
}
.tag button {
  border: 0;
  background: none;
  font: inherit;
  color: inherit;
  cursor: pointer;
  min-height: 28px;
}
.tag .word {
  padding: 0;
}
/* × a 32 px target (a finger, not a mouse), in the tag's right padding */
.tag .x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  min-height: 32px;
  padding: 0;
  margin-right: calc(-1 * var(--space-3));
  color: var(--muted);
}
</style>
