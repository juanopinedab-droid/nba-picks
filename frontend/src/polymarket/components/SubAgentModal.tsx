import { useState } from 'react'
import { X, AlertTriangle, ExternalLink, Globe, BarChart3, Brain, Lightbulb, Search, Clock, ChevronDown, ChevronRight, Wrench, TrendingUp, FileText } from 'lucide-react'
import { type SubAgentState, type SubAgentToolCall } from './SubAgentCard'
import { MarkdownRenderer } from './MarkdownRenderer'

interface SubAgentModalProps {
  agent: SubAgentState
  onClose: () => void
}

interface SourceItem {
  title: string
  url: string
  sourceType: 'web' | 'polymarket' | 'finance' | 'webpage'
}

function truncateUrl(url: string, maxLen = 50): string {
  return url.length > maxLen ? url.slice(0, maxLen) + '...' : url
}

function detectSourceType(s: { title: string; url: string }): 'web' | 'polymarket' | 'finance' | 'webpage' {
  if (
    s.title.toLowerCase().includes('polymarket ') ||
    s.title.toLowerCase().includes('polymarket api') ||
    s.url.includes('polymarket.com')
  ) {
    return 'polymarket'
  }
  if (
    s.title.toLowerCase().includes('yahoo finance') ||
    s.url.includes('finance.yahoo.com')
  ) {
    return 'finance'
  }
  if (
    s.title.startsWith('http://') ||
    s.title.startsWith('https://')
  ) {
    return 'webpage'
  }
  return 'web'
}

function collectSources(agent: SubAgentState): SourceItem[] {
  const seen = new Set<string>()
  const sources: SourceItem[] = []

  for (const s of agent.sources || []) {
    if (s.url && !seen.has(s.url)) {
      seen.add(s.url)
      sources.push({ ...s, sourceType: detectSourceType(s) })
    }
  }
  for (const step of agent.steps) {
    for (const tc of step.toolCalls || []) {
      for (const s of tc.sources || []) {
        if (s.url && !seen.has(s.url)) {
          seen.add(s.url)
          sources.push({ ...s, sourceType: detectSourceType(s) })
        }
      }
    }
  }
  return sources
}

function ReasoningBlock({ content, thinking }: { content: string; thinking?: boolean }) {
  if (!content) return null
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        {thinking ? (
          <Clock className="w-3 h-3 text-amber-400 animate-spin" />
        ) : (
          <Lightbulb className="w-3 h-3 text-amber-400" />
        )}
        <span className="text-[10px] font-medium text-amber-300">
          {thinking ? 'Thinking...' : 'Chain of Thought'}
        </span>
      </div>
      <div className="bg-amber-950/20 border border-amber-800/30 rounded p-3 text-xs text-amber-200/80 leading-relaxed italic">
        {content}
      </div>
    </div>
  )
}

function ToolCallCard({ tc, defaultExpanded }: { tc: SubAgentToolCall; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded ?? false)
  const hasResults = tc.results && tc.results.length > 0
  const isLoading = !hasResults && tc.query.length > 0

  return (
    <div className="bg-background border border-border rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-slate-700/10 transition-colors"
      >
        <Wrench className={`w-3.5 h-3.5 shrink-0 ${isLoading ? 'text-blue-400 animate-pulse' : 'text-slate-500'}`} />
        <span className="text-xs font-medium text-foreground/80">{tc.label}</span>
        <span className="text-[10px] text-muted truncate flex-1">"{tc.query}"</span>
        {isLoading && <Clock className="w-3 h-3 text-blue-400 animate-spin shrink-0" />}
        {hasResults && (
          expanded
            ? <ChevronDown className="w-3.5 h-3.5 text-muted shrink-0" />
            : <ChevronRight className="w-3.5 h-3.5 text-muted shrink-0" />
        )}
      </button>
      {expanded && hasResults && (
        <div className="border-t border-border px-3 py-2.5">
          <div className="text-sm text-foreground/80 leading-relaxed whitespace-pre-wrap">
            {tc.results}
          </div>
        </div>
      )}
      {expanded && !hasResults && !isLoading && (
        <div className="border-t border-border px-3 py-2.5 text-xs text-muted italic">
          No results returned.
        </div>
      )}
      {expanded && isLoading && (
        <div className="border-t border-border px-3 py-2.5 flex items-center gap-2 text-xs text-blue-400/70">
          <Clock className="w-3 h-3 animate-spin" />
          Waiting for results...
        </div>
      )}
    </div>
  )
}

