<script setup lang="ts">
import { computed, nextTick, ref, useTemplateRef } from "vue";
import { t } from "../i18n";
import { get, hhmm, hhmmss, post, type Row } from "../lib/api";
import { anonymous, speakerLabel } from "../lib/speakers";
import { setStar, starred } from "../lib/stars";
import { toast } from "../lib/toast";
import Avatar from "./Avatar.vue";
import Icon from "./Icon.vue";

// What a long press (or a right click) on a sentence offers, one per page (open(row, x, y, conv)): 复制, 收藏 and
// 纠正说话人, this sentence alone (POST /api/sentences/{id}/speaker; 撤销 for 10 s). A phone and the app get a sheet
// from the bottom, a mouse a small menu at the pointer: one modal <dialog> either way, so Escape and a tap outside
// close it. Only a final row of a closed conversation (conv) can be starred or fixed: a draft, the sentence being said
// and an open conversation's rows (all relabelled when it closes) are only copied. changed: a speaker was fixed or
// put back, the page reloads what shows it
const emit = defineEmits<{ changed: [] }>();
type Conv = { id: string; participants: string[] };
const dlg = useTemplateRef<HTMLDialogElement>("dlg");
const row = ref<Row | null>(null),
  conv = ref<Conv | null>(null),
  fixing = ref(false),
  sheet = ref(true),
  at = ref({ left: "0px", top: "0px" });
let x0 = 0,
  y0 = 0;

function open(r: Row, x: number, y: number, c?: Conv | null) {
  row.value = r;
  conv.value = r.state === "final" ? (c ?? null) : null;
  fixing.value = false;
  sheet.value =
    matchMedia("(max-width: 600px)").matches ||
    document.documentElement.classList.contains("in-app");
  [x0, y0] = [x, y];
  dlg.value?.showModal();
  place();
}
// the small menu at the pointer, kept on the screen (measured once it shows, again when the fixer replaces the items)
async function place() {
  if (sheet.value) return;
  await nextTick();
  const r = dlg.value!.getBoundingClientRect();
  at.value = {
    left: Math.max(8, Math.min(x0, innerWidth - r.width - 8)) + "px",
    top: Math.max(8, Math.min(y0, innerHeight - r.height - 8)) + "px",
  };
}
const close = () => dlg.value?.close();
defineExpose({ open });

const head = computed(() => [...(row.value?.text ?? "")].slice(0, 40).join(""));
const more = computed(() => [...(row.value?.text ?? "")].length > 40);

// 复制: 「14:05 说话人 2：……」. A page over plain http (the app's) has no navigator.clipboard: a selected textarea and
// execCommand, inside the tap, in the dialog (outside it everything is inert while it is open)
async function copy() {
  const r = row.value!;
  const text = t("rowMenu.line", {
    at: hhmm(r.start),
    who: speakerLabel(r.speaker),
    text: r.text ?? "",
  });
  try {
    if (navigator.clipboard) await navigator.clipboard.writeText(text);
    else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.readOnly = true;
      ta.className = "clip";
      dlg.value!.append(ta);
      ta.select();
      ta.setSelectionRange(0, text.length);
      const ok = document.execCommand("copy");
      ta.remove();
      if (!ok) throw new Error();
    }
    toast(t("rowMenu.copied"));
  } catch {
    toast(t("rowMenu.copyFailed"));
  }
  close();
}

async function star() {
  const r = row.value!,
    c = conv.value!,
    on = !starred.value.has(r.id);
  close();
  try {
    await setStar(r.id, c.id, on);
    toast(t(on ? "rowMenu.starred" : "rowMenu.unstarred"));
  } catch (e) {
    toast((e as Error).message);
  }
}

// 纠正说话人: 我 first, then the names, then 说话人 n; or a new name (the names used elsewhere offered as it is typed)
const name = ref(""),
  names = ref<string[]>([]);
const choices = computed(() => {
  const ps = conv.value?.participants ?? [];
  return [
    "我",
    ...ps.filter((p) => p !== "我" && !anonymous(p)),
    ...ps.filter((p) => /^S\d+$/.test(p)),
  ].filter((p) => p !== row.value?.speaker);
});
async function toFix() {
  fixing.value = true;
  name.value = "";
  void place();
  names.value = await get<string[]>("/api/names").catch(() => []);
}
async function fix(to: string) {
  if (!to) return;
  const r = row.value!,
    c = conv.value!,
    from = r.speaker ?? "",
    music = r.music;
  close();
  try {
    const res = await post<{ voiceprints: number; undo: string }>(
      `/api/sentences/${encodeURIComponent(r.id)}/speaker`,
      { conv: c.id, to },
    );
    r.speaker = to; // the live page's row; the conversation page loads again
    r.music = undefined; // given to someone, a ♪ row is a voice (the server clears it too)
    emit("changed");
    toast(t(res.voiceprints ? "rowMenu.fixedLearn" : "rowMenu.fixed", { to: speakerLabel(to) }), {
      undo: () => void undo(r, res.undo, from, music),
      ms: 10_000,
    });
  } catch (e) {
    toast((e as Error).message);
  }
}
async function undo(r: Row, id: string, from: string, music?: number) {
  try {
    await post("/api/speakers/undo", { undo: id });
    r.speaker = from;
    r.music = music;
    emit("changed");
    toast(t("rowMenu.fixedBack", { to: speakerLabel(from) }));
  } catch (e) {
    toast((e as Error).message);
  }
}
</script>

