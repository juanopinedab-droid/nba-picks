import { useState, useEffect, useRef } from 'react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { api } from '@/lib/api'
import { fmt } from '@/lib/utils'
import { Target, Zap, History } from 'lucide-react'

export function BankrollPage() {
  const [data, setData] = useState<any>(null)
  const [resolveData, setResolveData] = useState<any>(null)
  const [resolving, setResolving] = useState(false)
  const [history, setHistory] = useState<any[]>([])
  const pollRef = useRef<any>(null)

  const loadData = () => {
    api.bankroll.get().then(setData)
    api.resolve.status().then(d => {
      if (d.summary && Object.keys(d.summary).length > 0) setResolveData(d)
    })
    api.bankroll.history(20).then(setHistory)
  }

  useEffect(() => {
    loadData()
    return () => clearInterval(pollRef.current)
  }, [])

  const resolve = async () => {
    setResolving(true)
    await api.resolve.run()
    pollRef.current = setInterval(async () => {
      const d = await api.resolve.status()
      if (!d.running) {
        clearInterval(pollRef.current)
        setResolving(false)
        setResolveData(d)
        loadData()
      }
    }, 1500)
  }

  const saveClosing = async () => {
    await api.close.run()
  }

  const txLabels: Record<string, { label: string; color: string }> = {
    DEPOSIT:  { label: 'Deposito',  color: 'text-emerald-600' },
    WITHDRAW: { label: 'Retiro',    color: 'text-rose-600' },
    WIN:      { label: 'Ganancia',  color: 'text-green-600' },
    LOSS:     { label: 'Perdida',   color: 'text-red-600' },
    PUSH:     { label: 'Push',      color: 'text-slate-400' },
    INITIAL:  { label: 'Inicial',   color: 'text-muted' },
  }

  return (
    <div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-6">
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-xs text-muted mb-1 uppercase tracking-wider">Inicial</div>
            <div className="text-2xl font-bold text-foreground">${data ? fmt(data.initial) : '—'}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-xs text-muted mb-1 uppercase tracking-wider">Actual</div>
            <div className={`text-2xl font-bold ${data && data.profit >= 0 ? 'text-green-600' : 'text-red-600'}`}>
              ${data ? fmt(data.current) : '—'}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-xs text-muted mb-1 uppercase tracking-wider">ROI</div>
            <div className={`text-2xl font-bold ${data && data.roi >= 0 ? 'text-green-600' : 'text-red-600'}`}>
              {data ? (data.roi >= 0 ? '+' : '') + data.roi.toFixed(1) + '%' : '—'}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        <Button onClick={resolve} disabled={resolving}>
          <Target className="w-4 h-4 mr-1" />
          {resolving ? 'Resolviendo...' : 'Resolver pendientes (ESPN)'}
        </Button>
        <Button variant="outline" onClick={saveClosing}>
          <Zap className="w-4 h-4 mr-1" /> Guardar cuotas de cierre (CLV)
        </Button>
      </div>

      {resolveData && resolveData.summary && (
        <Card>
          <CardContent className="p-4">
            <h3 className="font-semibold text-foreground mb-2">Ultima resolucion</h3>
            <div className="grid grid-cols-5 gap-2 text-center text-sm">
              {Object.entries(resolveData.summary).map(([k, v]) => (
                <div key={k} className="bg-slate-50 dark:bg-slate-800/60 rounded-lg p-2">
                  <div className="text-xl font-bold">{v as number}</div>
                  <div className="text-xs text-muted">{k}</div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {history.length > 0 && (
        <Card className="mt-6">
          <CardContent className="p-4">
            <h3 className="font-semibold text-foreground mb-3 flex items-center gap-2">
              <History className="w-4 h-4" /> Historial de movimientos
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted uppercase tracking-wider border-b border-border">
                    <th className="pb-2">Fecha</th>
                    <th className="pb-2">Tipo</th>
                    <th className="pb-2 text-right">Monto</th>
                    <th className="pb-2 pl-2">Nota</th>
                  </tr>
                </thead>
                <tbody>
                  {history.slice(0, 20).map((tx: any) => {
                    const meta = txLabels[tx.type] || { label: tx.type, color: 'text-muted' }
                    return (
                      <tr key={tx.id} className="border-b border-border/50">
                        <td className="py-2 text-muted text-xs">{tx.created_at?.slice(0, 19)?.replace('T', ' ') || ''}</td>
                        <td className={`py-2 text-xs font-medium ${meta.color}`}>{meta.label}</td>
                        <td className={`py-2 text-right font-mono text-xs ${tx.amount >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {tx.amount >= 0 ? '+' : ''}{fmt(Math.abs(tx.amount))} COP
                        </td>
                        <td className="py-2 pl-2 text-xs text-muted truncate max-w-[150px]">{tx.note || (tx.pick_id ? `Pick #${tx.pick_id}` : '')}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
