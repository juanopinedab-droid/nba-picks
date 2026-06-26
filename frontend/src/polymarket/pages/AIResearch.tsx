import { useState, useCallback, useEffect, useRef } from 'react'
import { Brain, Square, RotateCcw, AlertTriangle, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { ResearchForm } from '../components/ResearchForm'
import { PrincipalAgentStream, type ResearchEvent } from '../components/PrincipalAgentStream'
import { SubAgentCard, type SubAgentState } from '../components/SubAgentCard'
import { SubAgentModal } from '../components/SubAgentModal'
import { ResearchHistory, type HistorySession } from '../components/ResearchHistory'
import { useResearchRunner } from '../hooks/useResearchRunner'

function buildSubAgentsFromEvents(events: ResearchEvent[]): SubAgentState[] {
  const map = new Map<number, SubAgentState>()

  function getOrCreateAgent(id: number, data: any): SubAgentState {
    if (!map.has(id)) {
      map.set(id, {
        id,
        topic: data.topic || '',
        status: 'pending',
        steps: [],
        round: data.round,
      })
    }
    return map.get(id)!
  }

  function getOrCreateStep(agent: SubAgentState, stepNum: number): SubAgentStep {
    const existing = agent.steps.find(s => s.step === stepNum)
    if (existing) return existing
    const step: SubAgentStep = { step: stepNum, toolCalls: [] }
    agent.steps.push(step)
    return step
  }

  for (const event of events) {
    if (event.type === 'subagent_start') {
      getOrCreateAgent(event.data.id, event.data)
    } else if (event.type === 'subagent_thinking') {
      const agent = map.get(event.data.id)
      if (agent && agent.status !== 'completed' && agent.status !== 'limit_reached') {
        agent.status = 'thinking'
      }
    } else if (event.type === 'subagent_reasoning') {
      const agent = map.get(event.data.id)
      if (agent) {
        const step = getOrCreateStep(agent, event.data.step || 1)
        step.reasoning = (step.reasoning || '') + event.data.content
      }
    } else if (event.type === 'subagent_tool_start') {
      const agent = map.get(event.data.id)
      if (agent) {
        agent.status = 'in_progress'
        const step = getOrCreateStep(agent, event.data.step || 1)
        step.toolCalls.push({
          tool: event.data.tool || '',
          label: event.data.label || 'Tool',
          query: event.data.query || '',
          results: '',
          sources: event.data.sources || [],
        })
      }
    } else if (event.type === 'subagent_tool_result') {
      const agent = map.get(event.data.id)
      if (agent) {
        agent.status = 'in_progress'
        const step = getOrCreateStep(agent, event.data.step || 1)
        const tc = step.toolCalls.find(t => t.tool === event.data.tool && !t.results)
        if (tc) {
          tc.results = event.data.results || ''
          tc.sources = event.data.sources || []
        } else {
          step.toolCalls.push({
            tool: event.data.tool || '',
            label: event.data.label || 'Tool',
            query: event.data.query || '',
            results: event.data.results || '',
            sources: event.data.sources || [],
          })
        }
      }
    } else if (event.type === 'subagent_step') {
      const agent = map.get(event.data.id)
      if (agent) {
        agent.status = 'in_progress'
        const step = getOrCreateStep(agent, event.data.step || 1)
        step.toolCalls.push({
          tool: 'search_internet',
          label: 'Web Search',
          query: event.data.query || '',
          results: event.data.results || '',
          sources: event.data.sources || [],
        })
      }
    } else if (event.type === 'subagent_complete') {
      const agent = map.get(event.data.id)
      if (agent) {
        agent.status = agent.status === 'limit_reached' ? 'limit_reached'
          : (event.data.report === 'CALL_LIMIT_REACHED' ? 'limit_reached' : 'completed')
        agent.report = event.data.report || ''
        agent.stepsUsed = event.data.steps_used || agent.steps.length
        agent.forcedSummary = event.data.forced_summary || false
        agent.timedOut = event.data.timed_out || false
        agent.sources = event.data.sources || []
        if (event.data.round != null) agent.round = event.data.round
      }
    }
  }

  return Array.from(map.values()).sort((a, b) => a.id - b.id)
}

function loadSessionAsEvents(session: any): { events: ResearchEvent[]; maxSteps: number } {
  const events: ResearchEvent[] = []
  const subReports: any[] = Array.isArray(session.subagent_reports) ? session.subagent_reports : []
  const isComplete = session.status === 'completed'

  let maxSteps = 3
  if (subReports.length > 0) {
    maxSteps = Math.max(...subReports.map((r: any) => r.steps_used || 3))
  }

  events.push({ type: 'phase', data: { phase: 'planning', message: 'Research session loaded from history' } })
  events.push({ type: 'phase', data: { phase: 'researching', message: `Loaded ${subReports.length} sub-agent reports`, total: subReports.length } })

  subReports.forEach((r: any, i: number) => {
    events.push({ type: 'subagent_start', data: { id: i, topic: r.topic || `Report ${i + 1}`, round: r.round || 1 } })

    if (r.tool_calls && Array.isArray(r.tool_calls)) {
      r.tool_calls.forEach((tc: any) => {
        events.push({ type: 'subagent_tool_start', data: { id: i, step: tc.step || 1, tool: tc.tool || 'search_internet', label: tc.label || 'Tool', query: tc.query || '' } })
        events.push({ type: 'subagent_tool_result', data: { id: i, step: tc.step || 1, tool: tc.tool || 'search_internet', label: tc.label || 'Tool', query: tc.query || '', results: tc.results || '', sources: tc.sources || [] } })
      })
    }

    events.push({
      type: 'subagent_complete',
      data: {
        id: i,
        topic: r.topic || `Report ${i + 1}`,
        report: r.report || '',
        steps_used: r.steps_used || 0,
        forced_summary: r.forced_summary || false,
        sources: r.sources || [],
        round: r.round || 1,
      }
    })
  })

  if (isComplete) {
    events.push({ type: 'phase', data: { phase: 'synthesizing', message: 'Research synthesis complete' } })
    events.push({
      type: 'result',
      data: {
        markdown_report: session.markdown_report || session.rationale || '',
        conviction_score: session.conviction_score ?? session.fundamental_shift ?? 0,
        fundamental_shift: session.fundamental_shift ?? session.conviction_score ?? 0,
        rationale: session.rationale || session.markdown_report || '',
        top_reports: Array.isArray(session.top_reports) ? session.top_reports : [],
        visualizations: (() => {
          try {
            const v = session.visualizations
            if (Array.isArray(v)) return v
            return typeof v === 'string' ? JSON.parse(v) : []
          } catch { return [] }
        })(),
        mispricing_report: (() => {
          try {
            const m = session.mispricing_report
            if (m && typeof m === 'object' && !Array.isArray(m)) return m
            return typeof m === 'string' ? JSON.parse(m) : {}
          } catch { return {} }
        })(),
        principal_model: session.principal_model || 'deepseek-v4-pro',
        subagent_model: session.subagent_model || 'deepseek-v4-flash',
        max_subagents: session.max_subagents || 9,
        rounds: session.round_number || session.rounds || Math.ceil(subReports.length / 3),
      }
    })
    events.push({ type: 'done', data: { session_id: session.id } })
  } else {
    events.push({
      type: 'phase',
      data: {
        phase: 'planning',
        message: `Research is ${session.status || 'incomplete'} — ${subReports.length} agent${subReports.length !== 1 ? 's' : ''} completed so far.`
      }
    })
  }

  return { events, maxSteps }
}

export function AIResearchPage() {
  const {
    events,
    streaming,
    sessionId,
    maxSteps,
    hasActive,
    start,
    clear,
    cancel,
    resume,
  } = useResearchRunner()

  const [historyCollapsed, setHistoryCollapsed] = useState(true)
  const [selectedHistoryId, setSelectedHistoryId] = useState<number | null>(null)
  const [selectedSessionStatus, setSelectedSessionStatus] = useState<string | null>(null)
  const [resumingId, setResumingId] = useState<number | null>(null)
  const [modalAgentId, setModalAgentId] = useState<number | null>(null)
  const [auditEvents, setAuditEvents] = useState<ResearchEvent[] | null>(null)
  const [auditMaxSteps, setAuditMaxSteps] = useState(3)
  const [historyKey, setHistoryKey] = useState(0)
  const prevCompleted = useRef(false)

  const isAudit = selectedHistoryId !== null && auditEvents !== null
  const displayEvents = isAudit ? auditEvents : events
  const displayStreaming = isAudit ? false : streaming
  const displayMaxSteps = isAudit ? auditMaxSteps : maxSteps
  const displaySessionId = isAudit ? selectedHistoryId : sessionId

  const subAgents = buildSubAgentsFromEvents(displayEvents)
  const modalAgent = modalAgentId !== null ? subAgents.find(a => a.id === modalAgentId) || null : null
  const hasCompleted = displayEvents.some(e => e.type === 'done')

  useEffect(() => {
    if (hasCompleted && !prevCompleted.current) {
      setHistoryKey(k => k + 1)
    }
    prevCompleted.current = hasCompleted
  }, [hasCompleted])

  useEffect(() => {
    if (!hasActive && resumingId) {
      setResumingId(null)
    }
  }, [hasActive, resumingId])

  const handleStart = useCallback(async (params: Record<string, any>) => {
    setSelectedHistoryId(null)
    setSelectedSessionStatus(null)
    setAuditEvents(null)
    setModalAgentId(null)
    start(params)
  }, [start])

  const handleNewResearch = useCallback(() => {
    clear()
    setSelectedHistoryId(null)
    setSelectedSessionStatus(null)
    setAuditEvents(null)
    setModalAgentId(null)
  }, [clear])

  const handleCancel = useCallback(() => {
    cancel()
  }, [cancel])

  const handleResume = useCallback(async (session: HistorySession) => {
    setSelectedHistoryId(null)
    setSelectedSessionStatus(null)
    setAuditEvents(null)
    setModalAgentId(null)
    setResumingId(session.id)
    resume(session.id)
  }, [resume])

  const handleHistorySelect = useCallback(async (session: HistorySession) => {
    setSelectedHistoryId(session.id)
    setSelectedSessionStatus(session.status)
    setModalAgentId(null)

    try {
      const data = await api.pm.aiResearch.get(session.id)
      const full = data.session || data
      const { events: loadedEvents, maxSteps: loadedMaxSteps } = loadSessionAsEvents(full)
      setAuditEvents(loadedEvents)
      setAuditMaxSteps(loadedMaxSteps)
    } catch {
      setAuditEvents([{ type: 'error', data: { error: 'Failed to load session' } }])
    }
  }, [])

  return (
    <div className="flex gap-0 h-[calc(100vh-140px)]">
      <div className="hidden lg:block">
        <ResearchHistory
          collapsed={historyCollapsed}
          onToggle={() => setHistoryCollapsed(!historyCollapsed)}
          selectedId={selectedHistoryId}
          onSelect={handleHistorySelect}
          refreshKey={historyKey}
        />
      </div>

      <div className="flex-1 min-w-0 flex gap-4 px-4">
        <div className="flex-1 min-w-0 space-y-4 overflow-y-auto">
          {(hasCompleted || hasActive || isAudit) && (
            <div className="flex items-center gap-2">
              <button
                onClick={handleNewResearch}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-accent/15 border border-accent/30 text-accent hover:bg-accent/25 transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                New Research
              </button>
              {hasActive && !hasCompleted && (
                <>
                  <span className="text-[10px] text-amber-400 animate-pulse">
                    Research running in background
                  </span>
                  <button
                    onClick={handleCancel}
                    className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 transition-colors ml-2"
                  >
                    <Square className="w-3 h-3" />
                    Stop Research
                  </button>
                </>
              )}
            </div>
          )}

          {isAudit && selectedSessionStatus === 'failed' && (
            <div className="flex items-center gap-3 p-4 bg-amber-950/30 border border-amber-800/40 rounded-lg animate-in fade-in slide-in-from-top-2 duration-300">
              <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-amber-300">Research was interrupted</div>
                <div className="text-xs text-amber-400/70 mt-0.5">
                  {subAgents.length} agent{subAgents.length !== 1 ? 's' : ''} completed.
                  Resume to continue from where it left off.
                </div>
              </div>
              <button
                onClick={() => selectedHistoryId && handleResume({ id: selectedHistoryId, status: selectedSessionStatus } as HistorySession)}
                disabled={resumingId === selectedHistoryId}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent/20 border border-accent/40 text-xs text-accent hover:bg-accent/30 transition-colors disabled:opacity-50 shrink-0"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                {resumingId === selectedHistoryId ? 'Resuming...' : 'Resume Research'}
              </button>
            </div>
          )}

          <ResearchForm streaming={displayStreaming} onStart={handleStart} />

          <PrincipalAgentStream
            events={displayEvents}
            streaming={displayStreaming}
            subReports={subAgents}
            onSubAgentClick={(id) => setModalAgentId(id)}
          />

          {displaySessionId && hasCompleted && (
            <div className="text-xs text-muted text-center">
              Session #{displaySessionId} saved to history
            </div>
          )}
        </div>

        <div className="w-[340px] shrink-0 hidden xl:block">
          <div className="bg-card border border-border rounded-lg">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
              <Brain className="w-4 h-4 text-muted" />
              <span className="text-sm font-medium text-foreground">Sub-Agents</span>
              {subAgents.length > 0 && (
                <span className="text-[10px] text-muted ml-auto">{subAgents.length} agents</span>
              )}
            </div>

            <div className="p-3 space-y-2 max-h-[calc(100vh-300px)] overflow-y-auto">
              {subAgents.length === 0 ? (
                <div className="text-xs text-muted text-center py-8">
                  {displayStreaming
                    ? 'Waiting for agents to start...'
                    : 'Sub-agent cards will appear here during research.'}
                </div>
              ) : (
                subAgents.map(agent => (
                  <SubAgentCard
                    key={agent.id}
                    agent={agent}
                    maxSteps={displayMaxSteps}
                    onClick={() => setModalAgentId(agent.id)}
                  />
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      {modalAgent && (
        <SubAgentModal agent={modalAgent} onClose={() => setModalAgentId(null)} />
      )}
    </div>
  )
}
