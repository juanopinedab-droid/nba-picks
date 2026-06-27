interface PnLBadgeProps {
  value: number
  pct?: number
  currency?: boolean
}

export function PnLBadge({ value, pct, currency }: PnLBadgeProps) {
  const positive = value >= 0
  const sign = positive ? '+' : ''
  const colorClass = positive ? 'text-green-600' : 'text-red-600'

  const formatted = currency
    ? `${sign}$${Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : `${sign}${value.toFixed(4)}`

  return (
    <span className={`font-mono text-sm font-medium ${colorClass}`}>
      {formatted}
      {pct !== undefined && (
        <span className="ml-1 text-xs opacity-70">
          ({pct >= 0 ? '+' : ''}{pct.toFixed(1)}%)
        </span>
      )}
    </span>
  )
}
