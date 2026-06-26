import { useState, useEffect, useRef } from 'react'
import { Loader2, Flame, Check, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent } from '@/components/ui/Card'
import { Progress } from '@/components/ui/Progress'
import { Skeleton } from '@/components/ui/Skeleton'
import { api } from '@/lib/api'
import { fmt, pct, odds, cop } from '@/lib/utils'
import { useJobs, useJob } from '@/lib/JobsContext'

const NBA_JOB_TYPE = 'nba'

function getAvailableSeasons(): string[] {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth() + 1
  const currentSeasonStart = month >= 10 ? year : year - 1
  const seasons: string[] = []
  for (let i = 0; i < 5; i++) {
    const start = currentSeasonStart - i
    seasons.push(`${start}-${(start + 1).toString().slice(2)}`)
  }
  return seasons
}

function SeasonSelect({ value, onChange, disabled }: {
  value: string
  onChange: (s: string) => void
  disabled: boolean
}) {
  const seasons = getAvailableSeasons()
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      disabled={disabled}
      className="bg-white dark:bg-slate-800 border border-border text-foreground text-sm rounded-lg px-3 py-2 focus:outline-none focus:border-accent disabled:opacity-50"
    >
      {seasons.map(s => (
        <option key={s} value={s}>{s}</option>
      ))}
    </select>
  )
}

