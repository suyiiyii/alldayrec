<script setup lang="ts">
import Icon from "./Icon.vue";

// inApp: what the header shows inside the Android app, which has its own navigation (nothing: hidden)
defineProps<{ inApp?: boolean }>();
</script>

<template>
  <header :class="{ bare: !inApp }">
    <div class="bar">
      <h1>alldayrec</h1>
      <nav>
        <RouterLink to="/" exact-active-class="on"
          ><Icon name="mic" :size="18" />实时字幕</RouterLink
        >
        <RouterLink
          to="/conversations"
          :class="{ on: $route.path === '/conversations' || $route.path.startsWith('/c/') }"
          ><Icon name="message-circle" :size="18" />对话</RouterLink
        >
        <RouterLink to="/calendar" exact-active-class="on"
          ><Icon name="calendar" :size="18" />日历</RouterLink
        >
        <RouterLink to="/speakers" exact-active-class="on"
          ><Icon name="users" :size="18" />说话人</RouterLink
        >
        <RouterLink to="/progress" exact-active-class="on"
          ><Icon name="activity" :size="18" />进度</RouterLink
        >
        <RouterLink to="/connect" exact-active-class="on"
          ><Icon name="smartphone" :size="18" />连接手机</RouterLink
        >
      </nav>
      <slot />
    </div>
  </header>
</template>

<style scoped>
header {
  position: sticky;
  top: 0;
  background: var(--surface);
  box-shadow: var(--shadow);
  border-bottom: 1px solid var(--line);
  z-index: 2;
}
.bar {
  max-width: 860px;
  margin: 0 auto;
  padding: 0 var(--space-4);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  column-gap: var(--space-4);
}
h1 {
  font-size: var(--fs-lg);
  margin: 0;
  color: var(--accent);
  white-space: nowrap;
}
nav {
  display: flex;
  font-size: var(--fs-sm);
  white-space: nowrap;
}
/* each link is as tall as a finger: 44 px */
nav a {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  padding: 0 var(--space-2);
  color: var(--muted);
}
nav a.on {
  color: var(--accent);
  font-weight: 600;
}
:global(.in-app nav),
:global(.in-app h1) {
  display: none;
}
:global(.in-app header.bare) {
  display: none;
}
/* a phone: the name on a line of its own, smaller, the links under it */
@media (max-width: 600px) {
  h1 {
    flex-basis: 100%;
    font-size: var(--fs-md);
    line-height: 22px;
    padding-top: var(--space-2);
  }
  .bar {
    padding: 0 var(--space-3);
  }
  /* the icon above the word, the six spread over the width */
  nav {
    flex: 1;
    justify-content: space-between;
    font-size: var(--fs-xs);
  }
  nav a {
    flex-direction: column;
    justify-content: center;
    gap: 0;
    min-height: 48px;
    padding: 0 var(--space-1);
    line-height: 1.3;
  }
}
</style>
