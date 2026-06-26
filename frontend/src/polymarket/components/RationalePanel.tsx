import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

interface Signal {
  name: string
  impact: number
  weight: number
  type: string
  color: string
  detail?: string
}

interface RationalePanelProps {
  rationale: {
    base_price: number
    signals: Signal[]
    raw_directional: number
    confidence_factor: number
    adjustment: number
    adjusted_prob: number
  }
  strategy: string
  llmReasoning?: string
  newsUsed?: string
  fundamentalShift?: number
}

function signalBarColor(color: string) {
  if (color === 'green') return 'bg-green-500'
  if (color === 'red') return 'bg-red-500'
  return 'bg-gray-500'
}

export function RationalePanel({ rationale, strategy, llmReasoning, newsUsed, fundamentalShift }: RationalePanelProps) {
  const [expanded, setExpanded] = useState(false)
  const [showSubagents, setShowSubagents] = useState(false)

  const hasLLMSignal = rationale.signals.some(s => s.name.includes('AI Oracle'))

  let subagentReports: any[] = []
  try {
    subagentReports = typeof newsUsed === 'string' ? JSON.parse(newsUsed || '[]') : []
  } catch { /* ignore */ }

  return (
    <div className="mt-2 border-t border-border pt-2">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-muted hover:text-foreground"
      >
        {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        Rationale
      </button>

      {expanded && (
        <div className="mt-2 space-y-1.5 text-xs">
          <div className="text-muted">
            Base: <span className="font-mono text-foreground">{rationale.base_price.toFixed(4)}</span>
            {' '}&rarr;{' '}
            Adjusted: <span className="font-mono text-foreground">{rationale.adjusted_prob.toFixed(4)}</span>
          </div>

          {rationale.signals.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-32 text-muted truncate">{s.name}</span>
              <div className="flex-1 h-2 bg-muted rounded overflow-hidden">
                <div
                  className={`h-full rounded ${signalBarColor(s.color)}`}
                  style={{ width: `${Math.min(Math.abs(s.impact) * 200, 100)}%` }}
                />
              </div>
              <span className={`w-14 text-right font-mono ${
                s.color === 'green' ? 'text-green-600' : s.color === 'red' ? 'text-red-600' : 'text-muted'
              }`}>
                {s.impact >= 0 ? '+' : ''}{s.impact.toFixed(4)}
              </span>
              <span className="w-10 text-right text-muted">
                {(s.weight * 100).toFixed(0)}%
              </span>
            </div>
          ))}

          <div className="flex justify-between text-muted pt-1 border-t border-border">
            <span>Directional: {rationale.raw_directional.toFixed(4)}</span>
            <span>Confidence: {rationale.confidence_factor.toFixed(4)}</span>
          </div>

          {subagentReports.length > 0 && (
            <div className="mt-2">
              <button
                onClick={() => setShowSubagents(!showSubagents)}
                className="flex items-center gap-1 text-xs text-muted hover:text-foreground"
              >
                {showSubagents ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                Sub-agent reports ({subagentReports.length})
              </button>
              {showSubagents && (
                <div className="mt-1 space-y-1 max-h-48 overflow-y-auto">
                  {subagentReports.map((r: any, i: number) => (
                    <div key={i} className="p-1.5 bg-muted/30 rounded text-[11px]">
                      <div className="font-medium text-foreground">{r.topic}</div>
                      <div className="text-muted line-clamp-3">{r.report}</div>
                      <div className="text-muted mt-0.5">
                        {r.steps} step{r.steps !== 1 ? 's' : ''}
                        {r.forced ? ' (forced)' : ''}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
