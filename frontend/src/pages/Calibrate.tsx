import { useState, useRef, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { api } from '@/lib/api'
import { Play, Save, CheckCircle2 } from 'lucide-react'

export function CalibratePage() {
  const [running, setRunning] = useState(false)
  const [output, setOutput] = useState('')
  const [done, setDone] = useState(false)
  const pollRef = useRef<any>(null)

  useEffect(() => {
    return () => clearInterval(pollRef.current)
  }, [])

  const run = async (apply = false) => {
    setRunning(true)
    setOutput('Iniciando...\n')
    setDone(false)
    await api.calibrate.run(apply)
    pollRef.current = setInterval(async () => {
      const d = await api.calibrate.status()
      if (d.done) {
        clearInterval(pollRef.current)
        setRunning(false)
        setDone(true)
        setOutput(d.output || '')
      }
    }, 1000)
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-4">
        <Button onClick={() => run(false)} disabled={running}>
          <Play className="w-4 h-4 mr-1" /> Analizar historial
        </Button>
        <Button onClick={() => run(true)} disabled={running} variant="outline">
          <Save className="w-4 h-4 mr-1" /> Analizar y aplicar
        </Button>
      </div>

      {(running || done) && (
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2">
              {running && (
                <>
                  <Loader2 className="animate-spin w-4 h-4" />
                  <span className="text-amber-600 text-sm font-medium">Analizando...</span>
                </>
              )}
              {done && <span className="text-green-600 text-sm flex items-center gap-1 font-medium"><CheckCircle2 className="w-4 h-4" /> Completado</span>}
            </div>
            <pre className="bg-slate-50 dark:bg-slate-800/60 border border-border rounded-lg p-4 text-xs text-muted max-h-96 overflow-y-auto font-mono whitespace-pre-wrap">
              {output || 'Sin resultados aun.'}
            </pre>
          </CardContent>
        </Card>
      )}

      {!running && !done && (
        <p className="text-muted text-sm">
          Lee el historial de picks en la base de datos y analiza que tipos de apuesta y niveles de confianza
          son rentables. Sugiere ajustes a MIN_EDGE y FETCH_PROPS en el .env.
        </p>
      )}
    </div>
  )
}
