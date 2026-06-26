import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/Button'
import { api } from '@/lib/api'
import { odds, cop } from '@/lib/utils'
import { RefreshCw } from 'lucide-react'

export function PendingPage() {
  const [pending, setPending] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    api.pending.list().then(d => {
      setPending(d)
      setLoading(false)
    })
  }

  useEffect(() => { load() }, [])

  const mark = async (id: number, result: string) => {
    await api.pending.mark(id, result)
    load()
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Refrescar
        </Button>
      </div>

      {!loading && pending.length === 0 && (
        <div className="text-center py-12 text-muted">
          <img src="/cat-spin.gif" alt="" className="w-48 h-48 mx-auto mb-3 object-contain" />
          Sin picks pendientes.
        </div>
      )}

      {pending.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted text-xs uppercase border-b border-border">
                <th className="text-left p-2 font-medium">#</th>
                <th className="text-left p-2 font-medium">Fecha</th>
                <th className="text-left p-2 font-medium">Partido</th>
                <th className="text-left p-2 font-medium">Apuesta</th>
                <th className="text-left p-2 font-medium">Cuota</th>
                <th className="text-left p-2 font-medium">Stake</th>
                <th className="text-right p-2 font-medium">Resultado</th>
              </tr>
            </thead>
            <tbody>
              {pending.map(p => (
                <tr key={p.id} className="border-b border-border">
                  <td className="p-2 text-muted">{p.id}</td>
                  <td className="p-2 text-muted">{p.date}</td>
                  <td className="p-2">{p.game}</td>
                  <td className="p-2">
                    <div className="font-medium">{p.bet_type}</div>
                    <div className="text-xs text-muted">{p.selection}</div>
                  </td>
                  <td className="p-2 font-bold">{odds(p.odds)}</td>
                  <td className="p-2 text-amber-600 font-medium">{cop(p.stake_cop)}</td>
                  <td className="p-2">
                    <div className="flex gap-1 justify-end">
                      <Button size="sm" onClick={() => mark(p.id, 'WIN')}>WIN</Button>
                      <Button size="sm" variant="destructive" onClick={() => mark(p.id, 'LOSS')}>LOSS</Button>
                      <Button size="sm" variant="outline" onClick={() => mark(p.id, 'PUSH')}>PUSH</Button>
                    </div>
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
