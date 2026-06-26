export function fmt(n: number): string {
  return Math.round(n).toLocaleString('es-CO')
}

export function pct(n: number): string {
  return (n * 100).toFixed(1) + '%'
}

export function odds(n: number): string {
  return n > 0 ? '+' + n : '' + n
}

export function cop(n: number): string {
  return n ? '$' + fmt(Math.abs(n)) + ' COP' : '—'
}

export function confidenceColor(c: string): string {
  if (c === 'ALTA') return 'text-green-400'
  if (c === 'MEDIA') return 'text-yellow-400'
  return 'text-gray-400'
}

export function confidenceBadge(c: string): string {
  if (c === 'ALTA') return 'bg-green-950 text-green-300 border-green-800'
  if (c === 'MEDIA') return 'bg-yellow-950 text-yellow-200 border-yellow-800'
  return 'bg-slate-800 text-slate-400 border-slate-700'
}

export function resultColor(r: string): string {
  if (r === 'WIN') return 'text-win'
  if (r === 'LOSS') return 'text-loss'
  return 'text-push'
}
