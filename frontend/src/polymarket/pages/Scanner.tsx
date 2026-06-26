import { useState, useRef, useEffect } from 'react'
import { Loader2, ScanLine, Settings2, Download, FlaskConical } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { Progress } from '@/components/ui/Progress'
import { Badge } from '@/components/ui/Badge'
import { api } from '@/lib/api'
import { MarketCard } from '../components/MarketCard'
import { RationalePanel } from '../components/RationalePanel'
import { WeightSliders } from '../components/WeightSliders'
import { LaboratoryModal } from '../components/LaboratoryModal'

const DEFAULT_WEIGHTS = {
  momentum: 0.25,
  imbalance: 0.25,
  fundamental: 0.25,
  sentiment: 0.10,
  time_penalty: 0.075,
  spread_penalty: 0.075,
}

export function ScannerPage() {
  const [tag, setTag] = useState('')
  const [tags, setTags] = useState<any[]>([])
  const [limit] = useState(20)
  const [minVolume, setMinVolume] = useState(5000)
  const [minLiquidity, setMinLiquidity] = useState(500)
  const [minEdge, setMinEdge] = useState(0.005)
  const [maxDays, setMaxDays] = useState(60)
  const [strategy, setStrategy] = useState('meta_consensus')
  const [fetchBooks, setFetchBooks] = useState(false)
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)
  const [showWeights, setShowWeights] = useState(false)

  const [scanning, setScanning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [statusText, setStatusText] = useState('')
  const [opportunities, setOpportunities] = useState<any[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const pollRef = useRef<any>(null)

  const [manualMarkets, setManualMarkets] = useState<any[]>([])
  const [manualProbs, setManualProbs] = useState<Record<string, string>>({})
  const [loadingMarkets, setLoadingMarkets] = useState(false)
  const [savedStrategies, setSavedStrategies] = useState<any[]>([])
  const [showLab, setShowLab] = useState(false)

  useEffect(() => {
    api.pm.tags().then(d => setTags(d.tags || [])).catch(() => {})
    api.pm.laboratory.strategies.list().then((d: any) => setSavedStrategies(d.strategies || [])).catch(() => {})
  }, [])

  const reloadStrategies = () => {
    api.pm.laboratory.strategies.list().then((d: any) => setSavedStrategies(d.strategies || [])).catch(() => {})
  }

  const toggleExpanded = (slug: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(slug)) next.delete(slug)
      else next.add(slug)
      return next
    })
  }

  const loadMarkets = async () => {
    setLoadingMarkets(true)
    setManualProbs({})
    try {
      const d = await api.pm.markets({ tag, limit, min_volume: minVolume })
      setManualMarkets(d.markets || [])
    } catch {
      setManualMarkets([])
    }
    setLoadingMarkets(false)
  }

  const setProb = (slug: string, value: string) => {
    if (value === '' || /^0?\.?\d*$/.test(value)) {
      setManualProbs(prev => {
        const next = { ...prev }
        if (value === '') delete next[slug]
        else next[slug] = value
        return next
      })
    }
  }

  const manualProbsCount = Object.keys(manualProbs).filter(k => manualProbs[k] !== '').length

  const scan = async () => {
    setScanning(true)
    setProgress(0)
    setStatusText('Submitting...')
    setOpportunities([])

    try {
      const isSaved = strategy.startsWith('saved:')
      const strategyId = isSaved ? parseInt(strategy.split(':')[1]) : null
      const effectiveStrategy = isSaved ? 'meta_consensus' : strategy

      const body: Record<string, any> = {
        tag,
        limit,
        min_volume: minVolume,
        min_liquidity: minLiquidity,
        min_edge: minEdge,
        max_days_to_resolution: maxDays,
        strategy: effectiveStrategy,
        fetch_orderbooks: fetchBooks,
      }

      if (strategyId) {
        body.strategy_id = strategyId
      }

      if (strategy === 'meta_consensus' || isSaved) {
        body.weight_momentum = weights.momentum
        body.weight_imbalance = weights.imbalance
        body.weight_fundamental = weights.fundamental
        body.weight_sentiment = weights.sentiment
        body.weight_time_penalty = weights.time_penalty
        body.weight_spread_penalty = weights.spread_penalty
      }

      if (strategy === 'manual') {
        const probs: Record<string, number> = {}
        for (const [slug, val] of Object.entries(manualProbs)) {
          const n = parseFloat(val)
          if (!isNaN(n) && n >= 0 && n <= 1) probs[slug] = n
        }
        body.manual_probs = probs
      }

      const { job_id } = await api.pm.scanner.run(body)

      pollRef.current = setInterval(async () => {
        try {
          const status = await api.pm.scanner.status(job_id)
          setProgress(status.progress || 0)
          const texts: Record<number, string> = {
            0.0: 'Fetching events...',
            0.05: 'Fetching orderbooks...',
            0.30: 'Analyzing markets...',
            0.65: 'Computing opportunities...',
            1.0: 'Done',
          }
          for (const [pct, txt] of Object.entries(texts)) {
            if ((status.progress || 0) >= parseFloat(pct)) {
              setStatusText(status.log_tail?.slice(-1)[0] || txt)
            }
          }

          if (status.status === 'completed') {
            clearInterval(pollRef.current)
            setScanning(false)
            setProgress(1.0)
            setStatusText('Complete')
            setOpportunities(status.result?.opportunities || [])
          }
          if (status.status === 'failed') {
            clearInterval(pollRef.current)
            setScanning(false)
            setStatusText('Failed: ' + (status.error || 'Unknown error').slice(0, 80))
          }
        } catch {
          clearInterval(pollRef.current)
          setScanning(false)
        }
      }, 800)
    } catch (e: any) {
      setScanning(false)
      setStatusText('Error: ' + (e.message || 'Unknown'))
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
            <div>
              <label className="text-xs text-muted block mb-1">Tag</label>
              <select
                value={tag}
                onChange={e => setTag(e.target.value)}
                className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5"
              >
                <option value="">All</option>
                {tags.map((t: any) => (
                  <option key={t.slug || t.label} value={t.slug || ''}>
                    {t.label || t.slug}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Min Volume</label>
              <input type="number" value={minVolume} onChange={e => setMinVolume(Number(e.target.value))}
                className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5" />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Min Liquidity</label>
              <input type="number" value={minLiquidity} onChange={e => setMinLiquidity(Number(e.target.value))}
                className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5" />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Min Edge</label>
              <input type="number" step={0.001} value={minEdge} onChange={e => setMinEdge(Number(e.target.value))}
                className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5" />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Max Days</label>
              <input type="number" value={maxDays} onChange={e => setMaxDays(Number(e.target.value))}
                className="w-full rounded border border-border bg-background text-foreground text-sm px-2 py-1.5" />
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div>
              <label className="text-xs text-muted block mb-1">Strategy</label>
              <select
                value={strategy}
                onChange={e => { setStrategy(e.target.value); setManualMarkets([]); setManualProbs({}) }}
                className="rounded border border-border bg-background text-foreground text-sm px-2 py-1.5"
              >
                <optgroup label="Built-in">
                  <option value="meta_consensus">Meta Consensus</option>
                  <option value="market_implied">Market Implied</option>
                  <option value="manual">Manual</option>
                  <option value="external">External</option>
                </optgroup>
                {savedStrategies.length > 0 && (
                  <optgroup label="Saved">
                    {savedStrategies.map((s: any) => (
                      <option key={`saved-${s.id}`} value={`saved:${s.id}`}>
                        {s.name} ({s.strategy_type})
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
            </div>
            {strategy !== 'manual' && !strategy.startsWith('saved:') && (
              <label className="checkbox-label">
                <input type="checkbox" className="checkbox-custom" checked={fetchBooks} onChange={e => setFetchBooks(e.target.checked)} />
                Fetch Orderbooks
              </label>
            )}
            {(strategy === 'meta_consensus' || strategy.startsWith('saved:')) && (
              <button
                onClick={() => setShowWeights(!showWeights)}
                className="flex items-center gap-1 text-xs text-muted hover:text-foreground"
              >
                <Settings2 className="w-3 h-3" />
                Weights
              </button>
            )}
            {strategy === 'manual' && (
              <Button variant="outline" size="sm" onClick={loadMarkets} disabled={loadingMarkets}>
                {loadingMarkets ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Download className="w-3.5 h-3.5 mr-1.5" />}
                {loadingMarkets ? 'Cargando...' : 'Load Markets'}
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={() => setShowLab(true)} className="text-xs">
              <FlaskConical className="w-3.5 h-3.5 mr-1" />
              Lab
            </Button>
            <Button onClick={scan} disabled={scanning} size="sm">
              {scanning ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <ScanLine className="w-4 h-4 mr-1" />}
              Scan Markets
            </Button>
          </div>

          {showWeights && (
            <div className="border-t border-border pt-3">
              <WeightSliders weights={weights} onChange={setWeights} />
            </div>
          )}

          {strategy === 'manual' && manualMarkets.length > 0 && (
            <div className="border-t border-border pt-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Manual Probabilities ({manualProbsCount} set)
                </span>
                <span className="text-xs text-muted">
                  Enter your probability (0–1) for each market
                </span>
              </div>
              <div className="max-h-96 overflow-y-auto space-y-1.5">
                {manualMarkets.map((m: any) => (
                  <div key={m.market_slug} className="flex items-center gap-2 p-1.5 rounded hover:bg-slate-50 dark:hover:bg-slate-800/50">
                    <span className="text-xs text-foreground flex-1 truncate">{m.question}</span>
                    <span className="text-[10px] text-muted w-16 text-right font-mono">
                      market: {m.last_trade_price?.toFixed(3) || '—'}
                    </span>
                    <input
                      type="text"
                      placeholder={m.last_trade_price?.toFixed(3) || '0.5'}
                      value={manualProbs[m.market_slug] || ''}
                      onChange={e => setProb(m.market_slug, e.target.value)}
                      className="w-16 rounded border border-border bg-background text-foreground text-xs px-1.5 py-1 text-center font-mono focus:outline-none focus:border-accent"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {scanning && (
            <div className="space-y-3">
              <Progress value={progress * 100} />
              <div className="flex justify-between text-xs text-muted">
                <span>{statusText}</span>
                <span>{(progress * 100).toFixed(0)}%</span>
              </div>
              <div className="flex flex-col items-center pt-2">
                <img src="/duck-analyse.gif" alt="Analizando..." className="w-20 h-20 object-contain opacity-80" />
                <p className="text-[10px] text-muted-foreground mt-1 italic">
                  No se como hacer barras de carga bien :b
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {opportunities.length > 0 && (
        <div className="space-y-3">
          <div className="text-sm text-muted">{opportunities.length} opportunities</div>
          {opportunities.map((opp: any) => (
            <div key={opp.market_slug}>
              <MarketCard
                question={opp.question}
                marketPrice={opp.market_price}
                bestAsk={opp.real_price}
                ourProb={opp.our_prob}
                edge={opp.edge}
                confidence={opp.confidence}
                volume24h={opp.volume_24h}
                daysLeft={opp.days_left}
                direction={opp.direction}
              />
              {opp.rationale && (
                <div className="ml-4">
                  <RationalePanel
                    rationale={opp.rationale}
                    strategy={opp.strategy || strategy}
                    llmReasoning={opp.llm_reasoning}
                    newsUsed={opp.news_used}
                    fundamentalShift={opp.fundamental_shift}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!scanning && opportunities.length === 0 && (
        <div className="text-center text-muted py-12">
          <ScanLine className="w-8 h-8 mx-auto mb-2 opacity-30" />
          <p className="text-sm">Configure parameters and scan for opportunities</p>
        </div>
      )}

      <LaboratoryModal
        open={showLab}
        onClose={() => { setShowLab(false); reloadStrategies() }}
      />
    </div>
  )
}
