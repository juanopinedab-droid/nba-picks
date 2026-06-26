import { useState, useEffect, useRef } from 'react'
import { Loader2, SlidersHorizontal, Settings2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent } from '@/components/ui/Card'
import { Progress } from '@/components/ui/Progress'
import { Skeleton } from '@/components/ui/Skeleton'
import { api } from '@/lib/api'
import { fmt, pct, odds, cop } from '@/lib/utils'
import { useJobs, useJob } from '@/lib/JobsContext'

const FOOTBALL_JOB_TYPE = 'football'

type BetToggle = { key: string; label: string; color: string }

const BET_TOGGLES: BetToggle[] = [
  { key: 'allow_win',   label: '1X2',     color: 'bg-sky-500/10 text-sky-600 border-sky-300 dark:border-sky-800' },
  { key: 'allow_draw',  label: 'Empate',  color: 'bg-slate-500/10 text-slate-600 border-slate-300 dark:border-slate-700' },
  { key: 'allow_over',  label: 'Over',    color: 'bg-emerald-500/10 text-emerald-600 border-emerald-300 dark:border-emerald-800' },
  { key: 'allow_under', label: 'Under',   color: 'bg-rose-500/10 text-rose-600 border-rose-300 dark:border-rose-800' },
  { key: 'allow_btts',  label: 'BTTS',    color: 'bg-violet-500/10 text-violet-600 border-violet-300 dark:border-violet-800' },
]

function ToggleChip({ label, active, color, onChange, disabled }: {
  label: string; active: boolean; color: string; onChange: () => void; disabled: boolean
}) {
  return (
    <button
      onClick={onChange}
      disabled={disabled}
      className={`
        px-2.5 py-1 rounded-md border text-xs font-medium transition-all select-none
        disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer
        ${active ? color + ' shadow-sm' : 'border-border text-muted-foreground hover:border-muted'}
      `}
    >
      {label}
    </button>
  )
}

