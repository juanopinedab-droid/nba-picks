import { Loader2, CheckCircle2, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { Progress } from '@/components/ui/Progress'
import { api } from '@/lib/api'
import { useJobs, useJob } from '@/lib/JobsContext'
import { Download, Play } from 'lucide-react'

const BACKTEST_JOB_TYPE = 'backtest'

export function BacktestPage() {
  const { startJob } = useJobs()
  const { job, isRunning, log, progress } = useJob(BACKTEST_JOB_TYPE)

  const status = job?.status
  const output = job?.result?.output || job?.error || ''
  const done = status === 'completed'
  const failed = status === 'failed'

  const run = async (downloadOnly = false, seasons = 2) => {
    const res = await api.backtest.run(seasons, downloadOnly)
    const jobId = res.job_id
    if (jobId) {
      startJob(jobId, BACKTEST_JOB_TYPE)
    }
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-4">
        <Button onClick={() => run(false, 2)} disabled={isRunning}>
          <Play className="w-4 h-4 mr-1" /> Backtest completo (2 seasons)
        </Button>
        <Button onClick={() => run(false, 1)} disabled={isRunning} variant="outline">
          <Play className="w-4 h-4 mr-1" /> Backtest (1 season)
        </Button>
        <Button onClick={() => run(true, 2)} disabled={isRunning} variant="outline">
          <Download className="w-4 h-4 mr-1" /> Solo descargar datos
        </Button>
      </div>

      {(isRunning || done || failed) && (
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2">
              {isRunning && (
                <>
                  <Loader2 className="animate-spin w-4 h-4" />
                  <span className="text-amber-600 text-sm font-medium">Ejecutando... (puede tomar varios minutos)</span>
                </>
              )}
              {done && <span className="text-green-600 text-sm flex items-center gap-1 font-medium"><CheckCircle2 className="w-4 h-4" /> Completado</span>}
              {failed && <span className="text-red-600 text-sm flex items-center gap-1 font-medium"><XCircle className="w-4 h-4" /> Error</span>}
            </div>
            {isRunning && progress > 0 && (
              <div className="mb-2">
                <div className="flex justify-between text-xs text-muted mb-1">
                  <span>Progreso</span>
                  <span>{Math.round(progress * 100)}%</span>
                </div>
                <Progress value={progress * 100} />
              </div>
            )}
            <pre className="bg-slate-50 dark:bg-slate-800/60 border border-border rounded-lg p-4 text-xs text-muted max-h-96 overflow-y-auto font-mono whitespace-pre-wrap">
              {output || (isRunning && log.slice(-10).join('\n')) || ''}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
