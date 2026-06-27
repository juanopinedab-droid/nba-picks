import { TrendingUp, TrendingDown, Minus } from 'lucide-react'

export interface MispricingPick {
  outcome: string
  market_price: number
  fair_value: number
  edge_pct: number
  action: 'BUY' | 'SELL' | 'HOLD'
  rationale: string
}

export interface MispricingReport {
  summary: string
  picks: MispricingPick[]
}

interface MispricingTableProps {
  report: MispricingReport
}

function EdgeBar({ edge, action }: { edge: number; action: string }) {
  const pct = Math.min(Math.abs(edge), 100)
  if (action === 'HOLD') {
    return (
      <div className="flex items-center gap-1">
        <div className="h-1.5 bg-slate-600/40 rounded-full" style={{ width: `${Math.max(pct, 8)}%` }} />
        <Minus className="w-3 h-3 text-slate-500" />
      </div>
    )
  }
  const isBuy = action === 'BUY'
  return (
    <div className="flex items-center gap-1">
      {isBuy ? (
        <>
          <div className="h-2 bg-green-500/60 rounded-r-full" style={{ width: `${pct}%` }} />
          <TrendingUp className="w-3 h-3 text-green-400 flex-shrink-0" />
        </>
      ) : (
        <>
          <div className="h-2 bg-red-500/60 rounded-l-full ml-auto" style={{ width: `${pct}%` }} />
          <TrendingDown className="w-3 h-3 text-red-400 flex-shrink-0" />
        </>
      )}
    </div>
  )
}

function ActionBadge({ action }: { action: string }) {
  if (action === 'BUY') return <span className="text-[10px] font-semibold text-green-400 bg-green-400/10 border border-green-400/30 rounded px-1.5 py-0.5">BUY</span>
  if (action === 'SELL') return <span className="text-[10px] font-semibold text-red-400 bg-red-400/10 border border-red-400/30 rounded px-1.5 py-0.5">SELL</span>
  return <span className="text-[10px] font-semibold text-slate-400 bg-slate-400/10 border border-slate-400/30 rounded px-1.5 py-0.5">HOLD</span>
}

export function MispricingTable({ report }: MispricingTableProps) {
  if (!report?.picks?.length) return null

  const maxAbsEdge = Math.max(...report.picks.map(p => Math.abs(p.edge_pct)), 1)

  return (
    <div className="border border-emerald-500/20 rounded-lg overflow-hidden animate-in fade-in duration-300">
      <div className="bg-emerald-500/5 border-b border-emerald-500/20 px-3 py-2">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-3.5 h-3.5 text-emerald-400" />
          <span className="text-xs font-semibold text-emerald-300">Market Mispricing Analysis</span>
        </div>
        {report.summary && (
          <p className="text-[11px] text-muted mt-1 leading-relaxed">{report.summary}</p>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border/50 text-[10px] text-slate-500 uppercase tracking-wider">
              <th className="text-left px-3 py-1.5 font-medium">Outcome</th>
              <th className="text-right px-2 py-1.5 font-medium w-14">Market</th>
              <th className="text-right px-2 py-1.5 font-medium w-14">Fair</th>
              <th className="text-right px-2 py-1.5 font-medium w-16">Edge</th>
              <th className="text-center px-2 py-1.5 font-medium w-12">Action</th>
              <th className="text-left px-3 py-1.5 font-medium">Edge</th>
            </tr>
          </thead>
          <tbody>
            {report.picks.map((pick, i) => {
              const barWidth = (Math.abs(pick.edge_pct) / maxAbsEdge) * 100
              const isBuy = pick.action === 'BUY'
              const isSell = pick.action === 'SELL'
              return (
                <tr key={i} className="border-b border-border/30 hover:bg-slate-800/20 transition-colors">
                  <td className="px-3 py-1.5 text-foreground/80 max-w-[180px] truncate" title={pick.outcome}>
                    {pick.outcome}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-400">
                    ${pick.market_price.toFixed(2)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-300">
                    ${pick.fair_value.toFixed(2)}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono font-semibold ${isBuy ? 'text-green-400' : isSell ? 'text-red-400' : 'text-slate-500'}`}>
                    {pick.edge_pct > 0 ? '+' : ''}{pick.edge_pct.toFixed(1)}%
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <ActionBadge action={pick.action} />
                  </td>
                  <td className="px-3 py-1.5">
                    <div className="flex items-center w-full">
                      {isSell ? (
                        <>
                          <div className="flex-1" />
                          <div
                            className="h-2 bg-red-500/50 rounded-l-full"
                            style={{ width: `${barWidth}%`, minWidth: isSell ? '8px' : '0' }}
                          />
                        </>
                      ) : isBuy ? (
                        <div
                          className="h-2 bg-green-500/50 rounded-r-full"
                          style={{ width: `${barWidth}%`, minWidth: '8px' }}
                        />
                      ) : (
                        <div className="h-1.5 bg-slate-600/40 rounded-full w-6" />
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
