import { ref } from "vue";

// Keep failed edits available for retry; callers only clear their inputs after success.
export function useMutation() {
  const busy = ref(false);
  const error = ref("");
  const detail = ref(""); // what the server said, for the error's title
  async function run(action: () => Promise<void>) {
    if (busy.value) return;
    busy.value = true;
    error.value = detail.value = "";
    try {
      await action();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      detail.value = (e as { detail?: string } | null)?.detail ?? ""; // api.ts ApiError; no import: socket.check.ts runs this under node
    } finally {
      busy.value = false;
    }
  }
  return { busy, error, detail, run };
}
