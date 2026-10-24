<template>
  <!-- loading: three grey bars where the content will be -->
  <div class="skeleton" role="status" aria-label="加载中"><i /><i /><i /></div>
</template>

<style scoped>
.skeleton {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-4) 0;
}
i {
  display: block;
  height: 16px;
  border-radius: var(--radius-sm);
  background: var(--line);
  animation: pulse 1.2s ease-in-out infinite;
}
i:nth-child(2) {
  width: 80%;
}
i:nth-child(3) {
  width: 55%;
}
@keyframes pulse {
  50% {
    opacity: 0.5;
  }
}
@media (prefers-reduced-motion: reduce) {
  i {
    animation: none;
  }
}
</style>
