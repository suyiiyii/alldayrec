<script setup lang="ts">
import { computed, ref, useTemplateRef } from "vue";
import { t } from "../i18n";
import { get, hhmmss, post, type LabelSuggestions, type Row } from "../lib/api";
import { play, player, toggle } from "../lib/audio";
import { useMutation } from "../lib/mutation";
import { anonymous, guesses, speakerLabel } from "../lib/speakers";
import { toast } from "../lib/toast";
import { rowAt } from "../lib/track";
import Avatar from "./Avatar.vue";
import Icon from "./Icon.vue";

// One speaker of one closed conversation (conv: its participants and sentences; sug: its label suggestions), one per
// page (open(label); open("") lists the speakers first): who they may be (one tap renames), a bit of their voice,
// 这是我, a name box with recent names, 查看主页 for a named speaker. Renaming a label, or merging it into a name the
// conversation has already, can be undone for 10 s (the server keeps what it changed: POST /api/speakers/undo). A
// modal <dialog>: Escape or a tap on the dim backdrop closes it. reload: the page loads again what shows the speakers.
// rename, alias, hear, saving, error: for the page's own suggestion list (the conversation page's ⋯ 建议)
const props = defineProps<{
  conv: { id: string; device?: string | null; participants: string[]; rows: Row[] } | null;
  sug: LabelSuggestions | null;
  reload: () => Promise<unknown>;
}>();
const dlg = useTemplateRef<HTMLDialogElement>("dlg");
const editing = ref(""),
  newName = ref(""),
  names = ref<string[]>([]);
const { busy: saving, error, detail, run } = useMutation();
const named = (p: string) => p !== "我" && !anonymous(p);
const labels = computed(() => (props.conv?.participants ?? []).filter((p) => p !== "我" && p !== "S?"));
async function open(label: string) {
  if (label && !labels.value.includes(label)) return; // a sentence's speaker not yet among the participants
  editing.value = label;
  newName.value = "";
  error.value = "";
  if (!dlg.value?.open) {
    dlg.value?.showModal();
    dlg.value?.focus(); // the sheet, not its ✕ (no focus ring) nor the name box (no keyboard)
  }
  if (label) names.value = (await get<string[]>("/api/names")).slice(0, 12);
}
const close = () => dlg.value?.close();

// 可能是: the names it may have (guesses), a name said here with its sentence to hear
const starts = computed(() => (props.conv?.rows ?? []).map((r) => Date.parse(r.start)));
const maybe = computed(() =>
  guesses(props.sug, editing.value).map((g) => ({
    ...g,
    row: g.at ? props.conv?.rows[rowAt(starts.value, Date.parse(g.at))] : undefined,
  })),
);
function hear(label: string) {
  const v = props.sug?.clips[label],
    c = props.conv;
  if (!v || !c) return;
  play(
    `${c.id}|${label}`,
    v.clip
      ? `/api/voice-clip/${v.clip}`
      : `/api/audio?${new URLSearchParams({ start: v.clip_start!, end: v.clip_end!, ...(c.device && { device: c.device }) })}`,
  );
}
const playing = (label: string) => player.playing === `${props.conv?.id}|${label}`;

async function rename(from: string, to: string) {
  const c = props.conv;
  if (!c) return;
  const merge = c.participants.includes(to) || c.rows.some((r) => r.speaker === to);
  await run(async () => {
    const r = await post<{ renamed: number; voiceprints: number; learned?: boolean; undo: string }>(
      `/api/conversation/${encodeURIComponent(c.id)}/rename`,
      { from, to },
    );
    if (editing.value === from) close(); // not another speaker's sheet opened meanwhile
    toast(
      to === "我"
        ? t(r.learned ? "speaker.toMe" : "speaker.toMeShort")
        : merge
          ? t("conv.merged", { from: speakerLabel(from), to: speakerLabel(to) })
          : t(r.voiceprints ? "conv.renamedLearn" : "conv.renamed", { to: speakerLabel(to), n: r.renamed }, r.renamed),
      { undo: () => void undo(r.undo, from), ms: 10_000 },
    );
    await props.reload();
  });
}
async function undo(id: string, from: string) {
  await run(async () => {
    await post("/api/speakers/undo", { undo: id });
    toast(t("conv.renamedBack", { to: speakerLabel(from) }));
    await props.reload();
  });
}
const save = () => rename(editing.value, newName.value.trim());
async function alias(name: string, of: string) {
  await run(async () => {
    await post("/api/aliases", { alias: name, name: of });
    toast(t("conv.aliasSaved", { name, of }), {
      undo: () =>
        void run(async () => {
          await post("/api/aliases", { alias: name, name: "" }); // no name drops it
          toast(t("conv.aliasRemoved", { name }));
          await props.reload();
        }),
    });
    await props.reload();
  });
}
defineExpose({ open, close, rename, alias, hear, saving, error, detail });
</script>

