<template>
  <transition name="slide">
    <aside v-if="isOpen" class="copilot-panel" :aria-label="t('copilot.title')">
      <header class="copilot-header">
        <div>
          <h3 class="copilot-title">{{ t('copilot.title') }}</h3>
          <p class="copilot-subtitle">{{ t('copilot.subtitle') }}</p>
        </div>
        <button class="copilot-new" :title="t('copilot.newChat')" @click="newConversation">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 5v14M5 12h14"/>
          </svg>
        </button>
      </header>

      <div ref="scroll" class="copilot-scroll">
        <div v-if="!hasMessages" class="copilot-empty">
          <p>{{ t('copilot.emptyTitle') }}</p>
          <ul>
            <li>"{{ t('copilot.exampleOne') }}"</li>
            <li>"{{ t('copilot.exampleTwo') }}"</li>
            <li>"{{ t('copilot.exampleThree') }}"</li>
          </ul>
        </div>

        <div
          v-for="(msg, idx) in messages"
          :key="idx"
          :class="['copilot-message', `is-${msg.role}`]"
        >
          <div v-if="msg.role === 'user'" class="copilot-user-text">{{ msg.text }}</div>

          <template v-else-if="msg.role === 'assistant'">
            <!-- Tool calls render as collapsible pencil-line blocks -->
            <details v-for="(call, ci) in msg.toolCalls" :key="ci" class="tool-call">
              <summary>
                <span class="tool-dot" :class="{ pending: call.pending }"></span>
                <span class="tool-name">{{ formatToolName(call.name) }}</span>
                <span class="tool-status">{{ call.pending ? t('copilot.thinking') : t('copilot.done') }}</span>
              </summary>
              <pre class="tool-input">{{ formatInput(call.input) }}</pre>
              <pre v-if="call.result !== null" class="tool-result">{{ formatResult(call.result) }}</pre>
            </details>

            <div v-if="msg.text" class="copilot-assistant-text" v-html="renderMarkdown(msg.text)"></div>

            <!-- Proposal card -->
            <div v-if="msg.proposal" :class="['proposal-card', { approved: msg.proposal.approved, rejected: msg.proposal.rejected }]">
              <div class="proposal-summary">{{ msg.proposal.summary }}</div>
              <ul class="proposal-items">
                <li v-for="(it, i) in msg.proposal.items.slice(0, 5)" :key="i">
                  {{ it.quantity }} × {{ it.item_name }}
                  <span class="proposal-line-cost">${{ it.line_cost.toLocaleString() }}</span>
                </li>
                <li v-if="msg.proposal.items.length > 5" class="proposal-more">
                  + {{ msg.proposal.items.length - 5 }} more
                </li>
              </ul>
              <div v-if="msg.proposal.approved" class="proposal-status approved-status">
                {{ t('copilot.approved') }} — {{ msg.proposal.order?.order_number }}
              </div>
              <div v-else-if="msg.proposal.rejected" class="proposal-status rejected-status">
                {{ t('copilot.rejected') }}
              </div>
              <div v-else class="proposal-actions">
                <button class="approve-btn" @click="approveProposal">{{ t('copilot.approve') }}</button>
                <button class="reject-btn" @click="rejectProposal">{{ t('copilot.reject') }}</button>
              </div>
            </div>
          </template>

          <div v-else-if="msg.role === 'error'" class="copilot-error">{{ msg.text }}</div>
        </div>

        <div v-if="streaming && !lastAssistantHasContent" class="copilot-typing">
          <span></span><span></span><span></span>
        </div>
      </div>

      <form class="copilot-input" @submit.prevent="onSend">
        <textarea
          v-model="draft"
          :placeholder="t('copilot.placeholder')"
          :disabled="streaming"
          rows="2"
          @keydown.enter.exact.prevent="onSend"
        />
        <button type="submit" :disabled="streaming || !draft.trim()">
          {{ streaming ? t('copilot.sending') : t('copilot.send') }}
        </button>
      </form>
    </aside>
  </transition>
</template>

