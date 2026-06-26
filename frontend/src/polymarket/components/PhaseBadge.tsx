export type Phase = 'idle' | 'planning' | 'researching' | 'synthesizing' | 'complete' | 'error'

const phases: Record<Phase, { label: string; color: string }> = {
  idle:         { label: 'Idle',         color: 'bg-slate-800 text-slate-400 border-slate-700' },
  planning:     { label: 'Planning',     color: 'bg-amber-950 text-amber-300 border-amber-800' },
  researching:  { label: 'Researching',  color: 'bg-blue-950 text-blue-300 border-blue-800' },
  synthesizing: { label: 'Synthesizing', color: 'bg-purple-950 text-purple-300 border-purple-800' },
  complete:     { label: 'Complete',     color: 'bg-green-950 text-green-300 border-green-800' },
  error:        { label: 'Error',        color: 'bg-red-950 text-red-300 border-red-800' },
}

interface PhaseBadgeProps {
  phase: Phase
}

export function PhaseBadge({ phase }: PhaseBadgeProps) {
  const { label, color } = phases[phase] || phases.idle
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded border font-medium ${color}`}>
      {label}
    </span>
  )
}
