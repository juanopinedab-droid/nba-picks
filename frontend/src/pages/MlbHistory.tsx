import { useState, useEffect } from 'react'
import { Diamond } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { api } from '@/lib/api'

export function MlbHistoryPage() {
  const [picks, setPicks] = useState<any[]>([])

  useEffect(() => {
    api.picks.mlb.history().then((d: any) => setPicks(d.picks || []))
  }, [])

  const wins = picks.filter(p => p.result === 'WIN').length
  const losses = picks.filter(p => p.result === 'LOSS').length
  const total = wins + losses
  const wr = total > 0 ? (wins / total * 100).toFixed(1) : '--'

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold flex items-center gap-2">
        <Diamond className="w-5 h-5 text-sky-500" />
        Historial MLB
      </h2>

      <div className="flex gap-4 text-sm">
        <span className="text-green-400">{wins}W</span>
        <span className="text-red-400">{losses}L</span>
        <span className="text-muted-foreground">WR: {wr}%</span>
        <span className="text-muted-foreground">Total: {picks.length} picks</span>
      </div>

      {picks.length === 0 && (
        <p className="text-sm text-muted-foreground">No hay historial MLB.</p>
      )}

      <div className="grid gap-2">
        {picks.slice(0, 30).map((p: any) => (
          <Card key={p.id}>
            <CardContent className="p-3 flex items-center justify-between">
              <div>
                <span className="font-semibold text-sm">{p.game}</span>
                <span className="text-sm text-muted-foreground ml-2">{p.selection}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">{p.odds}</span>
                <Badge variant={p.result === 'WIN' ? 'default' : p.result === 'LOSS' ? 'destructive' : 'outline'}>
                  {p.result}
                </Badge>
                <span className={`text-sm font-mono ${(p.profit_cop || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {(p.profit_cop || 0) >= 0 ? '+' : ''}{p.profit_cop?.toLocaleString() || 0} COP
                </span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
