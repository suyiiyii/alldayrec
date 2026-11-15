import { reactive } from "vue";
import { t } from "../i18n.ts";
import { post, type AutoMark, type Mark } from "./api.ts";
import { toast } from "./toast.ts";

// 完成 / 不对 on todos by id (server db.todo_id) and the undo. A mark set on this page wins over the one a todo came
// with, so every list (they show open todos only) and the conversation page change at once, and an undo brings a
// todo back where it was
export const marks = reactive<Record<string, Mark | null>>({});
type Markable = { id?: string; mark?: Mark | null };
export const markOf = (t: Markable) => (t.id && t.id in marks ? marks[t.id] : (t.mark ?? null));
export const open = <T extends Markable>(ts: T[]) => ts.filter((t) => !markOf(t));

export async function setMarks(ids: string[], mark: Mark | null) {
  const one = `/api/todos/${encodeURIComponent(ids[0])}/mark`;
  if (ids.length > 1) await post("/api/todos/marks", { ids, mark });
  else if (mark) await post(one, { mark }, "PUT");
  else await post(one, undefined, "DELETE");
  for (const id of ids) marks[id] = mark;
}

// the undo of a todo's automatic marks (server db.undo_auto): it is open again. The marks undone here, by the
// evidence (a later one, on a newer sentence, shows again), for the lists until they load again
const markKey = (a: AutoMark) => [a.kind, a.conv, a.sentence ?? a.at, a.judged_at].join("|");
const undone = reactive(new Set<string>());
export const autoUndone = (a?: AutoMark | null) => !!a && undone.has(markKey(a));
export async function undoAuto(id: string, a: AutoMark) {
  await post(`/api/todos/${encodeURIComponent(id)}/auto`, undefined, "DELETE");
  undone.add(markKey(a));
}

// marked, and a toast saying so with 撤销
export async function markWithUndo(ids: string[], mark: Mark, msg: string) {
  await setMarks(ids, mark);
  toast(msg, {
    undo: () =>
      void setMarks(ids, null).then(
        () => toast(t("common.undone")),
        (e: Error) => toast(e.message),
      ),
  });
}
