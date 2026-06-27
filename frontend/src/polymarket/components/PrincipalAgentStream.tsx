import { Brain, Loader2, Sparkles, CheckCircle, XCircle, Target, Layers, Lightbulb } from 'lucide-react'
import { PhaseBadge, type Phase } from './PhaseBadge'
import { MarkdownRenderer } from './MarkdownRenderer'
import { DynamicChartRenderer, type ChartConfig } from './DynamicChartRenderer'
import { TopReportCard } from './TopReportCard'
import { MispricingTable } from './MispricingTable'
import type { SubAgentState } from './SubAgentCard'

export interface ResearchEvent {
  type: string
  data: Record<string, any>
}

interface PrincipalAgentStreamProps {
  events: ResearchEvent[]
  streaming: boolean
  subReports?: SubAgentState[]
  onSubAgentClick?: (id: number) => void
}

interface RoundSummary {
  round: number
  count: number
  total: number
  cap: number
  message: string
}

function buildRounds(events: ResearchEvent[]): RoundSummary[] {
  const MAX_CAP = 9
  const rounds: RoundSummary[] = []
  const spawnEvents = events.filter(e => e.type === 'agents_spawned')
  for (const e of spawnEvents) {
    const d = e.data
    const phaseEvent = events.filter(
      p => p.type === 'phase' && p.data?.round === d.round && p.data?.phase === 'researching'
    ).pop()
    rounds.push({
      round: d.round || 0,
      count: d.count || 0,
      total: d.total || 0,
      cap: d.cap || MAX_CAP,
      message: phaseEvent?.data?.message || `Round ${d.round} — ${d.count} agents`,
    })
  }
  // Show in-progress rounds from phase events that don't have agents_spawned yet
  const pendingPhaseEvents = events.filter(
    e => e.type === 'phase' && e.data?.phase === 'researching' && e.data?.round
  )
  for (const e of pendingPhaseEvents) {
    const d = e.data
    if (rounds.some(r => r.round === d.round)) continue
    rounds.push({
      round: d.round,
      count: 0,
      total: d.total ?? 0,
      cap: events.filter(se => se.type === 'agents_spawned').pop()?.data?.cap || MAX_CAP,
      message: d.message || `R${d.round} — in progress`,
    })
  }
  return rounds
}

function derivePhase(events: ResearchEvent[], streaming: boolean): Phase {
  if (!streaming && events.length === 0) return 'idle'
  const types = events.map(e => e.type)
  if (types.includes('error')) return 'error'
  if (types.includes('done') || types.includes('result')) return 'complete'
  const phaseData = events.filter(e => e.type === 'phase').map(e => e.data?.phase)
  if (phaseData.includes('finalizing')) return 'synthesizing'
  if (phaseData.includes('reviewing')) return 'synthesizing'
  if (phaseData.includes('researching')) return 'researching'
  if (phaseData.includes('thinking')) return 'planning'
  if (phaseData.includes('planning')) return 'planning'
  if (types.includes('topics')) return 'planning'
  return 'idle'
}

function SkeletonLine({ wide }: { wide?: boolean }) {
  return (
    <div className={`animate-pulse bg-slate-700/30 rounded h-3 ${wide ? 'w-3/4' : 'w-full'}`} />
  )
}

