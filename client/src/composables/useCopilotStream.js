import { ref, computed } from 'vue'

/**
 * Composable wrapping the SSE-streamed /api/copilot/chat endpoint.
 *
 * Drives a Vue chat panel: holds the conversation `messages` array, streams
 * text deltas into the in-flight assistant message, surfaces tool calls and
 * proposals as their own UI affordances, and persists the conversation id to
 * localStorage so a page reload reattaches to the same on-disk transcript.
 *
 * The on-the-wire message protocol mirrors `server/copilot/agent.py`. Each
 * SSE `data:` line is one event:
 *   {type: 'text',        delta: '...'}
 *   {type: 'tool_use',    name, input}
 *   {type: 'tool_result', name, result}
 *   {type: 'proposal',    name, proposal: {proposal_id, items, total_value, ...}}
 *   {type: 'error',       message}
 *   {type: 'done',        stop_reason}
 */

const CONV_ID_KEY = 'copilot-conversation-id'

function makeConversationId() {
  // Short, URL-safe, sortable-ish — good enough for a JSONL filename.
  return 'conv-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8)
}

/**
 * Apply a UI action emitted by the server-side agent.
 *
 * The agent's UI tools (navigate_to_page / set_filter / highlight_element)
 * return a `{queued_action: ...}` payload that the server broadcasts as a
 * separate SSE `ui_action` event. We execute it here on the client.
 *
 * Returns a result tag for tracing — not used by the agent loop today, but
 * useful if we later round-trip an ack via a follow-up user message.
 */
function applyUiAction(action, ctx) {
  if (!action || typeof action !== 'object') return { applied: false }
  switch (action.queued_action) {
    case 'navigate': {
      if (ctx.router && action.route) {
        ctx.router.push(action.route).catch(() => {})
        return { applied: true, route: action.route }
      }
      return { applied: false, reason: 'no router or route' }
    }
    case 'set_filter': {
      const map = {
        period: ctx.filters?.selectedPeriod,
        location: ctx.filters?.selectedLocation,
        category: ctx.filters?.selectedCategory,
        status: ctx.filters?.selectedStatus,
      }
      const ref = map[action.filter]
      if (!ref || typeof ref !== 'object' || !('value' in ref)) {
        return { applied: false, reason: 'unknown filter' }
      }
      ref.value = normalizeFilterValue(action.filter, action.value)
      return { applied: true, filter: action.filter, value: ref.value }
    }
    case 'highlight': {
      const selector = highlightSelectorFor(action.kind)
      if (!selector) return { applied: false, reason: 'unmapped kind' }
      // Defer one paint so router-pushed nav has time to mount the target
      // element before we query for it. Highlights are almost always
      // preceded by navigate() in the same agent turn.
      requestAnimationFrame(() => {
        // Sometimes the new route is still mounting on the next paint —
        // retry a couple of frames before giving up.
        let attempts = 0
        const tryHighlight = () => {
          const el = document.querySelector(selector)
          if (!el) {
            attempts += 1
            if (attempts < 8) {
              requestAnimationFrame(tryHighlight)
            }
            return
          }
          // Scroll into view first so a below-the-fold row is actually
          // visible when the pulse starts. `block: center` puts it in
          // the middle of the viewport rather than the very top.
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
          // Restart animation via class-remove + reflow + class-add.
          el.classList.remove('copilot-pulse')
          void el.offsetWidth
          el.classList.add('copilot-pulse')
          const stripper = () => {
            el.classList.remove('copilot-pulse')
            el.removeEventListener('animationend', stripper)
          }
          el.addEventListener('animationend', stripper)
        }
        tryHighlight()
      })
      return { applied: true, kind: action.kind }
    }
    default:
      return { applied: false, reason: 'unknown queued_action' }
  }
}

/**
 * Normalize a filter value so it actually matches the option value the
 * <select> uses. The FilterBar uses lowercase for category + status, title
 * case for warehouses; the model sometimes guesses one off — coerce here
 * rather than make the model second-guess casing on every call.
 */
function normalizeFilterValue(filter, raw) {
  const v = String(raw ?? '').trim()
  if (!v || v.toLowerCase() === 'all') return 'all'
  switch (filter) {
    case 'category':
    case 'status':
      return v.toLowerCase()
    case 'location': {
      const map = {
        'san francisco': 'San Francisco',
        london: 'London',
        tokyo: 'Tokyo',
      }
      return map[v.toLowerCase()] || v
    }
    default:
      return v
  }
}

