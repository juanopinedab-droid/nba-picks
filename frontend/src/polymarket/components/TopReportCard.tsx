interface TopReportCardProps {
  topic: string
  preview: string
  round: number
  onClick: () => void
}

export function TopReportCard({ topic, preview, round, onClick }: TopReportCardProps) {
  return (
    <div
      onClick={onClick}
      className="bg-card border border-border rounded-lg p-3 hover:border-accent/40 cursor-pointer transition-colors"
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-[10px] text-accent font-mono">R{round}</span>
        <span className="text-xs font-medium text-foreground line-clamp-1">{topic}</span>
      </div>
      <p className="text-[11px] text-muted line-clamp-3 leading-relaxed">{preview}</p>
    </div>
  )
}