export function FootballPage() {
  const { startJob } = useJobs()
  const { job, isRunning: jobRunning, log: jobLog, progress: jobProgress, error: jobError } = useJob(FOOTBALL_JOB_TYPE)

  const [generating, setGenerating] = useState(false)
  const [picks, setPicks] = useState<any[]>([])
  const [log, setLog] = useState<string[]>([])
  const [progress, setProgress] = useState(0)
  const [timestamp, setTimestamp] = useState('')
  const [generateError, setGenerateError] = useState('')
  const [showParams, setShowParams] = useState(false)
  const pollRef = useRef<any>(null)

  const [minEdge, setMinEdge] = useState(4)
  const [toggles, setToggles] = useState<Record<string, boolean>>({
    allow_win: true,
    allow_draw: false,
    allow_over: true,
    allow_under: false,
    allow_btts: false,
  })

  const toggleKey = (key: string) => {
    if (jobRunning || generating) return
    setToggles(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const activeToggles = BET_TOGGLES.filter(t => toggles[t.key])

  const activeRunning = jobRunning || generating
  const activeLog = jobRunning ? jobLog : log
  const activeProgress = jobRunning ? jobProgress : progress
  console.log('[Football] render | activeRunning:', activeRunning, '| jobRunning:', jobRunning, '| generating:', generating, '| picks:', picks.length, '| timestamp:', timestamp)

  useEffect(() => {
    console.log('[Football] job status changed:', job?.status, 'jobId:', job?.id?.slice(0,8))
    if (job?.status === 'completed') {
      const r = job.result || {}
      console.log('[Football] job completed. result:', Object.keys(r), 'picks:', r.picks?.length)
      if (r.picks?.length > 0) {
        setPicks(r.picks || [])
        setTimestamp(new Date().toLocaleTimeString())
      }
      setGenerating(false)
    }
  }, [job?.status, job?.id])

  useEffect(() => {
    api.picks.football.status().then(d => {
      if (d.timestamp) {
        setTimestamp(d.timestamp)
        setPicks(d.picks || [])
      }
    })
    return () => clearInterval(pollRef.current)
  }, [])

  const generate = async () => {
    console.log('[Football] generate clicked')
    setGenerating(true)
    setProgress(0)
    setLog(['Iniciando...'])
    setGenerateError('')
    try {
      const params: Record<string, any> = {
        min_edge: minEdge / 100,
        allow_win: toggles.allow_win,
        allow_draw: toggles.allow_draw,
        allow_over: toggles.allow_over,
        allow_under: toggles.allow_under,
        allow_btts: toggles.allow_btts,
      }
      const res = await api.picks.football.generate(params)
      console.log('[Football] API response:', res)
      if (res.status === 'already_running') {
        setGenerating(false)
        setGenerateError('Ya hay un analisis EPL en curso. Espera a que termine.')
        return
      }
      const jobId = res.job_id
      if (!jobId) {
        pollRef.current = setInterval(async () => {
          const d = await api.picks.football.status()
          setLog(d.log || [])
          if (!d.generating) {
            clearInterval(pollRef.current)
            setGenerating(false)
            setTimestamp(d.timestamp || '')
            setPicks(d.picks || [])
          }
        }, 1500)
        return
      }
      startJob(jobId, FOOTBALL_JOB_TYPE)
      console.log('[Football] startJob called with', jobId)
    } catch (e) {
      console.error('[Football] generate error:', e)
      setGenerating(false)
      setGenerateError('Error de conexion al iniciar analisis EPL.')
    }
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <Button onClick={generate} disabled={activeRunning}>
          {activeRunning ? (
            <>
              <Loader2 className="animate-spin mr-1 w-4 h-4" /> Generando...
            </>
          ) : (
            'Generar Picks EPL'
          )}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setShowParams(!showParams)} className={showParams ? 'text-accent' : ''}>
          <SlidersHorizontal className="w-3.5 h-3.5 mr-1.5" />
          Parámetros
        </Button>
        {timestamp && <span className="text-sm text-muted">Ultima: {timestamp}</span>}
      </div>

      {showParams && (
        <Card className="mb-4">
          <CardContent className="p-4 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                <Settings2 className="w-3.5 h-3.5" />
                Tipos de apuesta
              </div>
              <span className="text-xs text-muted">
                {activeToggles.length > 0 ? activeToggles.map(t => t.label).join(' · ') : 'Ninguno seleccionado'}
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {BET_TOGGLES.map(t => (
                <ToggleChip
                  key={t.key}
                  label={t.label}
                  active={toggles[t.key]}
                  color={t.color}
                  onChange={() => toggleKey(t.key)}
                  disabled={activeRunning}
                />
              ))}
            </div>

            <div className="space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">Edge mínimo</span>
                <span className="font-mono font-medium text-foreground">{minEdge}%</span>
              </div>
              <input
                type="range"
                min="1" max="15" step="0.5"
                value={minEdge}
                onChange={e => setMinEdge(Number(e.target.value))}
                disabled={activeRunning}
                className="w-full h-1.5 accent-accent rounded-full appearance-none bg-border cursor-pointer disabled:opacity-40"
              />
              <div className="flex justify-between text-[10px] text-muted">
                <span>1%</span>
                <span>15%</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

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

      {activeRunning && picks.length === 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {[1, 2, 3].map(i => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
      )}

      {!activeRunning && picks.length === 0 && (
        <div className="text-center py-12 text-muted">
          <img src="/cat-spin.gif" alt="" className="w-48 h-48 mx-auto mb-3 object-contain" />
          <p>Sin picks EPL con edge suficiente ({minEdge}%). Ajusta los parametros.</p>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {picks.map((pick, i) => {
          const conf = pick.confidence || 'BAJA'
          const variant = conf === 'ALTA' ? 'alta' : conf === 'MEDIA' ? 'media' : 'baja'
          const edgePct = (pick.edge * 100).toFixed(1)
          const barW = Math.min(pick.edge * 600, 100)

          return (
            <Card key={i} className={conf === 'ALTA' ? 'border-l-4 border-l-green-500' : conf === 'MEDIA' ? 'border-l-4 border-l-yellow-500' : 'border-l-4 border-l-slate-400'}>
              <CardContent className="p-4">
                <div className="flex justify-between items-start mb-2">
                  <span className="text-xs text-muted truncate mr-2">{pick.game}</span>
                  <Badge variant={variant as any}>{conf}</Badge>
                </div>
                <div className="text-xs uppercase text-muted mb-1">{pick.bet_type}</div>
                <div className="flex items-center gap-2 mb-3">
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

                <div className="flex justify-between text-center text-xs">
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
                    <div className="font-semibold text-amber-600">{cop(pick.stake_cop || 0)}</div>
                  </div>
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