export function SubAgentModal({ agent, onClose }: SubAgentModalProps) {
  const sources = collectSources(agent)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-card border border-border rounded-lg w-full max-w-2xl max-h-[80vh] flex flex-col mx-4 animate-in zoom-in-95 duration-300">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground truncate">{agent.topic}</h3>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-xs text-muted">
                {agent.stepsUsed || agent.steps.length} step{(agent.stepsUsed || agent.steps.length) !== 1 ? 's' : ''}
              </span>
              {agent.forcedSummary && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800 flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3" />
                  Forced summary (max steps reached)
                </span>
              )}
              {agent.timedOut && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-950 text-red-300 border border-red-800 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  Timed out
                </span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-slate-700/20 text-muted hover:text-foreground transition-colors shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {agent.reasoning && !agent.steps.length && (
            <ReasoningBlock content={agent.reasoning} thinking />
          )}

          {agent.steps.map((s, i) => (
            <div
              key={i}
              className="space-y-3 animate-in fade-in slide-in-from-bottom-1 duration-300"
              style={{ animationDelay: `${i * 100}ms` }}
            >
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono font-semibold text-accent bg-accent/10 px-2 py-0.5 rounded">
                  STEP {s.step}
                </span>
              </div>

              {s.reasoning && <ReasoningBlock content={s.reasoning} />}

              <div className="space-y-2">
                {s.toolCalls.map((tc, j) => (
                  <ToolCallCard
                    key={j}
                    tc={tc}
                    defaultExpanded={agent.status === 'completed' && s.toolCalls.length <= 2}
                  />
                ))}
                {s.toolCalls.length === 0 && (
                  <div className="text-xs text-muted italic pl-1">Waiting for tool calls...</div>
                )}
              </div>
            </div>
          ))}

          {agent.report && (
            <div className="border-t border-border pt-4 space-y-3 animate-in fade-in slide-in-from-bottom-2 duration-400">
              <span className="text-xs font-semibold text-foreground">FINAL REPORT</span>
              <div className="bg-background border border-border rounded-lg p-4">
                <MarkdownRenderer content={agent.report} />
              </div>
            </div>
          )}

          {sources.length > 0 && (
            <div className="border-t border-border pt-4 space-y-3 animate-in fade-in slide-in-from-bottom-2 duration-400">
              <span className="text-xs font-semibold text-foreground">SOURCES</span>
              <div className="bg-background border border-border rounded-lg divide-y divide-border">
                {sources.map((s, i) => (
                  <a
                    key={i}
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-start gap-2 px-3.5 py-3 text-sm hover:underline hover:bg-slate-700/10 transition-colors group"
                  >
                    {s.sourceType === 'polymarket' ? (
                      <BarChart3 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                    ) : s.sourceType === 'finance' ? (
                      <TrendingUp className="w-3.5 h-3.5 text-purple-400 shrink-0 mt-0.5" />
                    ) : s.sourceType === 'webpage' ? (
                      <FileText className="w-3.5 h-3.5 text-cyan-400 shrink-0 mt-0.5" />
                    ) : (
                      <Globe className="w-3.5 h-3.5 text-muted shrink-0 mt-0.5 group-hover:text-accent" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <div className="text-foreground/90 leading-snug">{s.title}</div>
                        <span className={`text-[9px] px-1.5 py-0.5 rounded border shrink-0 ${
                          s.sourceType === 'polymarket'
                            ? 'bg-emerald-950/50 text-emerald-300 border-emerald-800'
                            : s.sourceType === 'finance'
                            ? 'bg-purple-950/50 text-purple-300 border-purple-800'
                            : s.sourceType === 'webpage'
                            ? 'bg-cyan-950/50 text-cyan-300 border-cyan-800'
                            : 'bg-slate-800 text-slate-400 border-slate-700'
                        }`}>
                          {s.sourceType === 'polymarket' ? 'Polymarket' : s.sourceType === 'finance' ? 'Yahoo Finance' : s.sourceType === 'webpage' ? 'Scraped' : 'Web'}
                        </span>
                      </div>
                      <div className="text-xs text-muted truncate mt-0.5">{truncateUrl(s.url)}</div>
                    </div>
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="px-5 py-3 border-t border-border shrink-0 flex justify-end">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded bg-slate-700/30 text-muted hover:text-foreground hover:bg-slate-700/50 transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