export function PicksPage() {
  const { startJob } = useJobs()
  const { job, isRunning: jobRunning, log: jobLog, progress: jobProgress, error: jobError } = useJob(NBA_JOB_TYPE)

  const [generating, setGenerating] = useState(false)
  const [picks, setPicks] = useState<any[]>([])
  const [props, setProps] = useState<any[]>([])
  const [log, setLog] = useState<string[]>([])
  const [progress, setProgress] = useState(0)
  const [timestamp, setTimestamp] = useState('')
  const [season, setSeason] = useState('')
  const [minEdge, setMinEdge] = useState(0.04)
  const [fetchProps, setFetchProps] = useState(true)
  const [generateError, setGenerateError] = useState('')
  const pollRef = useRef<any>(null)

  const activeRunning = jobRunning || generating
  const activeLog = jobRunning ? jobLog : log
  const activeProgress = jobRunning ? jobProgress : progress
  console.log('[NBA] render | activeRunning:', activeRunning, '| jobRunning:', jobRunning, '| generating:', generating, '| picks:', picks.length, '| props:', props.length)

  useEffect(() => {
    console.log('[NBA] job status changed:', job?.status, 'jobId:', job?.id?.slice(0,8))
    if (job?.status === 'completed') {
      const r = job.result || {}
      console.log('[NBA] job completed. result:', Object.keys(r), 'picks:', r.picks?.length, 'props:', r.props?.length)
      if (r.picks?.length > 0 || r.props?.length > 0) {
        setPicks(r.picks || [])
        setProps(r.props || [])
        setTimestamp(new Date().toLocaleTimeString())
      }
      setGenerating(false)
    }
  }, [job?.status, job?.id])

  useEffect(() => {
    api.picks.status().then(d => {
      if (d.season) setSeason(d.season)
      if (d.min_edge !== undefined) setMinEdge(d.min_edge)
      if (d.fetch_props !== undefined) setFetchProps(d.fetch_props)
      if (d.timestamp) {
        setTimestamp(d.timestamp)
        setPicks(d.picks || [])
        setProps(d.props || [])
      }
    })
    return () => clearInterval(pollRef.current)
  }, [])

  const generate = async () => {
    console.log('[NBA] generate clicked')
    setGenerating(true)
    setProgress(0)
    setLog(['Iniciando...'])
    setGenerateError('')
    try {
      const res = await api.picks.generate(season || undefined, minEdge, fetchProps)
      console.log('[NBA] API response:', res)
      if (res.status === 'already_running') {
        setGenerating(false)
        setGenerateError('Ya hay un analisis NBA en curso. Espera a que termine.')
        return
      }
      const jobId = res.job_id
      if (!jobId) {
        pollRef.current = setInterval(async () => {
          const d = await api.picks.status()
          setLog(d.log || [])
          if (!d.generating) {
            clearInterval(pollRef.current)
            setGenerating(false)
            setTimestamp(d.timestamp || '')
            setPicks(d.picks || [])
            setProps(d.props || [])
          }
        }, 1500)
        return
      }
      startJob(jobId, NBA_JOB_TYPE)
      console.log('[NBA] startJob called with', jobId)
    } catch (e) {
      console.error('[NBA] generate error:', e)
      setGenerating(false)
      setGenerateError('Error de conexion al iniciar analisis NBA.')
    }
  }

  const allPicks = [...picks, ...props]

  return (
    <div>
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <Button onClick={generate} disabled={activeRunning}>
          {activeRunning ? (
            <>
              <Loader2 className="animate-spin mr-1 w-4 h-4" /> Generando...
            </>
          ) : (
            'Generar Picks'
          )}
        </Button>
        <SeasonSelect value={season} onChange={setSeason} disabled={activeRunning} />
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted whitespace-nowrap">Edge min</label>
          <input
            type="range"
            min="0"
            max="15"
            step="1"
            value={Math.round(minEdge * 100)}
            onChange={e => setMinEdge(Number(e.target.value) / 100)}
            disabled={activeRunning}
            className="w-20 accent-accent"
          />
          <span className="text-xs font-mono text-accent w-8">{(minEdge * 100).toFixed(0)}%</span>
        </div>
        <label className="checkbox-label">
          <input
            type="checkbox"
            className="checkbox-custom"
            checked={fetchProps}
            onChange={e => setFetchProps(e.target.checked)}
            disabled={activeRunning}
          />
          Props
        </label>
        {timestamp && <span className="text-sm text-muted">Ultima: {timestamp}</span>}
      </div>

      {generateError && (
        <div className="p-3 mb-3 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
          {generateError}
        </div>
      )}

      {jobError && (
        <div className="p-3 mb-3 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-lg text-sm text-amber-700 dark:text-amber-300">
          {jobError}
        </div>
      )}

      {activeLog.length > 0 && (
        <div className="bg-slate-50 dark:bg-slate-800/60 border border-border rounded-lg p-3 mb-4">
          {activeRunning && activeProgress > 0 && (
            <div className="mb-2">
              <div className="flex justify-between text-xs text-muted mb-1">
                <span>Progreso</span>
                <span>{Math.round(activeProgress * 100)}%</span>
              </div>
              <Progress value={activeProgress * 100} />
            </div>
          )}
          <pre className="text-xs text-muted max-h-32 overflow-y-auto font-mono whitespace-pre-wrap">
            {activeLog.join('\n')}
          </pre>
        </div>
      )}

      {activeRunning && allPicks.length === 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {[1, 2, 3].map(i => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
      )}

      {!activeRunning && allPicks.length === 0 && (
        <div className="text-center py-12 text-muted">
          <img src="/cat-spin.gif" alt="" className="w-48 h-48 mx-auto mb-3 object-contain" />
          <p>Sin picks con edge suficiente hoy. Genera para analizar.</p>
        </div>
      )}

      {allPicks.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {allPicks.map((pick, i) => {
            const conf = pick.confidence || 'BAJA'
            const variant = conf === 'ALTA' ? 'alta' : conf === 'MEDIA' ? 'media' : 'baja'
            const confIcon = conf === 'ALTA'
              ? <Flame className="w-3 h-3 text-orange-500" />
              : conf === 'MEDIA'
              ? <Check className="w-3 h-3 text-green-600" />
              : <AlertTriangle className="w-3 h-3 text-slate-400" />
            const edgePct = (pick.edge * 100).toFixed(1)
            const barW = Math.min(pick.edge * 600, 100)
            const reasons = (pick.reasons || []).slice(0, 3)
            const borderLeft = conf === 'ALTA' ? 'border-l-green-500' : conf === 'MEDIA' ? 'border-l-yellow-500' : 'border-l-slate-400'

            return (
              <Card key={i} className={`border-l-4 ${borderLeft}`}>
                <CardContent className="p-4">
                  <div className="flex justify-between items-start mb-2">
                    <span className="text-xs text-muted truncate mr-2">{pick.game}</span>
                    <Badge variant={variant as any} className="flex items-center gap-1">{confIcon} {conf}</Badge>
                  </div>
                  <div className="text-xs uppercase text-muted mb-1">{pick.bet_type}</div>
                  <div className="flex items-center gap-2 mb-3 flex-wrap">
                    <span className="font-semibold text-foreground">{pick.selection}</span>
                    <span className="text-sm font-bold bg-slate-100 dark:bg-slate-700 text-foreground px-2 py-0.5 rounded">{odds(pick.odds)}</span>
                  </div>

                  <div className="mb-3">
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-muted">Edge</span>
                      <span className="font-bold text-accent">{edgePct}%</span>
                    </div>
                    <Progress value={barW} />
                  </div>

                  <div className="flex justify-between text-center text-xs mb-3">
                    <div>
                      <div className="text-muted mb-0.5">NUESTRA</div>
                      <div className="font-semibold">{pct(pick.our_prob)}</div>
                    </div>
                    <div>
                      <div className="text-muted mb-0.5">CASA</div>
                      <div className="text-muted">{pct(pick.implied_prob)}</div>
                    </div>
                    <div>
                      <div className="text-muted mb-0.5">STAKE</div>
                      <div className="font-semibold text-amber-600">{pick.stake_cop ? cop(pick.stake_cop) : '—'}</div>
                    </div>
                  </div>

                  {reasons.length > 0 && (
                    <ul className="text-xs text-muted space-y-0.5">
                      {reasons.map((r: string, j: number) => (
                        <li key={j}>• {r}</li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
