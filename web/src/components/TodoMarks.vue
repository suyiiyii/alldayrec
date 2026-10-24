<script setup lang="ts">
// 完成 / 不对 on one todo: a feedback line naming it (TODO_MARK, then its text); a marked todo leaves the due list
// and the recaps. Marked lines stay in the conversation's feedback for the read-back
import { useMutation } from "../lib/mutation";
import { post, TODO_MARK, type Feedback } from "../lib/api";

const props = defineProps<{ conv: string; text: string }>();
const emit = defineEmits<{ marked: [feedback: Feedback[]] }>();
const { busy, error, run } = useMutation();
async function mark(m: keyof typeof TODO_MARK) {
  await run(async () => {
    emit(
      "marked",
      await post<Feedback[]>(`/api/conversation/${props.conv}/feedback`, {
        text: TODO_MARK[m] + props.text,
      }),
    );
  });
}
</script>

<template>
  <span class="marks">
    <button :disabled="busy" @click="mark('done')">完成</button>
    <button :disabled="busy" @click="mark('wrong')">不对</button>
    <span v-if="error" class="bad" role="alert">{{ error }}</span>
  </span>
</template>

<style scoped>
.marks {
  white-space: nowrap;
}
.marks button {
  min-height: 32px;
  padding: 0 var(--space-2);
  margin-left: 6px;
}
/* the buttons stay together; an error wraps inside the page instead of running off a phone's edge */
.marks .bad {
  margin-left: 6px;
  white-space: normal;
  overflow-wrap: anywhere;
}
</style>
