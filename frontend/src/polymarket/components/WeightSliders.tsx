import { useState, useEffect } from 'react'

interface WeightConfig {
  momentum: number
  imbalance: number
  fundamental: number
  sentiment: number
  time_penalty: number
  spread_penalty: number
}

interface WeightSlidersProps {
  weights: WeightConfig
  onChange: (weights: WeightConfig) => void
}

const LABELS: { key: keyof WeightConfig; label: string }[] = [
  { key: 'momentum', label: 'Momentum' },
  { key: 'imbalance', label: 'Order Book Imbalance' },
  { key: 'fundamental', label: 'AI Oracle (fundamental)' },
  { key: 'sentiment', label: 'Context Sentiment' },
  { key: 'time_penalty', label: 'Time Penalty' },
  { key: 'spread_penalty', label: 'Spread Penalty' },
]

function redistribute(weights: WeightConfig, changed: keyof WeightConfig, newVal: number): WeightConfig {
  const oldVal = weights[changed]
  const diff = newVal - oldVal
  const otherKeys = LABELS.map(l => l.key).filter(k => k !== changed)
  const otherTotal = otherKeys.reduce((sum, k) => sum + weights[k], 0)

  const result = { ...weights, [changed]: newVal }

  if (otherTotal > 0 && diff !== 0) {
    for (const k of otherKeys) {
      result[k] -= (weights[k] / otherTotal) * diff
    }
  } else if (otherTotal <= 0 && diff < 0) {
    const perKey = Math.abs(diff) / otherKeys.length
    for (const k of otherKeys) {
      result[k] = perKey
    }
  }

  const total = LABELS.reduce((sum, l) => sum + result[l.key], 0)
  const scale = 1.0 / total
  for (const k of otherKeys) {
    result[k] = Math.max(0, Math.round(result[k] * scale * 1000) / 1000)
  }
  result[changed] = Math.max(0, Math.round(newVal * scale * 1000) / 1000)

  return result
}

export function WeightSliders({ weights, onChange }: WeightSlidersProps) {
  const handleChange = (key: keyof WeightConfig, value: number) => {
    const newWeights = redistribute(weights, key, value)
    onChange(newWeights)
  }

  return (
    <div className="space-y-2">
      {LABELS.map(({ key, label }) => (
        <div key={key} className="flex items-center gap-3">
          <span className="w-48 text-xs text-muted truncate">{label}</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={weights[key]}
            onChange={e => handleChange(key, parseFloat(e.target.value))}
            className="flex-1 h-1 accent-accent"
          />
          <span className="w-12 text-xs text-right font-mono text-muted">
            {(weights[key] * 100).toFixed(1)}%
          </span>
        </div>
      ))}
      <div className="text-xs text-muted text-right">
        Total: {(LABELS.reduce((s, l) => s + weights[l.key], 0) * 100).toFixed(1)}%
      </div>
    </div>
  )
}
