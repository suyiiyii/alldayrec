<script setup lang="ts">
// inApp: what the header shows inside the Android app, which has its own navigation (nothing: hidden)
defineProps<{ inApp?: boolean }>();
</script>

<template>
  <header :class="{ bare: !inApp }">
    <div class="bar">
      <h1>alldayrec</h1>
      <nav>
        <RouterLink to="/" exact-active-class="on">实时字幕</RouterLink>
        <RouterLink
          to="/conversations"
          :class="{ on: $route.path === '/conversations' || $route.path.startsWith('/c/') }"
          >对话</RouterLink
        >
        <RouterLink to="/calendar" exact-active-class="on">日历</RouterLink>
        <RouterLink to="/speakers" exact-active-class="on">说话人</RouterLink>
        <RouterLink to="/progress" exact-active-class="on">进度</RouterLink>
        <RouterLink to="/connect" exact-active-class="on">连接手机</RouterLink>
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
  nav {
    margin-left: -6px;
  }
  nav a {
    padding: 0 6px;
  }
}
</style>