export function PrincipalAgentStream({ events, streaming, subReports = [], onSubAgentClick }: PrincipalAgentStreamProps) {
  const phase = derivePhase(events, streaming)
  const phaseEvent = events.find(e => e.type === 'phase')
  const resultEvent = events.find(e => e.type === 'result')
  const errorEvent = events.find(e => e.type === 'error')
  const agentsEvent = events.filter(e => e.type === 'agents_spawned').pop()
  const capEvent = events.find(e => e.type === 'cap_warning')
  const rounds = buildRounds(events)
  const principalReasonings = events.filter(e => e.type === 'agent_reasoning')

  const mispricingEvents = events.filter(e => e.type === 'mispricing_agent_complete')
  const allMispricingPicks = mispricingEvents.flatMap(e => e.data?.picks || [])
  const liveMispricing = allMispricingPicks.length > 0
    ? { summary: '', picks: allMispricingPicks }
    : null

  const vizEvents = events.filter(e => e.type === 'viz_agent_complete')
  const allCharts = vizEvents.flatMap(e => e.data?.charts || [])
  const liveCharts = allCharts.length > 0 ? allCharts : null

  if (phase === 'idle' && !streaming) return null

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain className="w-4 h-4 text-accent" />
          <span className="text-sm font-semibold text-foreground">AI Research</span>
          {agentsEvent && (
            <span className="text-[10px] text-muted ml-1">
              {agentsEvent.data.total}/{agentsEvent.data.cap} agents
            </span>
          )}
        </div>
        <PhaseBadge phase={phase} />
      </div>

      {capEvent && (
        <div className="text-[11px] text-amber-400/80 bg-amber-400/5 border border-amber-400/20 rounded px-2 py-1 animate-in fade-in">
          {capEvent.data.message}
        </div>
      )}

      {principalReasonings.length > 0 && (
        <details className="group animate-in fade-in duration-300">
          <summary className="flex items-center gap-1.5 cursor-pointer text-[10px] text-amber-400/60 hover:text-amber-400/80 transition-colors">
            <Lightbulb className="w-3 h-3" />
            <span>Main Agent Chain of Thought ({principalReasonings.length})</span>
          </summary>
          <div className="mt-2 space-y-2 pl-3.5 border-l-2 border-amber-800/30">
            {principalReasonings.map((e, i) => (
              <div key={i} className="text-xs text-amber-200/70 bg-amber-950/20 border border-amber-800/20 rounded p-2.5 leading-relaxed italic">
                {e.data.round != null && (
                  <span className="text-[9px] text-amber-500 font-mono block mb-1">Round {e.data.round}</span>
                )}
                {e.data.content}
              </div>
            ))}
          </div>
        </details>
      )}

      {phase === 'planning' && (
        <div className="space-y-2 animate-in fade-in duration-300">
          <div className="flex items-center gap-2 text-amber-300">
            <Brain className="w-4 h-4 animate-pulse" />
            <span className="text-sm">{phaseEvent?.data?.message || 'Main Agent analyzing...'}</span>
          </div>
          <div className="space-y-1.5 pl-6">
            <SkeletonLine />
            <SkeletonLine wide />
            <SkeletonLine wide />
          </div>
        </div>
      )}

      {phase === 'researching' && (
        <div className="space-y-2 animate-in fade-in duration-300">
          <div className="flex items-center gap-2 text-blue-300">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span className="text-sm">{phaseEvent?.data?.message || 'Researching...'}</span>
          </div>
          <div className="space-y-1.5 pl-6">
            <SkeletonLine />
            <SkeletonLine wide />
          </div>
        </div>
      )}

      {phase === 'synthesizing' && (
        <div className="space-y-2 animate-in fade-in duration-300">
          <div className="flex items-center gap-2 text-purple-300">
            <Sparkles className="w-4 h-4 animate-pulse" />
            <span className="text-sm">{phaseEvent?.data?.message || 'Reviewing findings...'}</span>
          </div>
          <div className="space-y-1.5 pl-6">
            <SkeletonLine />
            <SkeletonLine wide />
            <SkeletonLine wide />
          </div>
        </div>
      )}

      {rounds.length > 0 && (
        <div className="space-y-1 pt-1 border-t border-border/50">
          {rounds.map((r, i) => (
            <div
              key={r.round}
              className="flex items-center gap-2 text-xs animate-in fade-in slide-in-from-left-2 duration-300"
              style={{ animationDelay: `${i * 100}ms` }}
            >
              <Layers className="w-3 h-3 text-slate-500" />
              <span className="text-slate-400 font-mono text-[10px]">R{r.round}</span>
              <span className="text-muted">{r.message}</span>
              <span className="text-[10px] text-slate-500 ml-auto">
                {r.total}/{r.cap}
              </span>
            </div>
          ))}
        </div>
      )}

      {liveMispricing && (
        <div className="animate-in fade-in duration-300">
          <MispricingTable report={liveMispricing} />
        </div>
      )}

      {liveCharts && (
        <DynamicChartRenderer charts={liveCharts as ChartConfig[]} />
      )}

      {phase === 'complete' && resultEvent && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-green-300 animate-in fade-in duration-300">
            <CheckCircle className="w-4 h-4" />
            <span className="text-sm font-medium">Research Complete</span>
          </div>

          <div className="flex items-center gap-2 text-[10px] text-slate-500 -mt-1 flex-wrap">
            {resultEvent.data?.principal_model && (
              <>
                <span className="font-mono">{resultEvent.data.principal_model}</span>
                <span className="text-slate-600">/</span>
                <span className="font-mono">{resultEvent.data.subagent_model}</span>
                <span className="text-slate-600">x{resultEvent.data.max_subagents || 9}</span>
              </>
            )}
            {resultEvent.data?.rounds != null && (
              <span className="text-slate-600 ml-1">{resultEvent.data.rounds} rounds</span>
            )}
          </div>

          {!liveMispricing && resultEvent.data?.mispricing_report?.picks?.length > 0 && (
            <div className="animate-in fade-in duration-300">
              <MispricingTable report={resultEvent.data.mispricing_report} />
            </div>
          )}

          {!liveMispricing && !resultEvent.data?.mispricing_report?.picks?.length && (
            <div className="flex items-center gap-2 py-1 animate-in fade-in duration-300">
              <Target className="w-4 h-4 text-accent/60" />
              <span className="text-xs text-muted">Mispricing analysis unavailable for this market</span>
            </div>
          )}

          {!liveCharts && resultEvent.data?.visualizations && resultEvent.data.visualizations.length > 0 && (
            <DynamicChartRenderer charts={resultEvent.data.visualizations as ChartConfig[]} />
          )}

          {resultEvent.data?.top_reports && resultEvent.data.top_reports.length > 0 && (
            <div className="space-y-2 animate-in fade-in duration-300">
              <span className="text-[10px] font-medium text-muted uppercase tracking-wider">
                Most Influential Reports
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                {resultEvent.data.top_reports.map((report: any, idx: number) => {
                  const agent = subReports.find(a =>
                    a.topic === report.topic && a.report === report.report
                  )
                  return (
                    <TopReportCard
                      key={idx}
                      topic={report.topic || `Report ${idx + 1}`}
                      preview={(report.report || '').slice(0, 150)}
                      round={report.round || 0}
                      onClick={() => {
                        if (agent != null) onSubAgentClick?.(agent.id)
                        else if (idx < subReports.length) onSubAgentClick?.(idx)
                      }}
                    />
                  )
                })}
              </div>
            </div>
          )}

          {resultEvent.data?.markdown_report && (
            <div className="border-t border-border pt-4 animate-in fade-in slide-in-from-bottom-2 duration-500">
              <div className="bg-background border border-border rounded-lg p-5">
                <MarkdownRenderer content={resultEvent.data.markdown_report} />
              </div>
            </div>
          )}

          {!liveCharts && resultEvent.data?.visualizations && resultEvent.data.visualizations.length > 0 && (
            <DynamicChartRenderer charts={resultEvent.data.visualizations as ChartConfig[]} />
          )}
        </div>
      )}

      {phase === 'error' && (
        <div className="flex items-center gap-2 text-red-300 animate-in fade-in duration-300">
          <XCircle className="w-4 h-4" />
          <span className="text-sm">
            {errorEvent?.data?.error || 'An error occurred during research.'}
          </span>
        </div>
      )}
    </div>
  )
}
