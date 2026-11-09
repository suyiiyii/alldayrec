<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useTemplateRef } from "vue";
import { t } from "../i18n";
import { get, hhmmss, post, type LabelSuggestions, type NameCheck as Check, type Row } from "../lib/api";
import { play, player, toggle } from "../lib/audio";
import { useMutation } from "../lib/mutation";
import { anonymous, guesses, speakerLabel } from "../lib/speakers";
import { toast } from "../lib/toast";
import { rowAt } from "../lib/track";
import Avatar from "./Avatar.vue";
import Icon from "./Icon.vue";
import NameCheck from "./NameCheck.vue";

// One speaker of one closed conversation (conv: its participants and sentences; sug: its label suggestions), one per
// page (open(label); open("") lists the speakers first): who they may be (one tap renames), a bit of their voice,
// 这是我, a name box with recent names, 查看主页 for a named speaker. Renaming a label, or merging it into a name the
// conversation has already, can be undone for 10 s (the server keeps what it changed: POST /api/speakers/undo). A
// modal <dialog>: Escape or a tap on the dim backdrop closes it. reload: the page loads again what shows the speakers.
// A typed name is checked first (NameCheck: a person it may be already); after a rename, the label's voice in earlier
// conversations still unnamed is offered to rename too (一起改: POST /api/speakers/name with those voices); the rename's
// 撤销 waits till the sheet closes (a toast under the modal sheet is out of reach), or goes into 一起改's, one for both.
// rename, alias, hear, saving, error: for the page's own suggestion list (the conversation page's ⋯ 建议)
const props = defineProps<{
  conv: { id: string; device?: string | null; participants: string[]; rows: Row[] } | null;
  sug: LabelSuggestions | null;
  reload: () => Promise<unknown>;
}>();
const dlg = useTemplateRef<HTMLDialogElement>("dlg");
const editing = ref(""),
  newName = ref(""),
  names = ref<string[]>([]),
  ask = ref<{ to: string; check: Check } | null>(null),
  also = ref<(Also & { to: string; done: Done }) | null>(null),
  box = useTemplateRef<HTMLInputElement>("box");
