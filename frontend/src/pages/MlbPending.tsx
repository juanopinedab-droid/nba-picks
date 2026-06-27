import { useState, useEffect } from 'react'
import { Diamond } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent } from '@/components/ui/Card'
import { api } from '@/lib/api'

export function MlbPendingPage() {
  const [picks, setPicks] = useState<any[]>([])

  useEffect(() => {
    api.picks.mlb.pending().then((d: any) => setPicks(d.picks || []))
  }, [])

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold flex items-center gap-2">
        <Diamond className="w-5 h-5 text-sky-500" />
        MLB Pendientes
      </h2>

      {picks.length === 0 && (
        <p className="text-sm text-muted-foreground">No hay picks MLB pendientes.</p>
      )}

      <div className="grid gap-3">
        {picks.map((p: any) => (
          <Card key={p.id}>
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-semibold text-sm">{p.game}</span>
                  <span className="text-sm text-muted-foreground ml-2">{p.selection}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">{p.confidence}</span>
                  <Badge variant="outline">{p.odds}</Badge>
                </div>
              </div>
              <div className="text-xs text-muted-foreground mt-1">
                Stake: {p.stake_cop?.toLocaleString() || 0} COP
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
