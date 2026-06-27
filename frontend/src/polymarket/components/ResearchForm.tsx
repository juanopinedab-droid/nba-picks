import { useState, useEffect } from 'react'
import { Search, X, ChevronDown, ChevronUp, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { api } from '@/lib/api'

interface ResearchFormProps {
  streaming: boolean
  onStart: (params: {
    question: string
    context: string
    prompt_customization: string
    fixed_data: string
    max_steps: number
    max_subagents: number
    max_rounds: number
    min_visualizations: number
    min_mispricing_calls: number
    force_top_reports: boolean
    principal_model: string
    subagent_model: string
  }) => void
}

export function ResearchForm({ streaming, onStart }: ResearchFormProps) {
  const [question, setQuestion] = useState('')
  const [maxSteps, setMaxSteps] = useState(3)
  const [maxSubagents, setMaxSubagents] = useState(9)
  const [maxRounds, setMaxRounds] = useState(5)
  const [principalModel, setPrincipalModel] = useState('deepseek-v4-pro')
  const [subagentModel, setSubagentModel] = useState('deepseek-v4-flash')
  const [tag, setTag] = useState('')
  const [tags, setTags] = useState<any[]>([])
  const [markets, setMarkets] = useState<any[]>([])
  const [marketSlug, setMarketSlug] = useState('')
  const [promptCustomization, setPromptCustomization] = useState('')
  const [fixedData, setFixedData] = useState('')
  const [showFixed, setShowFixed] = useState(false)
  const [showMisc, setShowMisc] = useState(false)
  const [minVisualizations, setMinVisualizations] = useState(0)
  const [minMispricingCalls, setMinMispricingCalls] = useState(0)
  const [forceTopReports, setForceTopReports] = useState(true)

  const questionValid = question.trim().length >= 10

  useEffect(() => {
    api.pm.tags().then(d => setTags(d.tags || [])).catch(() => {})
  }, [])

  useEffect(() => {
    if (!tag) {
      setMarkets([])
      return
    }
    api.pm.scanner.run({ tag, limit: 10, min_volume: 1000, min_liquidity: 100 })
      .then(({ job_id }: any) => {
        const poll = setInterval(async () => {
          try {
            const st = await api.pm.scanner.status(job_id)
            if (st.status === 'completed') {
              clearInterval(poll)
              setMarkets(st.result?.opportunities || [])
            }
            if (st.status === 'failed') clearInterval(poll)
          } catch {
            clearInterval(poll)
          }
        }, 800)
      })
      .catch(() => {})
  }, [tag])

  const handleMarketSelect = (slug: string) => {
    const m = markets.find((x: any) => x.market_slug === slug)
    if (m) {
      setMarketSlug(slug)
      setQuestion(m.question || '')
    }
  }

  const handleStart = () => {
    if (!questionValid) return
    onStart({
      question: question.trim(),
      context: tag || '',
      prompt_customization: promptCustomization,
      fixed_data: fixedData,
      max_steps: maxSteps,
      max_subagents: maxSubagents,
      max_rounds: maxRounds,
      min_visualizations: minVisualizations,
      min_mispricing_calls: minMispricingCalls,
      force_top_reports: forceTopReports,
      principal_model: principalModel,
      subagent_model: subagentModel,
    })
  }

  const handleClear = () => {
    setQuestion('')
    setMaxSteps(3)
    setMaxSubagents(9)
    setMaxRounds(5)
    setMinVisualizations(0)
    setMinMispricingCalls(0)
    setForceTopReports(true)
    setPrincipalModel('deepseek-v4-pro')
    setSubagentModel('deepseek-v4-flash')
    setTag('')
    setMarkets([])
    setMarketSlug('')
    setPromptCustomization('')
    setFixedData('')
  }

  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <label className="text-xs text-muted block">Research Question</label>
          <textarea
            value={question}
            onChange={e => setQuestion(e.target.value)}
            placeholder="e.g., Will ETH exceed $10K by end of 2026?"
            rows={2}
            maxLength={2000}
            disabled={streaming}
            className="w-full rounded border border-border bg-background text-foreground text-sm px-3 py-2 resize-none placeholder:text-slate-500/50 focus:border-accent/50 focus:outline-none"
          />
          {question.length > 0 && question.length < 10 && (
            <span className="text-[10px] text-red-400">Min 10 characters</span>
          )}
        </div>

        <div className="space-y-2">
          <div className="space-y-1">
            <label className="text-xs text-muted block">Tag (optional)</label>
            <select
              value={tag}
              onChange={e => { setTag(e.target.value); setMarkets([]); setMarketSlug('') }}
              disabled={streaming}
              className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5"
            >
              <option value="">Select tag...</option>
              {tags.map((t: any) => (
                <option key={t.slug || t.label} value={t.slug || ''}>
                  {t.label || t.slug}
                </option>
              ))}
            </select>
          </div>

          {markets.length > 0 && (
            <div className="space-y-1">
              <label className="text-xs text-muted block">Available Markets</label>
              <select
                value={marketSlug}
                onChange={e => handleMarketSelect(e.target.value)}
                disabled={streaming}
                className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5"
              >
                <option value="">Choose to auto-fill...</option>
                {markets.map((m: any) => (
                  <option key={m.market_slug} value={m.market_slug}>
                    {m.question || m.market_slug} ({m.market_price?.toFixed(2) || '?'})
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <div className="space-y-1">
            <label className="text-xs text-muted block">
              Max Steps per Agent: <span className="font-mono text-foreground">{maxSteps === 0 ? '∞' : maxSteps}</span>
            </label>
            <input
              type="range"
              min={0}
              max={20}
              step={1}
              value={maxSteps}
              onChange={e => setMaxSteps(Number(e.target.value))}
              disabled={streaming}
              className="w-28 sm:w-36 accent-accent"
            />
            <div className="flex justify-between text-[10px] text-muted w-28 sm:w-36">
              <span>∞</span>
              <span>10</span>
              <span>20</span>
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-xs text-muted block">
              Agents: <span className="font-mono text-foreground">{maxSubagents}</span>
            </label>
            <select
              value={maxSubagents}
              onChange={e => setMaxSubagents(Number(e.target.value))}
              disabled={streaming}
              className="w-16 sm:w-20 rounded border border-border bg-background text-foreground text-sm px-1.5 sm:px-2 py-1.5"
            >
              <option value={3}>3</option>
              <option value={6}>6</option>
              <option value={9}>9</option>
              <option value={12}>12</option>
              <option value={15}>15</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-xs text-muted block">
              Max Rounds: <span className="font-mono text-foreground">{maxRounds}</span>
            </label>
            <select
              value={maxRounds}
              onChange={e => setMaxRounds(Number(e.target.value))}
              disabled={streaming}
              className="w-16 sm:w-20 rounded border border-border bg-background text-foreground text-sm px-1.5 sm:px-2 py-1.5"
            >
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
              <option value={5}>5</option>
              <option value={7}>7</option>
              <option value={10}>10</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-xs text-muted block">Main Model</label>
            <select
              value={principalModel}
              onChange={e => setPrincipalModel(e.target.value)}
              disabled={streaming}
              className="w-32 sm:w-44 rounded border border-border bg-background text-foreground text-xs px-2 py-1.5"
            >
              <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
              <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-xs text-muted block">Sub Model</label>
            <select
              value={subagentModel}
              onChange={e => setSubagentModel(e.target.value)}
              disabled={streaming}
              className="w-32 sm:w-44 rounded border border-border bg-background text-foreground text-xs px-2 py-1.5"
            >
              <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
              <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
            </select>
          </div>

          <div className="flex items-center gap-2 self-end">
            <Button onClick={handleStart} disabled={streaming || !questionValid} size="sm">
              {streaming ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Search className="w-4 h-4 mr-1" />}
              Start Research
            </Button>
            <Button onClick={handleClear} disabled={streaming} variant="ghost" size="sm">
              <X className="w-4 h-4 mr-1" />
              Clear
            </Button>
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs text-muted block">
            Research Prompt Customization
            <span className="text-slate-400/50 ml-1">(max 2000 chars)</span>
          </label>
          <textarea
            value={promptCustomization}
            onChange={e => setPromptCustomization(e.target.value)}
            placeholder="Focus on concrete evidence: statistics, official statements, expert consensus, and macroeconomic signals. Avoid speculation."
            rows={2}
            maxLength={2000}
            disabled={streaming}
            className="w-full rounded border border-border bg-background text-foreground text-sm px-3 py-2 resize-none placeholder:text-slate-500/50 focus:border-accent/50 focus:outline-none"
          />
        </div>

        <div>
          <button
            onClick={() => setShowFixed(!showFixed)}
            disabled={streaming}
            className="flex items-center gap-1 text-xs text-muted hover:text-foreground transition-colors"
          >
            {showFixed ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            Fixed Reference Data
          </button>
          {showFixed && (
            <div className="mt-2 space-y-1">
              <textarea
                value={fixedData}
                onChange={e => setFixedData(e.target.value)}
                placeholder="Hard data all agents will use as base context..."
                rows={3}
                maxLength={5000}
                disabled={streaming}
                className="w-full rounded border border-border bg-background text-foreground text-sm px-3 py-2 resize-none placeholder:text-slate-500/50 focus:border-accent/50 focus:outline-none"
              />
            </div>
          )}
        </div>

        <div>
          <button
            onClick={() => setShowMisc(!showMisc)}
            disabled={streaming}
            className="flex items-center gap-1 text-xs text-muted hover:text-foreground transition-colors"
          >
            {showMisc ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            Misc
          </button>
          {showMisc && (
            <div className="mt-2 space-y-3 pl-1">
              <div className="space-y-1">
                <label className="text-xs text-muted block">
                  Min Chart Calls: <span className="font-mono text-foreground">{minVisualizations === 0 ? 'optional' : minVisualizations}</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={5}
                  step={1}
                  value={minVisualizations}
                  onChange={e => setMinVisualizations(Number(e.target.value))}
                  disabled={streaming}
                  className="w-44 accent-accent"
                />
                <div className="flex justify-between text-[10px] text-muted w-44">
                  <span>0 (off)</span>
                  <span>3</span>
                  <span>5</span>
                </div>
                <div className="text-[10px] text-muted/60 mt-0.5">Min calls to spawn_visualization_agent (0 = optional)</div>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted block">
                  Min Mispricing Calls: <span className="font-mono text-foreground">{minMispricingCalls === 0 ? 'optional' : minMispricingCalls}</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={5}
                  step={1}
                  value={minMispricingCalls}
                  onChange={e => setMinMispricingCalls(Number(e.target.value))}
                  disabled={streaming}
                  className="w-44 accent-accent"
                />
                <div className="flex justify-between text-[10px] text-muted w-44">
                  <span>0 (off)</span>
                  <span>3</span>
                  <span>5</span>
                </div>
                <div className="text-[10px] text-muted/60 mt-0.5">Min calls to spawn_mispricing_agent (0 = optional)</div>
              </div>
              <div className="space-y-1">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    className="checkbox-custom"
                    checked={forceTopReports}
                    onChange={e => setForceTopReports(e.target.checked)}
                    disabled={streaming}
                  />
                  Require 3 Most Influential Reports
                </label>
                <div className="text-[10px] text-muted/60">If checked, publish_final_report requires top_reports with 3 entries</div>
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