<script>
import { ref, computed, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { useFilters } from '../composables/useFilters'
import { useCopilotStream } from '../composables/useCopilotStream'

export default {
  name: 'CopilotPanel',
  props: {
    isOpen: { type: Boolean, default: false },
  },
  setup() {
    const { t } = useI18n()
    const route = useRoute()
    const { getCurrentFilters } = useFilters()
    const {
      messages,
      streaming,
      hasMessages,
      send,
      approveProposal,
      rejectProposal,
      newConversation,
    } = useCopilotStream()

    const draft = ref('')
    const scroll = ref(null)

    const onSend = async () => {
      const text = draft.value.trim()
      if (!text || streaming.value) return
      draft.value = ''
      const pageContext = {
        route: route.path,
        filters: getCurrentFilters(),
      }
      await send(text, pageContext)
    }

    // Auto-scroll to bottom on new content
    watch([messages, streaming], () => {
      nextTick(() => {
        if (scroll.value) scroll.value.scrollTop = scroll.value.scrollHeight
      })
    }, { deep: true })

    const lastAssistantHasContent = computed(() => {
      const last = messages.value[messages.value.length - 1]
      if (!last || last.role !== 'assistant') return true
      return Boolean(last.text || last.toolCalls.length > 0)
    })

    // Render assistant markdown with a very light touch: paragraphs, **bold**,
    // *italic*, `code`, simple bullet lists, and pipe tables. Anything else
    // falls through as escaped text. We deliberately avoid pulling a heavy
    // markdown library into the bundle just for this panel.
    const escapeHtml = (s) =>
      s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

    const renderMarkdown = (text) => {
      if (!text) return ''
      // Split off pipe tables first
      const lines = text.split('\n')
      const out = []
      let i = 0
      while (i < lines.length) {
        // Pipe table: header | header, separator, then rows
        if (
          lines[i]?.includes('|') &&
          lines[i + 1]?.match(/^\s*\|?\s*[:\-]+\s*(\|\s*[:\-]+\s*)+\|?\s*$/)
        ) {
          const header = lines[i].split('|').map((c) => c.trim()).filter(Boolean)
          i += 2
          const rows = []
          while (i < lines.length && lines[i].includes('|')) {
            rows.push(lines[i].split('|').map((c) => c.trim()).filter(Boolean))
            i++
          }
          const ths = header.map((h) => `<th>${inline(h)}</th>`).join('')
          const trs = rows
            .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
            .join('')
          out.push(`<table class="md-table"><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table>`)
          continue
        }
        // Unordered list
        if (/^\s*[-*]\s+/.test(lines[i])) {
          const items = []
          while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
            items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`)
            i++
          }
          out.push(`<ul>${items.join('')}</ul>`)
          continue
        }
        // Blank line → paragraph break
        if (!lines[i].trim()) {
          out.push('')
          i++
          continue
        }
        // Default: collect until blank line into a paragraph
        const para = []
        while (i < lines.length && lines[i].trim()) {
          para.push(lines[i])
          i++
        }
        out.push(`<p>${inline(para.join(' '))}</p>`)
      }
      return out.join('')
    }

    const inline = (s) => {
      let r = escapeHtml(s)
      // Order matters: code first so * inside `code` isn't interpreted.
      r = r.replace(/`([^`]+)`/g, '<code>$1</code>')
      r = r.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      r = r.replace(/\*([^*]+)\*/g, '<em>$1</em>')
      return r
    }

    const formatToolName = (name) =>
      name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

    const formatInput = (input) => {
      if (!input || Object.keys(input).length === 0) return '(no arguments)'
      return JSON.stringify(input, null, 2)
    }

    const formatResult = (result) => {
      if (typeof result === 'string') return result
      const json = JSON.stringify(result, null, 2)
      return json.length > 600 ? json.slice(0, 600) + '\n…(truncated)' : json
    }

    return {
      t,
      messages,
      streaming,
      hasMessages,
      draft,
      scroll,
      onSend,
      approveProposal,
      rejectProposal,
      newConversation,
      lastAssistantHasContent,
      renderMarkdown,
      formatToolName,
      formatInput,
      formatResult,
    }
  },
}
</script>

<style scoped>
/* Notebook-margin docked panel: torn left edge via a wavy SVG mask. */
.copilot-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 400px;
  max-width: 92vw;
  background: var(--paper-surface);
  border-left: 2px solid var(--pencil);
  box-shadow: -8px 0 24px rgba(43, 42, 38, 0.10);
  z-index: 1050;
  display: flex;
  flex-direction: column;
  font-family: var(--font-hand);
}

.slide-enter-from, .slide-leave-to {
  transform: translateX(100%);
}
.slide-enter-active, .slide-leave-active {
  transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}

.copilot-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 1rem 1.25rem 0.5rem;
  border-bottom: 1px dashed var(--pencil-soft);
}
.copilot-title {
  font-family: var(--font-display);
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--ink-strong);
  margin: 0;
  letter-spacing: 0.01em;
}
.copilot-subtitle {
  font-size: 0.875rem;
  color: var(--ink-muted);
  margin: 0;
}
.copilot-new {
  background: transparent;
  border: 1px dashed var(--pencil);
  border-radius: 6px 8px 7px 5px;
  width: 32px;
  height: 32px;
  cursor: pointer;
  color: var(--ink-muted);
  display: flex;
  align-items: center;
  justify-content: center;
}
.copilot-new:hover {
  background: var(--highlighter);
  color: var(--ink-strong);
}

.copilot-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.copilot-empty {
  color: var(--ink-muted);
  padding: 1rem 0;
}
.copilot-empty ul {
  margin: 0.5rem 0 0;
  padding-left: 1.25rem;
}
.copilot-empty li {
  margin: 0.25rem 0;
  font-style: italic;
}

/* Messages */
.copilot-message.is-user .copilot-user-text {
  background: var(--highlighter);
  color: var(--ink-strong);
  padding: 0.5rem 0.75rem;
  border-radius: 12px 14px 11px 13px;
  margin-left: 2rem;
  border: 1px solid var(--pencil-soft);
  font-family: var(--font-display);
  font-size: 1.1rem;
  line-height: 1.35;
}
.copilot-message.is-assistant {
  margin-right: 1rem;
}
.copilot-assistant-text {
  font-size: 0.95rem;
  line-height: 1.5;
  color: var(--ink-body);
}
.copilot-assistant-text :deep(p) { margin: 0 0 0.5rem; }
.copilot-assistant-text :deep(p:last-child) { margin-bottom: 0; }
.copilot-assistant-text :deep(strong) { color: var(--ink-strong); }
.copilot-assistant-text :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.85rem;
  background: var(--paper-surface-warm);
  padding: 0.05rem 0.35rem;
  border-radius: 4px;
}
.copilot-assistant-text :deep(ul) {
  margin: 0.25rem 0 0.5rem 1.25rem;
  padding: 0;
}
.copilot-assistant-text :deep(li) { margin: 0.15rem 0; }
.copilot-assistant-text :deep(.md-table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.5rem 0;
  font-size: 0.85rem;
}
.copilot-assistant-text :deep(.md-table th) {
  text-align: left;
  background: var(--paper-surface-warm);
  padding: 0.3rem 0.5rem;
  border-bottom: 1px solid var(--pencil);
  font-weight: 600;
  color: var(--ink-soft, var(--ink-body));
}
.copilot-assistant-text :deep(.md-table td) {
  padding: 0.3rem 0.5rem;
  border-bottom: 1px dashed var(--pencil-soft);
  color: var(--ink-body);
}