let sure = false; // 是另一个人: the next typed name is not checked again
const { busy: saving, error, detail, run } = useMutation();
const named = (p: string) => p !== "我" && !anonymous(p);
const labels = computed(() => (props.conv?.participants ?? []).filter((p) => p !== "我" && p !== "S?"));
async function open(label: string) {
  if (label && !labels.value.includes(label)) return; // a sentence's speaker not yet among the participants
  editing.value = label;
  newName.value = "";
  error.value = "";
  ask.value = also.value = null;
  sure = false;
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

type Also = { voices: string[]; n: number; group: string; suggestion?: string };
type Renamed = { renamed: number; voiceprints: number; learned?: boolean; undo: string; also: Also | null };
type Done = { msg: string; undo: string; back: string }; // a rename's toast: its undo id, what 撤销 then says
const done = (d: Done) => toast(d.msg, { undo: () => void undo(d.undo, d.back), ms: 10_000 });
async function rename(from: string, to: string, check = false) {
  const c = props.conv;
  if (!c) return;
  const merge = c.participants.includes(to) || c.rows.some((r) => r.speaker === to);
  await run(async () => {
    const r = await post<Renamed | { check: Check }>(
      `/api/conversation/${encodeURIComponent(c.id)}/rename`,
      { from, to, ...(check && { check: true }) },
    );
    if ("check" in r) {
      ask.value = { to, check: r.check };
      return;
    }
    ask.value = null;
    const d = {
      msg:
        to === "我"
          ? t(r.learned ? "speaker.toMe" : "speaker.toMeShort")
          : merge
            ? t("conv.merged", { from: speakerLabel(from), to: speakerLabel(to) })
            : t(r.voiceprints ? "conv.renamedLearn" : "conv.renamed", { to: speakerLabel(to), n: r.renamed }, r.renamed),
      undo: r.undo,
      back: t("conv.renamedBack", { to: speakerLabel(from) }),
    };
    // not another speaker's sheet opened meanwhile, nor the sheet closed
    if (editing.value === from && r.also && dlg.value?.open) [also.value, editing.value] = [{ ...r.also, to, done: d }, ""];
    else {
      if (editing.value === from) close();
      done(d);
    }
    await props.reload();
  });
}
async function undo(id: string, msg: string) {
  await run(async () => {
    await post("/api/speakers/undo", { undo: id });
    toast(msg);
    await props.reload();
  });
}
const save = () => rename(editing.value, newName.value.trim(), !sure);
function other(free: string) {
  [newName.value, ask.value, sure] = [free, null, true];
  box.value?.focus();
}
let joining = false; // 一起改 on its way: its answer decides which 撤销 (together)
function closed() {
  // the sheet closed (✕, 不用了, Escape, the backdrop, the page left) with 一起改 not taken: now the rename's 撤销 is in reach
  if (joining) return;
  if (also.value) done(also.value.done);
  also.value = null;
}
onBeforeUnmount(closed);
async function together() {
  const a = also.value;
  if (!a || saving.value) return;
  joining = true;
  let ok = false;
  await run(async () => {
    const { to, voices, group, suggestion } = a;
    const r = await post<{ mixed: true } | { renamed: number; undo: string | null }>("/api/speakers/name", {
      name: to,
      voices,
      group,
      suggestion,
      undo: a.done.undo, // the rename's: one 撤销 takes back both
    });
    if ("mixed" in r) {
      error.value = t("nameCheck.twoPeople");
      return;
    }
    ok = true;
    if (also.value === a) {
      // not another speaker's sheet opened meanwhile
      also.value = null;
      close();
    }
    const undo = r.undo ?? a.done.undo; // the rename's taken into it
    // none renamed (named meanwhile): the rename's toast, its 撤销 the one undo
    done(r.renamed ? { msg: t("nameCheck.togetherDone", { n: r.renamed }, r.renamed), undo, back: t("common.undone") } : { ...a.done, undo });
    await props.reload();
  });
  joining = false;
  if (ok) return;
  // not taken (mixed, an error): with another speaker's sheet opened meanwhile, not its error, the rename's 撤销 now;
  // with the sheet closed while it was on its way, the same (closed)
  if (also.value !== a) {
    error.value = detail.value = "";
    done(a.done);
  } else if (!dlg.value?.open) closed();
}
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
    @close="closed"
  >
    <div class="in">
      <div class="phead">
        <h3 id="sheet-who">
          <template v-if="also"><Avatar :name="also.to" :size="40" />{{ speakerLabel(also.to) }}</template>
          <template v-else-if="editing"><Avatar :name="editing" :size="40" />{{ speakerLabel(editing) }}</template>
          <template v-else>{{ t("conv.rename") }}</template>
        </h3>
        <button class="btn-ghost x" :aria-label="t('conv.close')" @click="close"><Icon name="x" /></button>
      </div>
      <template v-if="also">
        <p class="line">{{ t("nameCheck.found", { n: also.n }, also.n) }}</p>
        <div class="line">
          <button class="btn-primary" :disabled="saving" @click="together">
            {{ t("nameCheck.together", { name: speakerLabel(also.to) }) }}
          </button>
          <button class="btn-ghost" @click="close">{{ t("nameCheck.skip") }}</button>
        </div>
        <p v-if="error" class="bad" role="alert" :title="detail">{{ error }}</p>
      </template>
      <div v-else-if="!editing" class="line">
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
            ref="box"
            v-model="newName"
            @input="ask = null"
            enterkeyhint="done"
            maxlength="20"
            :placeholder="named(editing) ? t('rowMenu.newName') : t('conv.whoIs', { who: speakerLabel(editing) })"
          />
          <button class="btn-primary" :disabled="saving || !newName.trim()">{{ t("conv.save") }}</button>
        </form>
        <NameCheck
          v-if="ask"
          :to="ask.to"
          :check="ask.check"
          :busy="saving"
          @name="(n) => rename(editing, n)"
          @other="other"
        />
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