/** Translate a server-side highlight `kind` into a CSS selector. */
function highlightSelectorFor(kind) {
  // Table rows by SKU: data-copilot-sku="<SKU>". Escape any quotes so a
  // hypothetical hostile SKU can't break out of the attribute selector.
  if (kind?.startsWith?.('sku:')) {
    const sku = kind.slice(4).replace(/"/g, '\\"')
    return `[data-copilot-sku="${sku}"]`
  }
  // Nav tabs: data-copilot-nav="<key>".
  if (kind?.startsWith?.('nav:')) {
    return `[data-copilot-nav="${kind.slice(4)}"]`
  }
  // Filter dropdowns: data-copilot-filter="<key>".
  if (kind?.startsWith?.('filter:')) {
    return `[data-copilot-filter="${kind.slice(7)}"]`
  }
  // In-page cards: data-copilot-card="<key>".
  if (kind?.startsWith?.('card:')) {
    return `[data-copilot-card="${kind.slice(5)}"]`
  }
  if (kind === 'page:current') return '.main-content'
  return null
}

export function useCopilotStream({ router, filters } = {}) {
  const uiCtx = { router, filters }
  /** @type {import('vue').Ref<string>} */
  const conversationId = ref(localStorage.getItem(CONV_ID_KEY) || makeConversationId())
  localStorage.setItem(CONV_ID_KEY, conversationId.value)

  /**
   * Each chat message is one of:
   *   {role: 'user', text}
   *   {role: 'assistant', text, toolCalls: [{name, input, result}], proposal?: {...}}
   *   {role: 'error', text}
   */
  const messages = ref([])
  const streaming = ref(false)
  const error = ref(null)

  // Current proposal pending approval (most recent). The panel renders an
  // Approve/Reject card when this is non-null.
  const pendingProposal = ref(null)

  let abortController = null

  const send = async (userText, pageContext) => {
    if (!userText?.trim() || streaming.value) return

    error.value = null
    pendingProposal.value = null
    messages.value.push({ role: 'user', text: userText })

    // Pre-seed the assistant slot. Critical: we mutate via messages.value[idx]
    // (the reactive proxy), NOT via a closure-held raw reference — Vue's
    // Proxy only tracks writes that go through the proxy, so a plain POJO
    // reference would write through but never notify subscribers. That was
    // why the streamed deltas weren't visible until the turn ended.
    messages.value.push({ role: 'assistant', text: '', toolCalls: [], proposal: null })
    const assistantIdx = messages.value.length - 1
    const assistant = messages.value[assistantIdx]

    streaming.value = true
    abortController = new AbortController()

    try {
      const response = await fetch('http://localhost:8001/api/copilot/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          conversation_id: conversationId.value,
          user_message: userText,
          page_context: pageContext || null,
        }),
        signal: abortController.signal,
      })

      if (!response.ok) {
        throw new Error(`Server error ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE frame separator: blank line. Split on \n\n, keep tail in buffer.
        let sep
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep)
          buffer = buffer.slice(sep + 2)
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data:')) continue
            const json = line.slice(5).trim()
            if (!json) continue
            try {
              const event = JSON.parse(json)
              handleEvent(event, assistant)
            } catch (parseErr) {
              console.error('Bad SSE frame:', parseErr, json)
            }
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        assistant.text += '\n[Cancelled.]'
      } else {
        error.value = err.message
        assistant.text = assistant.text || `Error: ${err.message}`
      }
    } finally {
      streaming.value = false
      abortController = null
    }
  }

  const handleEvent = (event, assistant) => {
    switch (event.type) {
      case 'text':
        assistant.text += event.delta
        break
      case 'tool_use':
        assistant.toolCalls.push({
          name: event.name,
          input: event.input,
          result: null,
          pending: true,
        })
        break
      case 'tool_result': {
        // Match to the last pending call with the same name.
        const call = [...assistant.toolCalls].reverse().find(
          (c) => c.name === event.name && c.pending
        )
        if (call) {
          call.result = event.result
          call.pending = false
        }
        break
      }
      case 'proposal':
        assistant.proposal = event.proposal
        pendingProposal.value = event.proposal
        // Also mark the matching tool call as resolved.
        {
          const call = [...assistant.toolCalls].reverse().find(
            (c) => c.name === event.name && c.pending
          )
          if (call) call.pending = false
        }
        break
      case 'error':
        error.value = event.message
        messages.value.push({ role: 'error', text: event.message })
        break
      case 'ui_action':
        applyUiAction(event.action, uiCtx)
        break
      case 'done':
        // Nothing to do — the stream loop will exit on its own.
        break
    }
  }

  const approveProposal = async () => {
    if (!pendingProposal.value) return
    const proposal = pendingProposal.value
    try {
      const r = await fetch('http://localhost:8001/api/copilot/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          proposal_id: proposal.proposal_id,
          conversation_id: conversationId.value,
        }),
      })
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}))
        throw new Error(detail.detail || `Approve failed (${r.status})`)
      }
      const data = await r.json()
      // Mark the proposal as approved on the assistant message that emitted it.
      const last = [...messages.value].reverse().find((m) => m.proposal === proposal)
      if (last) {
        last.proposal = { ...proposal, approved: true, order: data.order }
      }
      pendingProposal.value = null
      return data
    } catch (err) {
      error.value = err.message
    }
  }

  const rejectProposal = () => {
    if (!pendingProposal.value) return
    const proposal = pendingProposal.value
    const last = [...messages.value].reverse().find((m) => m.proposal === proposal)
    if (last) {
      last.proposal = { ...proposal, rejected: true }
    }
    pendingProposal.value = null
  }

  const abort = () => {
    if (abortController) abortController.abort()
  }

  const newConversation = () => {
    conversationId.value = makeConversationId()
    localStorage.setItem(CONV_ID_KEY, conversationId.value)
    messages.value = []
    pendingProposal.value = null
    error.value = null
  }

  const hasMessages = computed(() => messages.value.length > 0)

  return {
    conversationId,
    messages,
    streaming,
    error,
    pendingProposal,
    hasMessages,
    send,
    approveProposal,
    rejectProposal,
    abort,
    newConversation,
  }
}