/* Tool call block */
.tool-call {
  margin: 0.4rem 0;
  border: 1px dashed var(--pencil);
  border-radius: 8px 10px 7px 9px;
  background: var(--paper-surface-warm);
  font-size: 0.8rem;
}
.tool-call summary {
  cursor: pointer;
  padding: 0.4rem 0.6rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  list-style: none;
  color: var(--ink-muted);
}
.tool-call summary::-webkit-details-marker { display: none; }
.tool-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success);
}
.tool-dot.pending { background: var(--warning); animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}
.tool-name { color: var(--ink-strong); font-weight: 600; }
.tool-status { margin-left: auto; font-style: italic; }
.tool-input, .tool-result {
  margin: 0;
  padding: 0.4rem 0.6rem;
  background: var(--paper-surface);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem;
  color: var(--ink-body);
  white-space: pre-wrap;
  word-break: break-word;
  border-top: 1px dashed var(--pencil-soft);
}

/* Proposal card */
.proposal-card {
  margin-top: 0.6rem;
  border: 2px solid var(--ink-blue);
  border-radius: 12px 14px 11px 13px;
  padding: 0.75rem 1rem;
  background: var(--paper-surface-warm);
  transform: rotate(-0.2deg);
}
.proposal-summary {
  font-family: var(--font-display);
  font-size: 1.05rem;
  color: var(--ink-strong);
  margin-bottom: 0.5rem;
}
.proposal-items {
  margin: 0 0 0.5rem;
  padding-left: 1.1rem;
  font-size: 0.85rem;
  color: var(--ink-body);
}
.proposal-items li { margin: 0.1rem 0; display: flex; justify-content: space-between; }
.proposal-line-cost { color: var(--ink-muted); margin-left: 0.5rem; font-variant-numeric: tabular-nums; }
.proposal-more { font-style: italic; color: var(--ink-muted); justify-content: flex-start !important; }
.proposal-actions {
  display: flex;
  gap: 0.5rem;
}
.approve-btn {
  background: var(--success);
  color: var(--paper-surface);
  border: 2px solid var(--ink-strong);
  padding: 0.4rem 0.85rem;
  border-radius: 8px 10px 7px 9px;
  font-family: var(--font-hand);
  font-weight: 600;
  cursor: pointer;
  flex: 1;
}
.approve-btn:hover { transform: translateY(-1px); }
.reject-btn {
  background: transparent;
  color: var(--ink-muted);
  border: 1px dashed var(--pencil);
  padding: 0.4rem 0.85rem;
  border-radius: 8px 10px 7px 9px;
  font-family: var(--font-hand);
  cursor: pointer;
}
.reject-btn:hover { color: var(--danger); border-color: var(--danger); }
.proposal-status { font-size: 0.85rem; font-style: italic; }
.approved-status { color: var(--success); }
.rejected-status { color: var(--ink-muted); }
.proposal-card.approved { border-color: var(--success); opacity: 0.85; }
.proposal-card.rejected { border-color: var(--pencil); opacity: 0.7; }

