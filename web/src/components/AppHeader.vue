<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { t } from "../i18n";
import Icon, { type IconName } from "./Icon.vue";

// inApp: what the header shows inside the Android app, which has its own navigation (nothing: hidden). lift: how far
// it goes up over the top edge, taking its first row (the page's day picker, useHideOnScroll) out of sight
defineProps<{ inApp?: boolean; lift?: number }>();

// four places, each owning the pages under it (a conversation, the calendar and the todos are 时间线's; devices,
// progress, connect are 我的); a phone shows 我的 as the fourth tab, a wide screen too. label: the page title's key
const TABS: { to: string; label: string; icon: IconName; under: RegExp }[] = [
  {
    to: "/",
    label: "title.timeline",
    icon: "list",
    under: /^\/($|conversations|c\/|calendar|todos)/,
  },
  { to: "/live", label: "title.live", icon: "captions", under: /^\/live/ },
  { to: "/speakers", label: "title.people", icon: "users", under: /^\/(speakers|p\/)/ },
  { to: "/me", label: "title.me", icon: "user", under: /^\/(me|connect|progress|device\/|record)/ },
];
const route = useRoute();
const router = useRouter();
// inside the app a page under a tab (进度, a device, the calendar...) has no way back: the app hides this header's
// navigation and has no back key on iOS, so such a page gets a 返回 row (CSS shows it in the app only)
const under = computed(() => TABS.find((x) => x.under.test(route.path)));
const sub = computed(() => !!under.value && route.path !== under.value.to);
const back = () => (history.state?.back ? router.back() : router.push(under.value?.to ?? "/"));
</script>

<template>
  <header :class="{ bare: !inApp }" :style="lift ? { top: -lift + 'px' } : undefined">
    <div class="bar">
      <h1>alldayrec</h1>
      <!-- a wide screen: 时间线 / 实时 / 人物 next to the name, 我的 apart on the right -->
      <nav class="nav-top">
        <RouterLink
          v-for="tab in TABS.slice(0, 3)"
          :key="tab.to"
          :to="tab.to"
          :class="{ on: tab.under.test(route.path) }"
          ><Icon :name="tab.icon" :size="18" />{{ t(tab.label) }}</RouterLink
        >
      </nav>
      <slot />
      <nav class="nav-top me">
        <RouterLink :to="TABS[3].to" :class="{ on: TABS[3].under.test(route.path) }"
          ><Icon :name="TABS[3].icon" :size="18" />{{ t(TABS[3].label) }}</RouterLink
        >
      </nav>
    </div>
  </header>
  <div v-if="sub" class="back-row">
    <button class="btn-ghost" @click="back">
      <Icon name="chevron-left" :size="20" />{{ t("common.back") }}
    </button>
    <span class="where">{{ route.meta.title ? t(route.meta.title as string) : "" }}</span>
  </div>
  <!-- a phone: the same four as tabs at the bottom -->
  <nav class="nav-tabs">
    <RouterLink
      v-for="tab in TABS"
      :key="tab.to"
      :to="tab.to"
      :class="{ on: tab.under.test(route.path) }"
      ><Icon :name="tab.icon" :size="22" />{{ t(tab.label) }}</RouterLink
    >
  </nav>
</template>

<style scoped>
.back-row {
  display: none; /* the app only, below */
  align-items: center;
  gap: var(--space-2);
  padding: 0 var(--space-2);
  min-height: 40px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 2;
}
.back-row .where {
  font-size: var(--fs-sm);
  color: var(--muted);
}
:global(html.in-app .back-row) {
  display: flex;
}
header {
  position: sticky;
  top: 0;
  background: var(--surface);
  box-shadow: var(--shadow);
  border-bottom: 1px solid var(--line);
  z-index: 2;
}
@media (prefers-reduced-motion: no-preference) {
  header {
    transition: top 0.15s;
  }
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
.nav-top.me {
  margin-left: auto;
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
