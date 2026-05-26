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

export function useCopilotStream() {
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
