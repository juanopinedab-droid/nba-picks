import { useState, useEffect, useCallback } from 'react'
import { Loader2, TrendingUp, TrendingDown, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { api } from '@/lib/api'
import { PnLBadge } from '../components/PnLBadge'

interface PortfolioProps {
  onUpdateHeader?: () => void
}

export function PortfolioPage({ onUpdateHeader }: PortfolioProps) {
  const [loading, setLoading] = useState(true)
  const [summary, setSummary] = useState<any>(null)
  const [closing, setClosing] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api.pm.portfolio.get()
      .then(d => setSummary(d))
      .catch(() => setSummary(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const handleClose = async (positionId: number) => {
    setClosing(positionId)
    try {
      await api.pm.portfolio.close(positionId, 'manual')
      load()
      onUpdateHeader?.()
    } catch { /* ignore */ }
    setClosing(null)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted" />
      </div>
    )
  }

  if (!summary) {
    return <div className="text-center text-muted py-12">Failed to load portfolio</div>
  }

  const { positions = [], open_count, total_cost_usd, current_value_usd, unrealized_pnl_usd, unrealized_pnl_pct } = summary

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Open Positions</div>
            <div className="text-xl font-bold text-foreground">{open_count}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Invested</div>
            <div className="text-lg font-bold text-foreground font-mono">
              ${total_cost_usd.toLocaleString('en-US', { minimumFractionDigits: 2 })}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Current Value</div>
            <div className="text-lg font-bold text-foreground font-mono">
              ${current_value_usd.toLocaleString('en-US', { minimumFractionDigits: 2 })}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Unrealized PnL</div>
            <div className="text-lg font-bold">
              <PnLBadge value={unrealized_pnl_usd} pct={unrealized_pnl_pct} currency />
            </div>
          </CardContent>
        </Card>
      </div>

      {positions.length === 0 ? (
        <div className="text-center text-muted py-12">
          <TrendingUp className="w-8 h-8 mx-auto mb-2 opacity-30" />
          <p className="text-sm">No open positions</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted text-xs">
                <th className="text-left py-2 px-2">Question</th>
                <th className="text-right py-2 px-2">Shares</th>
                <th className="text-right py-2 px-2">Entry</th>
                <th className="text-right py-2 px-2">Current</th>
                <th className="text-right py-2 px-2">Cost</th>
                <th className="text-right py-2 px-2">PnL</th>
                <th className="text-right py-2 px-2"></th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p: any) => (
                <tr key={p.id} className="border-b border-border/50 hover:bg-muted/20">
                  <td className="py-2 px-2 max-w-xs truncate text-foreground">{p.question}</td>
                  <td className="py-2 px-2 text-right font-mono text-muted">{p.shares.toFixed(0)}</td>
                  <td className="py-2 px-2 text-right font-mono text-foreground">{p.entry_price.toFixed(4)}</td>
                  <td className="py-2 px-2 text-right font-mono text-foreground">{p.current_price.toFixed(4)}</td>
                  <td className="py-2 px-2 text-right font-mono text-muted">${p.cost_usd.toFixed(2)}</td>
                  <td className="py-2 px-2 text-right">
                    <PnLBadge value={p.pnl_usd} pct={p.pnl_pct} currency />
                  </td>
                  <td className="py-2 px-2 text-right">
                    <button
                      onClick={() => handleClose(p.id)}
                      disabled={closing === p.id}
                      className="text-xs px-2 py-0.5 rounded border border-red-800 text-red-400 hover:bg-red-950 transition-colors"
                    >
                      {closing === p.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <X className="w-3 h-3" />}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
