import { reactive } from "vue";
import { t } from "../i18n";
import { post, type Mark } from "./api";
import { toast } from "./toast";

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
