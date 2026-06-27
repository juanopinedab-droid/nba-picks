import { useState, useEffect } from 'react'
import { ChevronLeft, ChevronRight, Clock } from 'lucide-react'
import { api } from '@/lib/api'

export interface HistorySession {
  id: number
  created_at: string
  question: string
  market_slug?: string
  event_slug?: string
  market_price?: number
  fundamental_shift: number
  status: string
  conviction_score?: number
  round_number?: number
}

interface ResearchHistoryProps {
  collapsed: boolean
  onToggle: () => void
  selectedId: number | null
  onSelect: (session: HistorySession) => void
  refreshKey?: number
}

function formatDate(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso + 'Z')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

function statusLabel(status: string): string {
  if (status === 'failed') return 'Interrupted'
  return ''
}

function shiftColor(shift: number): string {
  if (shift > 0.05) return 'text-green-400'
  if (shift > 0) return 'text-green-400/70'
  if (shift < -0.05) return 'text-red-400'
  if (shift < 0) return 'text-red-400/70'
  return 'text-slate-400'
}

export function ResearchHistory({ collapsed, onToggle, selectedId, onSelect, refreshKey }: ResearchHistoryProps) {
  const [sessions, setSessions] = useState<HistorySession[]>([])

  useEffect(() => {
    api.pm.aiResearch.history(20).then(d => {
      const all = (d.sessions || []) as HistorySession[]
      setSessions(all.filter(s => s.status !== 'running'))
    }).catch(() => {})
  }, [refreshKey])

  if (collapsed) {
    return (
      <div className="w-4 shrink-0 flex flex-col items-center pt-4">
        <button
          onClick={onToggle}
          className="p-0.5 rounded hover:bg-slate-700/20 text-muted hover:text-foreground transition-colors"
          title="Expand history"
        >
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
    )
  }

  return (
    <div className="w-[260px] shrink-0 flex flex-col border-r border-border bg-sidebar min-h-0">
      <div className="flex items-center justify-between px-3 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5 text-muted" />
          <span className="text-xs font-medium text-foreground">History</span>
        </div>
        <button
          onClick={onToggle}
          className="p-0.5 rounded hover:bg-slate-700/20 text-muted hover:text-foreground transition-colors"
          title="Collapse history"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {sessions.length === 0 ? (
          <div className="text-[11px] text-muted text-center py-8 px-3">
            No research sessions yet.
          </div>
        ) : (
          <div className="py-1">
            {sessions.map(s => {
                const label = statusLabel(s.status)
                return (
              <div
                key={s.id}
                className={`w-full text-left transition-colors border-l-[3px] ${
                  selectedId === s.id
                    ? 'border-l-accent bg-accent/5'
                    : 'border-l-transparent hover:bg-slate-700/10'
                }`}
              >
                <button
                  onClick={() => onSelect(s)}
                  className="w-full text-left px-3 py-2"
                >
                <div className="text-[10px] text-muted">{formatDate(s.created_at)}</div>
                <div className="text-xs text-foreground/80 truncate mt-0.5">{s.question}</div>
                <div className={`text-[11px] font-mono font-medium mt-0.5 ${shiftColor(s.fundamental_shift)}`}>
                  {(s.fundamental_shift || 0) >= 0 ? '+' : ''}{(s.fundamental_shift || 0).toFixed(2)}
                  {label && (
                    <span className="text-[9px] text-amber-400/70 ml-1">{label}</span>
                  )}
                </div>
                {s.conviction_score != null && s.conviction_score !== 0 && (
                  <div className="text-[9px] text-slate-500 mt-0.5">
                    conv: {(s.conviction_score >= 0 ? '+' : '') + s.conviction_score.toFixed(2)}
                    {s.round_number != null && s.round_number > 0 && (
                      <span className="ml-2">{s.round_number} round{s.round_number !== 1 ? 's' : ''}</span>
                    )}
                  </div>
                )}
                {s.round_number != null && s.round_number > 0 && s.conviction_score == null && (
                  <div className="text-[9px] text-slate-500 mt-0.5">
                    {s.round_number} round{s.round_number !== 1 ? 's' : ''}
                  </div>
                )}
                </button>
              </div>
            )})}
          </div>
        )}
      </div>
    </div>
  )
}