<template>
  <dialog
    ref="dlg"
    class="sheet"
    aria-labelledby="sheet-who"
    tabindex="-1"
    @click.self="close"
  >
    <div class="in">
      <div class="phead">
        <h3 id="sheet-who">
          <template v-if="editing"><Avatar :name="editing" :size="40" />{{ speakerLabel(editing) }}</template>
          <template v-else>{{ t("conv.rename") }}</template>
        </h3>
        <button class="btn-ghost x" :aria-label="t('conv.close')" @click="close"><Icon name="x" /></button>
      </div>
      <div v-if="!editing" class="line">
        <span class="muted">{{ t("conv.renameWho") }}</span>
        <button v-for="l in labels" :key="l" class="chip" @click="open(l)">
          <Avatar :name="l" :size="20" />{{ speakerLabel(l) }}
        </button>
      </div>
      <template v-else>
        <div v-if="maybe.length" class="line">
          <span class="muted">{{ t("conv.maybe") }}</span>
          <span v-for="g in maybe" :key="g.name" class="guess">
            <button :disabled="saving" @click="rename(editing, g.name)">{{ g.name }}</button>
            <button v-if="g.row" class="btn-ghost at" :title="t('conv.hear')" @click="toggle(g.row)">
              <Icon :name="player.playing === g.row.id ? 'square' : 'play'" :size="12" />{{ hhmmss(g.row.start) }}
            </button>
          </span>
        </div>
        <div class="line">
          <button v-if="sug?.clips[editing]" class="btn" @click="hear(editing)">
            <Icon :name="playing(editing) ? 'square' : 'play'" :size="14" />{{
              playing(editing) ? t("conv.stop") : t("conv.hearSome")
            }}
          </button>
          <button v-if="anonymous(editing)" :disabled="saving" @click="rename(editing, '我')">
            {{ t("speaker.isMe") }}
          </button>
          <RouterLink v-if="named(editing)" class="btn" :to="`/p/${encodeURIComponent(editing)}`">
            {{ t("conv.profile") }}
          </RouterLink>
        </div>
        <form class="line" @submit.prevent="newName.trim() && save()">
          <input
            v-model="newName"
            enterkeyhint="done"
            maxlength="20"
            :placeholder="named(editing) ? t('rowMenu.newName') : t('conv.whoIs', { who: speakerLabel(editing) })"
          />
          <button class="btn-primary" :disabled="saving || !newName.trim()">{{ t("conv.save") }}</button>
        </form>
        <div v-if="names.length" class="line">
          <template v-for="n in names" :key="n">
            <button v-if="n !== editing" class="chip" @click="newName = n">{{ n }}</button>
          </template>
        </div>
        <p v-if="error" class="bad" role="alert" :title="detail">{{ error }}</p>
      </template>
    </div>
  </dialog>
</template>

<style scoped>
/* from the bottom edge on a phone, a card in the middle on a wide screen; .in fills the dialog, so a tap on the dialog
   itself is one on the backdrop */
.sheet {
  width: 100%;
  max-width: none;
  margin: auto 0 0;
  padding: 0;
  border: 0;
  outline: none; /* focused as it opens */
  border-radius: 16px 16px 0 0;
  background: var(--surface);
  color: var(--fg);
}
@media (min-width: 601px) {
  .sheet {
    max-width: 420px;
    margin: auto;
    border-radius: 16px;
  }
}
@media (prefers-reduced-motion: no-preference) and (max-width: 600px) {
  .sheet[open] {
    animation: up 0.2s ease-out;
  }
}
@keyframes up {
  from {
    transform: translateY(100%);
  }
}
.sheet::backdrop {
  background: rgb(0 0 0 / 0.4);
}
.in {
  padding: var(--space-4) var(--space-4) max(var(--space-4), env(safe-area-inset-bottom));
}
.phead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: calc(-1 * var(--space-2)) calc(-1 * var(--space-2)) var(--space-2) 0;
}
h3 {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
}
.x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  padding: 0;
  color: var(--muted);
}
.line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-sm);
}
.line + .line {
  margin-top: var(--space-3);
}
.line .chip {
  gap: 6px;
}
.guess {
  display: inline-flex;
  align-items: center;
}
.line .btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.line input {
  flex: 1;
  min-width: 0;
  min-height: 40px;
  padding: 0 10px;
}
.line input:focus {
  outline: none;
  border-color: var(--accent);
}
/* a time to listen from: a finger-sized target around a small label */
.at {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  min-height: 32px;
  padding: 0 var(--space-1);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.bad {
  margin: var(--space-3) 0 0;
}
</style>
