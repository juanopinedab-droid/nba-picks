import { Clock, Loader2, CheckCircle, AlertTriangle, ChevronRight, Brain, Lightbulb } from 'lucide-react'

export type SubAgentStatus = 'pending' | 'in_progress' | 'thinking' | 'completed' | 'limit_reached'

export interface SubAgentToolCall {
  tool: string
  label: string
  query: string
  results: string
  sources?: { title: string; url: string }[]
}

export interface SubAgentStep {
  step: number
  reasoning?: string
  toolCalls: SubAgentToolCall[]
}

export interface SubAgentState {
  id: number
  topic: string
  status: SubAgentStatus
  steps: SubAgentStep[]
  report?: string
  stepsUsed?: number
  forcedSummary?: boolean
  timedOut?: boolean
  sources?: { title: string; url: string }[]
  round?: number
  reasoning?: string
}

interface SubAgentCardProps {
  agent: SubAgentState
  maxSteps: number
  onClick: () => void
}

const statusConfig: Record<SubAgentStatus, { icon: typeof Clock; color: string; label: string }> = {
  pending:       { icon: Clock,          color: 'text-slate-400', label: 'Waiting...' },
  in_progress:   { icon: Loader2,        color: 'text-blue-400',  label: 'Researching' },
  thinking:      { icon: Brain,          color: 'text-amber-400', label: 'Thinking' },
  completed:     { icon: CheckCircle,    color: 'text-green-400', label: 'Completed' },
  limit_reached: { icon: AlertTriangle,  color: 'text-amber-400', label: 'Call limit reached' },
}

export function SubAgentCard({ agent, maxSteps, onClick }: SubAgentCardProps) {
  const config = statusConfig[agent.status]
  const Icon = config.icon
  const lastStep = agent.steps[agent.steps.length - 1]
  const lastToolCall = lastStep?.toolCalls?.[lastStep.toolCalls.length - 1]
  const currentStep = lastStep?.step || agent.stepsUsed || 0
  const pct = maxSteps > 0 ? Math.min(100, (currentStep / maxSteps) * 100) : 100
  const isClickable = agent.status !== 'pending'

  return (
    <div
      className={`bg-card border border-border rounded-lg p-3 transition-colors ${
        isClickable ? 'hover:border-accent/40 cursor-pointer' : ''
      }`}
      onClick={isClickable ? onClick : undefined}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <Icon className={`w-3.5 h-3.5 ${config.color} ${agent.status === 'in_progress' || agent.status === 'thinking' ? 'animate-spin' : ''}`} />
        <span className={`text-[11px] font-medium ${config.color}`}>{config.label}</span>
        {agent.round != null && (
          <span className="text-[9px] text-slate-500 font-mono ml-auto">R{agent.round}</span>
        )}
        {agent.forcedSummary && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800 ml-auto">
            Limited
          </span>
        )}
        {agent.timedOut && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-950 text-red-300 border border-red-800 ml-auto">
            Timeout
          </span>
        )}
      </div>

      <div className="text-xs text-foreground font-medium mb-2 line-clamp-2">
        {agent.topic}
      </div>

      {agent.status === 'thinking' && (
        <div className="space-y-1 mb-2">
          <div className="flex items-center gap-1.5 text-[10px] text-amber-400/60">
            <Lightbulb className="w-3 h-3" />
            <span>{agent.reasoning?.slice(0, 100) || 'Analyzing data...'}</span>
          </div>
        </div>
      )}

      {agent.status !== 'pending' && agent.status !== 'thinking' && (
        <div className="space-y-1 mb-2">
          <div className="flex items-center justify-between text-[10px] text-muted">
            <span>Step {currentStep}{maxSteps > 0 ? `/${maxSteps}` : ''}</span>
          </div>
          <div className="w-full bg-slate-700/30 rounded-full h-1">
            <div
              className={`h-1 rounded-full transition-all duration-500 ${
                agent.status === 'completed' ? 'bg-green-500' : 'bg-blue-500'
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {lastToolCall && (
        <div className="text-[10px] text-muted truncate mb-1">
          <span className="text-slate-400/60">{lastToolCall.label}: </span>
          "{lastToolCall.query}"
        </div>
      )}

      {isClickable && (
        <button className="flex items-center gap-1 text-[10px] text-accent hover:text-accent-dark mt-1.5 transition-colors">
          <span>View details</span>
          <ChevronRight className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}
