<script setup lang="ts">
import { useRoute } from "vue-router";
import Icon, { type IconName } from "./Icon.vue";

// inApp: what the header shows inside the Android app, which has its own navigation (nothing: hidden)
defineProps<{ inApp?: boolean }>();

// four places, each owning the pages under it (the live page is today's; devices, progress, connect are 我的)
const TABS: { to: string; label: string; icon: IconName; under: RegExp }[] = [
  { to: "/", label: "今天", icon: "home", under: /^\/(live)?$/ },
  {
    to: "/conversations",
    label: "对话",
    icon: "message-circle",
    under: /^\/(conversations|c\/|calendar)/,
  },
  { to: "/speakers", label: "人物", icon: "users", under: /^\/(speakers|p\/)/ },
  { to: "/me", label: "我的", icon: "user", under: /^\/(me|connect|progress|device\/|record)/ },
];
const route = useRoute();
</script>

<template>
  <header :class="{ bare: !inApp }">
    <div class="bar">
      <h1>alldayrec</h1>
      <nav class="nav-top">
        <RouterLink
          v-for="t in TABS"
          :key="t.to"
          :to="t.to"
          :class="{ on: t.under.test(route.path) }"
          ><Icon :name="t.icon" :size="18" />{{ t.label }}</RouterLink
        >
      </nav>
      <slot />
    </div>
  </header>
  <!-- a phone: the same four as tabs at the bottom -->
  <nav class="nav-tabs">
    <RouterLink v-for="t in TABS" :key="t.to" :to="t.to" :class="{ on: t.under.test(route.path) }"
      ><Icon :name="t.icon" :size="22" />{{ t.label }}</RouterLink
    >
  </nav>
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
  white-space: nowrap;
}
nav a {
  display: inline-flex;
  align-items: center;
  color: var(--muted);
}
nav a.on {
  color: var(--accent);
  font-weight: 600;
}
/* each link is as tall as a finger: 44 px */
.nav-top {
  font-size: var(--fs-sm);
}
.nav-top a {
  gap: 6px;
  min-height: 44px;
  padding: 0 var(--space-2);
}
.nav-tabs {
  display: none;
}
/* a phone: no name bar (a page with nothing in the header has none), the tabs at the bottom over the safe area */
@media (max-width: 600px) {
  .bar {
    padding: 0 var(--space-3);
  }
  h1,
  .nav-top,
  header.bare {
    display: none;
  }
  .nav-tabs {
    display: flex;
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 3;
    height: var(--tabbar); /* exactly what the page keeps clear: border and safe area inside */
    padding-bottom: env(safe-area-inset-bottom);
    background: var(--surface);
    border-top: 1px solid var(--line);
    font-size: var(--fs-xs);
  }
  .nav-tabs a {
    flex: 1;
    flex-direction: column;
    justify-content: center;
    gap: 2px;
    line-height: 1.2;
  }
}
/* inside the app its own bottom bar navigates; the app adds the class after the page loads (CSS, not a JS check).
   :global() takes the whole selector (it drops anything after it), so the names are unique */
:global(html.in-app .nav-top),
:global(html.in-app .nav-tabs),
:global(html.in-app header h1),
:global(html.in-app header.bare) {
  display: none;
}
</style>
