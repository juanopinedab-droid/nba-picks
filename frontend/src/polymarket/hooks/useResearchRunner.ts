import { useCallback, useEffect, useState } from 'react'
import type { ResearchEvent } from '../components/PrincipalAgentStream'

type Listener = () => void

const SSE_BASE = import.meta.env.DEV ? 'http://localhost:5000' : ''

let activeEvents: ResearchEvent[] = []
let activeStreaming = false
let activeSessionId: number | null = null
let activeAbortController: AbortController | null = null
let activeMaxSteps = 3
let resumeRetries = 0
const MAX_RESUME_RETRIES = 3
const listeners = new Set<Listener>()

function notify() {
  for (const fn of listeners) {
    fn()
  }
}

function subscribe(cb: Listener) {
  listeners.add(cb)
  return () => { listeners.delete(cb) }
}

function cancelSessionViaBeacon(sid: number) {
  try {
    navigator.sendBeacon(
      `${SSE_BASE}/api/pm/ai-research/${sid}/cancel`,
      new Blob(['{}'], { type: 'application/json' })
    )
  } catch {}
}

function getSnapshot() {
  return {
    events: activeEvents,
    streaming: activeStreaming,
    sessionId: activeSessionId,
    maxSteps: activeMaxSteps,
    hasActive: activeStreaming || activeSessionId !== null,
  }
}

async function sseStream(fetchFn: () => Promise<Response>, controller: AbortController) {
  try {
    const response = await fetchFn()

    if (!response.ok) throw new Error(`HTTP ${response.status}`)

    const reader = response.body?.getReader()
    if (!reader) throw new Error('ReadableStream not supported')

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''

      for (const block of parts) {
        if (!block.trim()) continue
        const lines = block.split('\n')
        let eventType = ''
        let dataStr = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) eventType = line.slice(7).trim()
          else if (line.startsWith('data: ')) dataStr = line.slice(6).trim()
        }
        if (!eventType) continue
        if (eventType === 'ping') continue
        if (!dataStr) continue

        try {
          const parsed = JSON.parse(dataStr)
          if (eventType === 'session_created') {
            activeSessionId = parsed.session_id || null
            notify()
            continue
          }
          if (eventType === 'done') {
            activeSessionId = activeSessionId || parsed.session_id || null
            activeStreaming = false
            resumeRetries = 0
            activeEvents = [...activeEvents, { type: eventType, data: parsed }]
            notify()
            return
          }
          activeEvents = [...activeEvents, { type: eventType, data: parsed }]
          notify()
        } catch { /* skip */ }
      }
    }

    const hasResult = activeEvents.some(e => e.type === 'result' || e.type === 'done')
    if (!hasResult && activeEvents.length > 0) {
      tryAutoResume()
    } else {
      activeStreaming = false
      notify()
    }
  } catch (e: any) {
    if (e.name !== 'AbortError') {
      tryAutoResume()
    }
  }
}

function tryAutoResume() {
  const sid = activeSessionId
  if (!sid || resumeRetries >= MAX_RESUME_RETRIES) {
    resumeRetries = 0
    if (sid) cancelSessionViaBeacon(sid)
    activeEvents = [...activeEvents, {
      type: 'error',
      data: { error: 'Research connection lost. Maximum retry attempts reached. The session has been saved and can be resumed manually from history.' }
    }]
    activeStreaming = false
    notify()
    return
  }
  resumeRetries++
  activeEvents = [...activeEvents, {
    type: 'phase',
    data: { phase: 'thinking', message: `Connection lost. Auto-resuming... (attempt ${resumeRetries}/${MAX_RESUME_RETRIES})` }
  }]
  activeStreaming = true
  notify()

  const controller = new AbortController()
  activeAbortController = controller
  sseStream(
    () => fetch(`${SSE_BASE}/api/pm/ai-research/${sid}/resume`, {
      method: 'POST',
      signal: controller.signal,
    }),
    controller
  )
}

async function runStream(params: Record<string, any>) {
  activeAbortController?.abort()
  const controller = new AbortController()
  activeAbortController = controller
  activeEvents = []
  activeStreaming = true
  activeSessionId = null
  activeMaxSteps = params.max_steps || 3
  resumeRetries = 0

  const onBeforeUnload = () => {
    if (activeSessionId) cancelSessionViaBeacon(activeSessionId)
  }
  window.addEventListener('beforeunload', onBeforeUnload)

  notify()

  await sseStream(
    () => fetch(`${SSE_BASE}/api/pm/ai-research/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: params.question,
        context: params.context,
        price: params.price,
        prompt_customization: params.prompt_customization,
        fixed_data: params.fixed_data,
        max_steps: params.max_steps,
        max_subagents: params.max_subagents,
        max_rounds: params.max_rounds,
        min_visualizations: params.min_visualizations,
        min_mispricing_calls: params.min_mispricing_calls,
        force_top_reports: params.force_top_reports !== false,
        principal_model: params.principal_model,
        subagent_model: params.subagent_model,
      }),
      signal: controller.signal,
    }),
    controller
  )

  window.removeEventListener('beforeunload', onBeforeUnload)
}

async function runResume(sessionId: number) {
  activeAbortController?.abort()
  const controller = new AbortController()
  activeAbortController = controller
  activeEvents = []
  activeStreaming = true
  activeSessionId = null
  activeMaxSteps = 3
  resumeRetries = 0

  const onBeforeUnload = () => {
    if (activeSessionId) cancelSessionViaBeacon(activeSessionId)
  }
  window.addEventListener('beforeunload', onBeforeUnload)

  notify()

  await sseStream(
    () => fetch(`${SSE_BASE}/api/pm/ai-research/${sessionId}/resume`, {
      method: 'POST',
      signal: controller.signal,
    }),
    controller
  )

  window.removeEventListener('beforeunload', onBeforeUnload)
}

export function useResearchRunner() {
  const [snap, setSnap] = useState(getSnapshot)

  useEffect(() => {
    setSnap(getSnapshot())
    return subscribe(() => setSnap(getSnapshot()))
  }, [])

  const start = useCallback(async (params: Record<string, any>) => {
    runStream(params)
  }, [])

  const cancel = useCallback(() => {
    const sid = activeSessionId
    activeAbortController?.abort()
    activeStreaming = false
    activeEvents = []
    activeSessionId = null
    resumeRetries = 0
    notify()
    if (sid) {
      fetch(`${SSE_BASE}/api/pm/ai-research/${sid}/cancel`, { method: 'POST' }).catch(() => {})
    }
  }, [])

  const clear = useCallback(() => {
    activeEvents = []
    activeStreaming = false
    activeSessionId = null
    activeMaxSteps = 3
    resumeRetries = 0
    notify()
  }, [])

  const resume = useCallback(async (sessionId: number) => {
    runResume(sessionId)
  }, [])

  return {
    events: snap.events,
    streaming: snap.streaming,
    sessionId: snap.sessionId,
    maxSteps: snap.maxSteps,
    hasActive: snap.hasActive,
    start,
    cancel,
    clear,
    resume,
  }
}
