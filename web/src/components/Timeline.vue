<script setup lang="ts">
import { hhmmss, type LiveTopic } from "../lib/api";
import { speakerLabel } from "../lib/speakers";

// open: the last topic is still being discussed
defineProps<{ topics: LiveTopic[]; open?: boolean }>();
defineEmits<{ jump: [start: string] }>();
</script>

<template>
  <div
    v-for="(t, i) in topics"
    :key="t.id ?? i + t.start"
    class="topic"
    :class="{ now: open && i === topics.length - 1 }"
  >
    <div class="head">
      <a class="at" :href="'#' + t.start" @click.prevent="$emit('jump', t.start)">{{
        hhmmss(t.start)
      }}</a>
      <b>{{ t.title }}</b>
      <span v-if="open && i === topics.length - 1" class="badge">{{
        $t("topicTimeline.now")
      }}</span>
    </div>
    <ul>
      <li v-for="p in t.points" :key="p">{{ p }}</li>
      <li v-for="d in t.decisions" :key="'d' + d" class="dec">
        {{ $t("topicTimeline.decided", { d }) }}
      </li>
      <li v-for="(x, j) in t.todos" :key="'t' + j" class="todo">
        {{ $t("topicTimeline.todo")
        }}<span class="who">{{ x.who ? speakerLabel(x.who) : $t("common.unknown") }}</span>
        {{ x.what }}
      </li>
      <li v-for="q in t.open_questions" :key="'q' + q" class="q">
        {{ $t("topicTimeline.open", { q }) }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
.topic {
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}
.topic.now b {
  color: var(--accent);
}
.head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.at {
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}
.badge {
  font-size: var(--fs-xs);
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: var(--radius-pill);
  padding: 0 var(--space-2);
}
ul {
  margin: 2px 0 0;
  padding-left: 18px;
  font-size: var(--fs-sm);
}
.dec {
  color: var(--accent);
}
.todo .who {
  color: var(--warn);
}
.q {
  color: var(--muted);
}
</style>
