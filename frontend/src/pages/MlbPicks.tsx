import { useState } from 'react'
import { Loader2, Diamond, ChevronDown, ChevronUp, Settings2, SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent } from '@/components/ui/Card'
import { Progress } from '@/components/ui/Progress'
import { api } from '@/lib/api'
import { useJobs, useJob } from '@/lib/JobsContext'

const MLB_JOB_TYPE = 'mlb'

type BetToggle = { key: string; label: string; color: string }

const BET_TOGGLES: BetToggle[] = [
  { key: 'allow_over',    label: 'Over',  color: 'bg-emerald-500/10 text-emerald-600 border-emerald-300 dark:border-emerald-800' },
  { key: 'allow_under',   label: 'Under', color: 'bg-rose-500/10 text-rose-600 border-rose-300 dark:border-rose-800' },
  { key: 'allow_moneyline', label: 'ML',  color: 'bg-sky-500/10 text-sky-600 border-sky-300 dark:border-sky-800' },
  { key: 'allow_runline', label: 'RL',    color: 'bg-amber-500/10 text-amber-600 border-amber-300 dark:border-amber-800' },
  { key: 'allow_f5',      label: 'F5',    color: 'bg-violet-500/10 text-violet-600 border-violet-300 dark:border-violet-800' },
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

export function MlbPicksPage() {
  const { startJob } = useJobs()
  const { job, isRunning, log, progress, error } = useJob(MLB_JOB_TYPE)
  const [generateError, setGenerateError] = useState('')
  const [showGames, setShowGames] = useState(false)
  const [showParams, setShowParams] = useState(false)

  const [minEdge, setMinEdge] = useState(7)
  const [maxPicks, setMaxPicks] = useState(3)
  const [toggles, setToggles] = useState<Record<string, boolean>>({
    allow_over: true,
    allow_under: false,
    allow_moneyline: true,
    allow_runline: true,
    allow_f5: false,
  })

  const toggleKey = (key: string) => {
    if (isRunning) return
    setToggles(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const activeToggles = BET_TOGGLES.filter(t => toggles[t.key])

  const result = job?.result || {}
  const picks   = result.picks    || []
  const picksMl = result.picks_ml || []
  const picksRl = result.picks_rl || []
  const picksF5 = result.picks_f5 || []
  const games   = result.games    || []
  const allPicks = [...picks, ...picksMl, ...picksRl, ...picksF5]
  const jobCompleted = job?.status === 'completed'

  const generate = async () => {
    setGenerateError('')
    setShowGames(false)
    try {
      const params: Record<string, any> = {
        min_edge: minEdge / 100,
        allow_over: toggles.allow_over,
        allow_under: toggles.allow_under,
        allow_moneyline: toggles.allow_moneyline,
        allow_runline: toggles.allow_runline,
        allow_f5: toggles.allow_f5,
        max_picks: maxPicks,
      }
      const res = await api.picks.mlb.generate(params)
      if (res.status === 'already_running') {
        setGenerateError('Ya hay un analisis MLB en curso. Espera a que termine.')
        return
      }
      const jobId = res.job_id
      if (jobId) {
        startJob(jobId, MLB_JOB_TYPE)
      }
    } catch (e) {
      console.error('[MLB] generate error:', e)
      setGenerateError('Error de conexion al iniciar analisis MLB.')
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold flex items-center gap-2">
          <Diamond className="w-5 h-5 text-sky-500" />
          MLB Picks
        </h2>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setShowParams(!showParams)} className={showParams ? 'text-accent' : ''}>
            <SlidersHorizontal className="w-3.5 h-3.5 mr-1.5" />
            Parámetros
          </Button>
          <Button onClick={generate} disabled={isRunning}>
            {isRunning ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Diamond className="w-4 h-4 mr-2" />}
            {isRunning ? 'Analizando...' : 'Generar Picks'}
          </Button>
        </div>
      </div>

      {showParams && (
        <Card>
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
                  disabled={isRunning}
                />
              ))}
            </div>

            <div className="grid grid-cols-2 gap-4 pt-1">
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
                  disabled={isRunning}
                  className="w-full h-1.5 accent-accent rounded-full appearance-none bg-border cursor-pointer disabled:opacity-40"
                />
                <div className="flex justify-between text-[10px] text-muted">
                  <span>1%</span>
                  <span>15%</span>
                </div>
              </div>

              <div className="space-y-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-muted-foreground">Max picks</span>
                  <span className="font-mono font-medium text-foreground">{maxPicks}</span>
                </div>
                <input
                  type="range"
                  min="1" max="10" step="1"
                  value={maxPicks}
                  onChange={e => setMaxPicks(Number(e.target.value))}
                  disabled={isRunning}
                  className="w-full h-1.5 accent-accent rounded-full appearance-none bg-border cursor-pointer disabled:opacity-40"
                />
                <div className="flex justify-between text-[10px] text-muted">
                  <span>1</span>
                  <span>10</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {generateError && (
        <div className="p-3 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
          {generateError}
        </div>
      )}

      {error && (
        <div className="p-3 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-lg text-sm text-amber-700 dark:text-amber-300">
          {error}
        </div>
      )}

      {isRunning && (
        <div className="space-y-2">
          {progress > 0 && (
            <>
              <div className="flex justify-between text-xs text-muted-foreground mb-1">
                <span>Progreso</span>
                <span>{Math.round(progress * 100)}%</span>
              </div>
              <Progress value={progress * 100} />
            </>
          )}
          <div className="text-xs text-muted-foreground max-h-32 overflow-y-auto">
            {log.slice(-8).map((l, i) => <div key={i}>{l}</div>)}
          </div>
        </div>
      )}

      {isRunning && (
        <div className="flex flex-col items-center justify-center py-8">
          <img src="/duck-analyse.gif" alt="Analizando..." className="w-32 h-32 object-contain" />
          <p className="text-xs text-muted-foreground mt-3 italic">
            No se como hacer barras de carga bien :b
          </p>
        </div>
      )}

      {jobCompleted && allPicks.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">{allPicks.length} pick(s)</span>
            <span className="text-xs text-muted-foreground">de {games.length} juegos analizados</span>
          </div>
          <div className="grid gap-3">
            {allPicks.map((pick: any, i: number) => (
              <MlbPickCard key={i} pick={pick} />
            ))}
          </div>
        </div>
      )}

      {jobCompleted && allPicks.length === 0 && games.length > 0 && (
        <div className="space-y-3">
          <div className="p-4 bg-slate-50 dark:bg-slate-800/40 border border-border rounded-lg">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium">
                  {games.length} juegos analizados
                </p>
                <p className="text-xs text-muted-foreground">
                  Ninguno supera el umbral de edge ({minEdge}%). Ajusta los parametros o espera a que las cuotas se muevan.
                </p>
              </div>
              {games.length > 0 && (
                <Button variant="ghost" size="sm" onClick={() => setShowGames(!showGames)} className="text-xs">
                  {showGames ? <ChevronUp className="w-3 h-3 mr-1" /> : <ChevronDown className="w-3 h-3 mr-1" />}
                  {showGames ? 'Ocultar juegos' : 'Ver juegos'}
                </Button>
              )}
            </div>

            {showGames && (
              <div className="mt-3 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
                {games.map((g: any, i: number) => (
                  <div key={i} className="p-2 bg-white dark:bg-slate-900 rounded border border-border text-xs">
                    <div className="font-medium mb-1">
                      {g.away_abbr || g.away_team || '?'} @ {g.home_abbr || g.home_team || '?'}
                    </div>
                    <div className="text-muted-foreground space-y-0.5">
                      {g.away_pitcher?.name && (
                        <div>{g.away_pitcher.name} vs {g.home_pitcher?.name || 'TBD'}</div>
                      )}
                      {g.away_ml_odds && (
                        <div className="flex gap-3">
                          <span>ML: {g.away_ml_odds > 0 ? '+' : ''}{g.away_ml_odds} / {g.home_ml_odds}</span>
                          {g.total_line && <span>O/U: {g.total_line}</span>}
                        </div>
                      )}
                      {g.total_line && g.our_total !== undefined && g.implied_total !== undefined && (
                        <div>
                          Nuestra total: {g.our_total?.toFixed(1)} | Casa: {g.implied_total?.toFixed(1)}
                          {g.total_edge !== undefined && (
                            <span className={g.total_edge > 0 ? 'text-green-500 ml-1' : 'text-red-400 ml-1'}>
                              ({(g.total_edge * 100).toFixed(1)}%)
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {!jobCompleted && !isRunning && (
        <p className="text-sm text-muted-foreground">
          Presiona "Generar Picks" para analizar los juegos MLB de hoy.
        </p>
      )}
    </div>
  )
}

function MlbPickCard({ pick }: { pick: any }) {
  const home = pick.home_team || '?'
  const away = pick.away_team || '?'
  const direction = pick.direction || '?'
  const line = pick.line ?? '?'
  const edge = pick.edge || 0
  const prob = pick.our_prob || 0
  const oddsVal = pick.odds ?? -110
  const conf = pick.confianza || pick.confidence || 'BAJA'
  const betType = pick.bet_type || 'TOTAL'
  const homePitcher = pick.home_pitcher?.name || '?'
  const awayPitcher = pick.away_pitcher?.name || '?'

  const edgeColor = edge > 0 ? 'text-green-400' : 'text-red-400'
  const badgeColor = betType === 'ML' ? 'bg-sky-950 text-sky-300' :
                     betType === 'RL' ? 'bg-amber-950 text-amber-300' : 'bg-slate-800 text-slate-300'

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Badge className={badgeColor}>{betType}</Badge>
            <span className="font-semibold text-sm">{away} @ {home}</span>
          </div>
          <Badge variant="outline" className={edgeColor}>{edge >= 0 ? '+' : ''}{(edge * 100).toFixed(1)}% edge</Badge>
        </div>

        <div className="text-sm text-muted-foreground">
          <span className="font-mono">{direction} {line}</span>
          <span className="mx-2">·</span>
          <span>Prob: {(prob * 100).toFixed(1)}%</span>
          <span className="mx-2">·</span>
          <span>Odds: {oddsVal > 0 ? '+' : ''}{oddsVal}</span>
          <span className="mx-2">·</span>
          <span>Conf: {conf}</span>
        </div>

        <div className="text-xs text-muted-foreground mt-1">
          {awayPitcher} @ {homePitcher}
        </div>
      </CardContent>
    </Card>
  )
}
