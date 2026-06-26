import { TrendingUp, TrendingDown } from 'lucide-react'
import { PnLBadge } from './PnLBadge'

interface MarketCardProps {
  question: string
  marketPrice: number
  bestAsk: number
  ourProb: number
  edge: number
  confidence: string
  volume24h: number
  daysLeft: number
  direction: string
  onOpenPosition?: () => void
}

function confidenceStyle(c: string) {
  if (c === 'HIGH') return 'bg-green-950 text-green-300 border-green-800'
  if (c === 'MEDIUM') return 'bg-yellow-950 text-yellow-200 border-yellow-800'
  return 'bg-slate-800 text-slate-400 border-slate-700'
}

export function MarketCard({
  question, marketPrice, bestAsk, ourProb, edge,
  confidence, volume24h, daysLeft, direction, onOpenPosition,
}: MarketCardProps) {
  return (
    <div className="bg-card border border-border rounded-lg p-3 hover:border-accent/30 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            {direction === 'UP' ? (
              <TrendingUp className="w-4 h-4 text-green-500 shrink-0" />
            ) : (
              <TrendingDown className="w-4 h-4 text-red-500 shrink-0" />
            )}
            <span className="text-sm font-medium text-foreground line-clamp-2">{question}</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 hidden sm:inline ${confidenceStyle(confidence)}`}>
              {confidence}
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-3 gap-y-1 text-xs">
            <div>
              <span className="text-muted">Market</span>
              <div className="font-mono text-foreground">{marketPrice.toFixed(4)}</div>
            </div>
            <div>
              <span className="text-muted">Best Ask</span>
              <div className="font-mono text-foreground">{bestAsk.toFixed(4)}</div>
            </div>
            <div>
              <span className="text-muted">Our Prob</span>
              <div className="font-mono text-foreground font-medium">{ourProb.toFixed(4)}</div>
            </div>
            <div>
              <span className="text-muted">Edge</span>
              <div><PnLBadge value={edge} /></div>
            </div>
          </div>

          <div className="flex items-center gap-3 mt-1 sm:hidden text-[10px] text-muted">
            <span className={`px-1.5 py-0.5 rounded border ${confidenceStyle(confidence)}`}>
              {confidence}
            </span>
            <span>${(volume24h / 1000).toFixed(0)}k vol</span>
            <span>{daysLeft}d left</span>
          </div>
        </div>

        <div className="hidden sm:flex flex-col items-end gap-1.5 shrink-0">
          <span className="text-[10px] text-muted">
            ${(volume24h / 1000).toFixed(0)}k vol
          </span>
          <span className="text-[10px] text-muted">
            {daysLeft}d left
          </span>
          {onOpenPosition && edge > 0 && (
            <button
              onClick={onOpenPosition}
              className="text-[10px] px-2 py-0.5 rounded bg-accent text-white hover:bg-accent-dark transition-colors mt-1"
            >
              Open
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
