<template>
  <button
    class="copilot-fab"
    :class="{ open: isOpen }"
    :aria-label="isOpen ? t('copilot.close') : t('copilot.fab')"
    :title="isOpen ? t('copilot.close') : t('copilot.fab')"
    @click="$emit('toggle')"
  >
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path v-if="!isOpen" d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
      <path v-else d="M18 6L6 18M6 6l12 12"/>
    </svg>
  </button>
</template>

<script>
import { useI18n } from '../composables/useI18n'

export default {
  name: 'CopilotFab',
  props: {
    isOpen: { type: Boolean, default: false },
  },
  emits: ['toggle'],
  setup() {
    const { t } = useI18n()
    return { t }
  },
}
</script>

<style scoped>
.copilot-fab {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  width: 56px;
  height: 56px;
  border-radius: 28px 30px 27px 29px; /* asymmetric hand-drawn feel */
  background: var(--ink-blue);
  color: var(--paper-surface);
  border: 2px solid var(--ink-strong);
  box-shadow:
    1px 2px 0 var(--ink-strong),
    0 4px 16px rgba(43, 42, 38, 0.18);
  cursor: pointer;
  z-index: 1100;
  display: flex;
  align-items: center;
  justify-content: center;
  transform: rotate(-2deg);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.copilot-fab:hover {
  transform: rotate(-2deg) translateY(-2px);
  box-shadow:
    1px 3px 0 var(--ink-strong),
    0 6px 22px rgba(43, 42, 38, 0.24);
}
.copilot-fab.open {
  transform: rotate(0deg);
  background: var(--paper-surface);
  color: var(--ink-strong);
}
.copilot-fab svg {
  width: 26px;
  height: 26px;
}
</style>