<template>
  <dialog
    ref="dlg"
    class="rowmenu"
    :class="sheet ? 'sheet' : 'pop'"
    :style="sheet ? undefined : at"
    :aria-label="t('rowMenu.title')"
    @click.self="close"
  >
    <div v-if="row" class="in">
      <div class="head">
        <span class="meta">{{ hhmmss(row.start) }} · {{ speakerLabel(row.speaker) }}</span>
        <span class="quote">{{ head }}{{ more ? "…" : "" }}</span>
      </div>
      <template v-if="!fixing">
        <button class="item" @click="copy"><Icon name="copy" />{{ t("rowMenu.copy") }}</button>
        <template v-if="conv">
          <button class="item" @click="star">
            <Icon name="star" :class="{ on: starred.has(row.id) }" />{{
              starred.has(row.id) ? t("rowMenu.unstar") : t("rowMenu.star")
            }}
          </button>
          <button class="item" @click="toFix">
            <Icon name="user-pen" />{{ t("rowMenu.fix") }}
          </button>
        </template>
      </template>
      <form v-else class="fix" @submit.prevent="fix(name.trim())">
        <p class="who">{{ t("rowMenu.fixWho") }}</p>
        <button v-for="p in choices" :key="p" type="button" class="item" @click="fix(p)">
          <Avatar :name="p" />{{ speakerLabel(p) }}
        </button>
        <div class="new">
          <input
            v-model="name"
            list="rowmenu-names"
            maxlength="20"
            enterkeyhint="done"
            :placeholder="t('rowMenu.newName')"
          />
          <button class="btn-primary" :disabled="!name.trim()">{{ t("rowMenu.save") }}</button>
          <datalist id="rowmenu-names">
            <option v-for="n in names" :key="n" :value="n" />
          </datalist>
        </div>
      </form>
    </div>
  </dialog>
</template>

<style scoped>
.rowmenu {
  padding: 0;
  border: 0;
  outline: none; /* focused as it opens: it, not its first item */
  background: var(--surface);
  color: var(--fg);
}
/* a phone: from the bottom edge, full width; .in fills it, so a tap on the dialog itself is the backdrop */
.sheet {
  width: 100%;
  max-width: none;
  margin: auto 0 0;
  border-radius: 16px 16px 0 0;
}
.sheet::backdrop {
  background: rgb(0 0 0 / 0.4);
}
.sheet .in {
  padding: var(--space-2) 0 max(var(--space-2), env(safe-area-inset-bottom));
}
@media (prefers-reduced-motion: no-preference) {
  .sheet[open] {
    animation: up 0.2s ease-out;
  }
}
@keyframes up {
  from {
    transform: translateY(100%);
  }
}
/* a mouse: a small menu where the click was */
.pop {
  position: fixed;
  inset: auto;
  margin: 0;
  width: 260px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: 0 4px 16px rgb(0 0 0 / 0.12);
}
.pop::backdrop {
  background: transparent;
}
.pop .in {
  padding: var(--space-1) 0;
}
.head {
  display: flex;
  flex-direction: column;
  padding: var(--space-2) var(--space-4) var(--space-3);
  border-bottom: 1px solid var(--line);
  margin-bottom: var(--space-1);
}
.meta {
  color: var(--muted);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}
.quote {
  font-size: var(--fs-sm);
  color: var(--fg-2);
}
.pop .head {
  padding: var(--space-2) var(--space-3);
}
.item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-height: 48px;
  padding: 0 var(--space-4);
  border: 0;
  border-radius: 0;
  background: none;
  color: var(--fg);
  font-size: var(--fs-md);
  text-align: left;
}
.item:active {
  background: var(--accent-soft);
}
.item > .icon {
  color: var(--fg-2);
}
.item > .icon.on {
  color: var(--warn);
  fill: currentColor;
}
.pop .item {
  min-height: 40px;
  padding: 0 var(--space-3);
  font-size: var(--fs-sm);
}
@media (hover: hover) {
  .item:hover {
    background: var(--accent-soft);
  }
}
.who {
  margin: 0;
  padding: var(--space-1) var(--space-4);
  color: var(--muted);
  font-size: var(--fs-sm);
}
.fix {
  max-height: 60vh;
  overflow-y: auto;
}
.new {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
}
.new input {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  padding: 0 var(--space-3);
}
.new input:focus {
  outline: none;
  border-color: var(--accent);
}
.new .btn-primary {
  flex: none;
  padding: 0 var(--space-4);
}
.pop .who {
  padding: var(--space-1) var(--space-3);
}
.pop .new {
  padding: var(--space-2) var(--space-3);
}
.pop .new input,
.pop .new .btn-primary {
  min-height: 36px;
}
/* 复制's textarea over plain http: in the dialog, out of sight */
:deep(.clip) {
  position: fixed;
  top: 0;
  opacity: 0;
}
</style>
