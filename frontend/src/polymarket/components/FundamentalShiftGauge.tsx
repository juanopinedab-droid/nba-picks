import { useEffect, useState } from 'react'

interface FundamentalShiftGaugeProps {
  shift: number
}

function interpolateColor(t: number): string {
  if (t <= 0.5) {
    const s = t / 0.5
    const r = Math.round(239 + (107 - 239) * s)
    const g = Math.round(68 + (114 - 68) * s)
    const b = Math.round(68 + (128 - 68) * s)
    return `rgb(${r},${g},${b})`
  }
  const s = (t - 0.5) / 0.5
  const r = Math.round(107 + (34 - 107) * s)
  const g = Math.round(114 + (197 - 114) * s)
  const b = Math.round(128 + (94 - 128) * s)
  return `rgb(${r},${g},${b})`
}

function labelForShift(shift: number): string {
  if (shift >= 0.1) return 'BULLISH (STRONG)'
  if (shift >= 0.04) return 'BULLISH (MODERATE)'
  if (shift > 0) return 'SLIGHTLY BULLISH'
  if (shift >= -0.04) return 'NEUTRAL'
  if (shift > -0.1) return 'SLIGHTLY BEARISH'
  if (shift > -0.2) return 'BEARISH (MODERATE)'
  return 'BEARISH (STRONG)'
}

export function FundamentalShiftGauge({ shift }: FundamentalShiftGaugeProps) {
  const [offset, setOffset] = useState(502.654) // circumference, starts empty
  const radius = 80
  const circumference = 2 * Math.PI * radius
  const normalized = (Math.max(-0.20, Math.min(0.20, shift)) + 0.20) / 0.40
  const targetOffset = circumference * (1 - normalized)
  const color = interpolateColor(normalized)

  useEffect(() => {
    const timer = setTimeout(() => setOffset(targetOffset), 50)
    return () => clearTimeout(timer)
  }, [targetOffset])

  return (
    <div className="flex flex-col items-center select-none">
      <svg width="160" height="140" viewBox="0 0 200 180" className="drop-shadow-sm">
        <circle
          cx="100"
          cy="100"
          r={radius}
          fill="none"
          stroke="currentColor"
          className="text-slate-500/15"
          strokeWidth="8"
          transform="rotate(-90 100 100)"
        />

        <circle
          cx="100"
          cy="100"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 100 100)"
          style={{ transition: 'stroke-dashoffset 800ms cubic-bezier(0.4, 0, 0.2, 1)' }}
        />

        <text
          x="100"
          y="102"
          textAnchor="middle"
          className="fill-foreground font-mono font-bold"
          fontSize="24"
        >
          {shift >= 0 ? '+' : ''}{shift.toFixed(2)}
        </text>
      </svg>

      <span className="text-[10px] font-medium text-muted tracking-widest uppercase mt-1">
        {labelForShift(shift)}
      </span>
    </div>
  )
}
