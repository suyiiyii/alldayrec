import { computed, ref } from "vue";
import { get, post } from "./api";

// 收藏: the starred sentences, the newest star first (GET /api/stars), as they read now; loaded by each page that shows
// rows (their ★) or the list (我的)
export interface Star {
  id: string; // the sentence's
  conv: string;
  at: string; // when it was starred
  start: string;
  end: string;
  speaker?: string | null;
  text?: string | null;
  conv_start: string;
  title?: string | null; // its conversation's summary's
}
export const stars = ref<Star[]>([]);
export const starred = computed(() => new Set(stars.value.map((s) => s.id)));
export async function loadStars() {
  stars.value = await get<Star[]>("/api/stars");
}
export async function setStar(id: string, conv: string, on: boolean) {
  const path = `/api/sentences/${encodeURIComponent(id)}/star`;
  await (on ? post(path, { conv }, "PUT") : post(path, undefined, "DELETE"));
  await loadStars();
}
