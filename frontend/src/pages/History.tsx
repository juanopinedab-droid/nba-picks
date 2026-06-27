import { useState, useEffect } from 'react'
import { Card, CardContent } from '@/components/ui/Card'
import { api } from '@/lib/api'
import { fmt, odds, cop } from '@/lib/utils'

export function HistoryPage() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.history().then(d => {
      setData(d)
      setLoading(false)
    })
  }, [])

  if (loading) return <div className="text-center py-12 text-muted">Cargando...</div>
  if (!data) return null

  const { wins, losses, pushes, profit, bankroll, roi_summary, history } = data
  const total = wins + losses
  const winPct = total ? ((wins / total) * 100).toFixed(1) + '%' : '—'
  const profColor = profit >= 0 ? 'text-green-600' : 'text-red-600'
  const profSign = profit >= 0 ? '+' : ''

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-xl font-bold text-foreground">{wins}W - {losses}L</div>
            <div className="text-xs text-muted mt-0.5">Record</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-xl font-bold text-foreground">{winPct}</div>
            <div className="text-xs text-muted mt-0.5">Win Rate</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className={`text-xl font-bold ${profColor}`}>
              {profSign}${fmt(Math.abs(profit))}
            </div>
            <div className="text-xs text-muted mt-0.5">Profit COP</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-xl font-bold text-foreground">${fmt(bankroll)}</div>
            <div className="text-xs text-muted mt-0.5">Bankroll</div>
          </CardContent>
        </Card>
      </div>

      {roi_summary && roi_summary.length > 0 && (
        <div className="mb-4">
          <h3 className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">ROI por Tipo</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-xs uppercase border-b border-border">
                  <th className="text-left p-2 font-medium">Tipo</th>
                  <th className="text-center p-2 font-medium">W</th>
                  <th className="text-center p-2 font-medium">L</th>
                  <th className="text-right p-2 font-medium">Apostado</th>
                  <th className="text-right p-2 font-medium">Profit</th>
                  <th className="text-right p-2 font-medium">ROI</th>
                </tr>
              </thead>
              <tbody>
                {roi_summary.map((s: any, i: number) => {
                  const roi = (s.roi >= 0 ? '+' : '') + s.roi.toFixed(1) + '%'
                  const roc = s.roi >= 0 ? 'text-green-600' : 'text-red-600'
                  const pc = s.profit >= 0 ? 'text-green-600' : 'text-red-600'
                  const ps = s.profit >= 0 ? '+' : ''
                  return (
                    <tr key={i} className="border-b border-border">
                      <td className="p-2">{s.tipo}</td>
                      <td className="p-2 text-center">{s.wins}</td>
                      <td className="p-2 text-center">{s.total - s.wins}</td>
                      <td className="p-2 text-right">${fmt(s.wagered)}</td>
                      <td className={`p-2 text-right font-medium ${pc}`}>{ps}${fmt(Math.abs(s.profit))}</td>
                      <td className={`p-2 text-right font-semibold ${roc}`}>{roi}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <h3 className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">Ultimas 100 apuestas</h3>
      {history.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted text-xs uppercase border-b border-border">
                <th className="text-left p-2 font-medium">Fecha</th>
                <th className="text-left p-2 font-medium">Partido</th>
                <th className="text-left p-2 font-medium">Tipo</th>
                <th className="text-left p-2 font-medium">Apuesta</th>
                <th className="text-left p-2 font-medium">Cuota</th>
                <th className="text-center p-2 font-medium">Resultado</th>
                <th className="text-right p-2 font-medium">Profit</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h: any) => {
                const pc = h.profit_cop >= 0 ? 'text-green-600' : 'text-red-600'
                const ps = h.profit_cop >= 0 ? '+' : ''
                const rc = h.result === 'WIN' ? 'text-win font-semibold' : h.result === 'LOSS' ? 'text-loss font-semibold' : 'text-push font-semibold'
                return (
                  <tr key={h.id} className="border-b border-border">
                    <td className="p-2 text-muted">{h.date}</td>
                    <td className="p-2">{h.game}</td>
                    <td className="p-2 text-muted">{h.bet_type}</td>
                    <td className="p-2">{h.selection}</td>
                    <td className="p-2 font-bold">{odds(h.odds)}</td>
                    <td className={`p-2 text-center ${rc}`}>{h.result}</td>
                    <td className={`p-2 text-right font-medium ${pc}`}>{ps}${fmt(Math.abs(h.profit_cop || 0))}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-center py-8 text-muted">
          <img src="/cat-spin.gif" alt="" className="w-48 h-48 mx-auto mb-3 object-contain" />
          Sin historial todavia.
        </div>
      )}
    </div>
  )
}
