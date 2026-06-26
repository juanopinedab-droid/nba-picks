import { useState, useEffect } from 'react'
import { Loader2, BarChart3, Landmark } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/Card'
import { api } from '@/lib/api'
import { PnLBadge } from '../components/PnLBadge'

export function HistoryPage() {
  const [loading, setLoading] = useState(true)
  const [positions, setPositions] = useState<any[]>([])
  const [pnlSummary, setPnlSummary] = useState<any>(null)
  const [txHistory, setTxHistory] = useState<any[]>([])

  useEffect(() => {
    Promise.all([
      api.pm.history(),
      api.pm.treasury.history(),
    ]).then(([posData, txData]) => {
      setPositions(posData.positions || [])
      setPnlSummary(posData.pnl_summary || {})
      setTxHistory(txData.history || [])
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted" />
      </div>
    )
  }

  const totalPnl = pnlSummary?.closed_pnl_usd || 0
  const closedCount = pnlSummary?.closed_count || 0
  const wins = positions.filter((p: any) => (p.pnl_usd || 0) > 0).length
  const losses = positions.filter((p: any) => (p.pnl_usd || 0) < 0).length
  const winRate = closedCount > 0 ? ((wins / closedCount) * 100).toFixed(0) + '%' : '--'
  const avgReturn = closedCount > 0 ? (totalPnl / closedCount).toFixed(2) : '--'

  const typeLabel = (t: string) => {
    const map: Record<string, string> = {
      INITIAL: 'Initial', DEPOSIT: 'Deposit', WITHDRAW: 'Withdraw',
      WIN: 'Win', LOSS: 'Loss', PARTIAL_CLOSE: 'Partial Close',
    }
    return map[t] || t
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Closed</div>
            <div className="text-xl font-bold text-foreground">{closedCount}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Win Rate</div>
            <div className="text-xl font-bold text-foreground">{winRate}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Wins</div>
            <div className="text-xl font-bold text-green-600">{wins}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Losses</div>
            <div className="text-xl font-bold text-red-600">{losses}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-3">
            <div className="text-xs text-muted">Total PnL</div>
            <div className="text-lg font-bold">
              <PnLBadge value={totalPnl} currency />
            </div>
          </CardContent>
        </Card>
      </div>

      {positions.length === 0 ? (
        <div className="text-center text-muted py-12">
          <BarChart3 className="w-8 h-8 mx-auto mb-2 opacity-30" />
          <p className="text-sm">No closed positions yet</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted text-xs">
                <th className="text-left py-2 px-2">Question</th>
                <th className="text-right py-2 px-2">Opened</th>
                <th className="text-right py-2 px-2">Closed</th>
                <th className="text-right py-2 px-2">Entry</th>
                <th className="text-right py-2 px-2">Exit</th>
                <th className="text-right py-2 px-2">PnL</th>
                <th className="text-right py-2 px-2">Strategy</th>
                <th className="text-right py-2 px-2">Reason</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p: any) => (
                <tr key={p.id} className="border-b border-border/50 hover:bg-muted/20">
                  <td className="py-2 px-2 max-w-xs truncate text-foreground">{p.question}</td>
                  <td className="py-2 px-2 text-right text-xs text-muted">{p.opened_at?.slice(0, 10)}</td>
                  <td className="py-2 px-2 text-right text-xs text-muted">{p.closed_at?.slice(0, 10)}</td>
                  <td className="py-2 px-2 text-right font-mono text-foreground">{p.entry_price?.toFixed(4)}</td>
                  <td className="py-2 px-2 text-right font-mono text-foreground">{p.exit_price?.toFixed(4)}</td>
                  <td className="py-2 px-2 text-right"><PnLBadge value={p.pnl_usd} pct={p.pnl_pct} currency /></td>
                  <td className="py-2 px-2 text-right text-xs text-muted">{p.strategy}</td>
                  <td className="py-2 px-2 text-right text-xs text-muted">{p.closed_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Card>
        <CardContent>
          <div className="flex items-center gap-2 mb-2">
            <Landmark className="w-4 h-4 text-accent" />
            <h3 className="text-sm font-semibold text-foreground">Transaction History</h3>
          </div>
          {txHistory.length === 0 ? (
            <p className="text-xs text-muted text-center py-4">No transactions</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-muted text-xs">
                    <th className="text-left py-1 px-2">Date</th>
                    <th className="text-left py-1 px-2">Type</th>
                    <th className="text-right py-1 px-2">Amount</th>
                    <th className="text-right py-1 px-2">Balance</th>
                    <th className="text-left py-1 px-2">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {txHistory.map((h: any) => (
                    <tr key={h.id} className="border-b border-border/50">
                      <td className="py-1 px-2 text-xs text-muted">{h.created_at?.slice(0, 16)}</td>
                      <td className="py-1 px-2">
                        <span className={`text-xs px-1 rounded ${
                          h.type === 'DEPOSIT' || h.type === 'WIN' || h.type === 'INITIAL' ? 'text-green-400 bg-green-950/50' :
                          h.type === 'WITHDRAW' || h.type === 'LOSS' ? 'text-red-400 bg-red-950/50' :
                          'text-muted bg-muted/30'
                        }`}>
                          {typeLabel(h.type)}
                        </span>
                      </td>
                      <td className="py-1 px-2 text-right font-mono text-xs">${(h.amount || 0).toFixed(2)}</td>
                      <td className="py-1 px-2 text-right font-mono text-xs text-muted">${(h.balance_after || 0).toFixed(2)}</td>
                      <td className="py-1 px-2 text-xs text-muted max-w-[150px] truncate">{h.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