.copilot-error {
  color: var(--danger);
  background: rgba(181, 61, 52, 0.10);
  border: 1px solid rgba(181, 61, 52, 0.30);
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  font-size: 0.85rem;
}

/* Typing indicator */
.copilot-typing {
  display: flex;
  gap: 0.35rem;
  padding: 0.5rem 0.25rem;
}
.copilot-typing span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ink-blue);
  animation: bounce 1.2s ease-in-out infinite;
}
.copilot-typing span:nth-child(2) { animation-delay: 0.15s; }
.copilot-typing span:nth-child(3) { animation-delay: 0.30s; }
@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.55; }
  30%           { transform: translateY(-4px); opacity: 1; }
}

/* Input */
.copilot-input {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem 1rem;
  border-top: 1px dashed var(--pencil-soft);
  background: var(--paper-surface);
}
.copilot-input textarea {
  flex: 1;
  resize: none;
  font-family: var(--font-hand);
  font-size: 0.95rem;
  background: var(--paper-bg);
  color: var(--ink-strong);
  border: 1px solid var(--pencil);
  border-radius: 8px 10px 7px 9px;
  padding: 0.5rem 0.75rem;
  outline: none;
}
.copilot-input textarea:focus {
  border-color: var(--ink-blue);
  box-shadow: 0 0 0 3px rgba(59, 111, 179, 0.12);
}
.copilot-input button {
  background: var(--ink-blue);
  color: var(--paper-surface);
  border: 2px solid var(--ink-strong);
  padding: 0.5rem 0.95rem;
  border-radius: 8px 10px 7px 9px;
  font-family: var(--font-hand);
  font-weight: 600;
  cursor: pointer;
  align-self: stretch;
}
.copilot-input button:disabled {
  background: var(--pencil-soft);
  color: var(--ink-muted);
  border-color: var(--pencil);
  cursor: not-allowed;
}
</style>
