import { Card, CardContent } from '@/components/ui/Card'
import { ExternalLink } from 'lucide-react'

const mappings = [
  {
    old: 'python cli/picks.py',
    desc: 'Generar picks NBA del dia',
    view: 'NBA',
    path: '#/picks',
    detail: 'Boton "Generar Picks NBA". Configura season, min edge y fetch props antes de generar.',
  },
  {
    old: 'python cli/picks.py --pendientes',
    desc: 'Ver picks sin resultado',
    view: 'Pendientes',
    path: '#/pending',
    detail: 'Lista de picks pendientes con boton para marcar WIN / LOSS / PUSH.',
  },
  {
    old: 'python cli/picks.py --historial',
    desc: 'Ver record y ROI',
    view: 'Historial',
    path: '#/history',
    detail: 'Record completo, ROI por tipo de apuesta y detalle de cada pick resuelto.',
  },
  {
    old: 'python cli/picks.py --resultado <ID> WIN/LOSS/PUSH',
    desc: 'Marcar resultado de un pick',
    view: 'Pendientes',
    path: '#/pending',
    detail: 'En la tabla de pendientes, boton verde/rojo/gris para WIN/LOSS/PUSH.',
  },
  {
    old: 'python cli/picks.py --resolver',
    desc: 'Resolver pendientes automaticamente (ESPN)',
    view: 'Bankroll',
    path: '#/bankroll',
    detail: 'Boton "Resolver pendientes (ESPN)". Busca scores reales y marca resultados.',
  },
  {
    old: 'python cli/picks.py --cerrar',
    desc: 'Guardar cuotas de cierre (CLV)',
    view: 'Bankroll',
    path: '#/bankroll',
    detail: 'Boton "Guardar cuotas de cierre (CLV)".',
  },
  {
    old: 'python cli/picks.py --season 2024-25',
    desc: 'Cambiar temporada NBA',
    view: 'NBA',
    path: '#/picks',
    detail: 'Dropdown de season arriba del boton generar.',
  },
  {
    old: 'python cli/picks.py --partido Lakers',
    desc: 'Filtrar picks por equipo',
    view: '—',
    path: '',
    detail: 'No disponible en frontend aun. Usa CLI.',
  },
  {
    old: 'python cli/picks_football.py',
    desc: 'Generar picks EPL del dia',
    view: 'Futbol',
    path: '#/football',
    detail: 'Boton "Generar Picks EPL". Misma mecanica que NBA.',
  },
  {
    old: 'python cli/picks_football.py --pendientes / --historial / --resultado',
    desc: 'Gestionar picks EPL',
    view: 'Pendientes / Historial',
    path: '#/pending',
    detail: 'Los picks de futbol y NBA comparten las mismas vistas de Pendientes e Historial.',
  },
  {
    old: 'python cli/picks_mlb.py',
    desc: 'Generar picks MLB del dia',
    view: 'MLB',
    path: '#/sports/mlb',
    detail: 'Boton "Generar Picks". Panel de parametros con toggles de tipos de apuesta (Over, Under, ML, RL, F5), slider de edge y max picks.',
  },
  {
    old: 'python cli/picks_mlb.py --partido Yankees',
    desc: 'Filtrar MLB por equipo',
    view: 'MLB',
    path: '#/sports/mlb',
    detail: 'No disponible en frontend aun. Usa CLI con --partido.',
  },
  {
    old: 'python cli/picks_mlb.py --pendientes / --historial / --resultado',
    desc: 'Gestionar picks MLB',
    view: 'Pendientes / Historial',
    path: '#/pending',
    detail: 'Los picks MLB comparten las vistas de Pendientes e Historial con NBA y Futbol.',
  },
  {
    old: 'python cli/backtest.py',
    desc: 'Validar modelo (grid-search k + LR)',
    view: 'Backtest',
    path: '#/backtest',
    detail: 'Boton "Backtest completo (2 seasons)". Muestra progreso real y output del modelo.',
  },
  {
    old: 'python cli/backtest.py --download',
    desc: 'Descargar datos historicos',
    view: 'Backtest',
    path: '#/backtest',
    detail: 'Boton "Solo descargar datos".',
  },
  {
    old: 'python cli/backtest.py --seasons 1',
    desc: 'Backtest con 1 temporada',
    view: 'Backtest',
    path: '#/backtest',
    detail: 'Boton "Backtest (1 season)".',
  },
  {
    old: 'python cli/calibrate.py',
    desc: 'Analizar desempeno desde historial',
    view: 'Calibracion',
    path: '#/calibrate',
    detail: 'Boton "Ejecutar calibracion". Analisis de rendimiento por confianza, tipo y edge.',
  },
  {
    old: 'python cli/calibrate.py --apply',
    desc: 'Aplicar recomendaciones al .env',
    view: 'Calibracion',
    path: '#/calibrate',
    detail: 'Checkbox "Aplicar cambios" antes de ejecutar.',
  },
  {
    old: 'Ajustar MIN_EDGE, FETCH_PROPS, BANKROLL en .env',
    desc: 'Configurar parametros del modelo',
    view: 'NBA',
    path: '#/picks',
    detail: 'Slider de MIN_EDGE, checkbox FETCH_PROPS en la vista NBA. Bankroll en icono del header.',
  },
  {
    old: 'python dashboard.py',
    desc: 'Abrir panel web antiguo (Bootstrap)',
    view: '—',
    path: '',
    detail: 'Eliminado. Usa el frontend actual (React + Vite).',
  },
]

export function HelpPage() {
  return (
    <div>
      <h2 className="text-lg font-semibold text-foreground mb-4">De CLI a Frontend — Guia de migracion</h2>
      <p className="text-sm text-muted mb-6">
        Cada comando que antes se ejecutaba en terminal ahora tiene su equivalente en una vista del frontend.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted uppercase tracking-wider border-b border-border">
              <th className="pb-2 pr-4">Comando (antes)</th>
              <th className="pb-2 px-4">Que hacia</th>
              <th className="pb-2 px-4">Vista</th>
              <th className="pb-2 pl-4">Como se hace ahora</th>
            </tr>
          </thead>
          <tbody>
            {mappings.map((m, i) => (
              <tr key={i} className="border-b border-border/50 hover:bg-slate-50 dark:hover:bg-slate-800/50">
                <td className="py-2.5 pr-4">
                  <code className="text-[11px] font-mono bg-slate-100 dark:bg-slate-700 px-1.5 py-0.5 rounded whitespace-nowrap">
                    {m.old}
                  </code>
                </td>
                <td className="py-2.5 px-4 text-muted whitespace-nowrap">{m.desc}</td>
                <td className="py-2.5 px-4">
                  {m.path ? (
                    <a href={m.path} className="text-accent hover:underline font-medium inline-flex items-center gap-1">
                      {m.view} <ExternalLink className="w-3 h-3" />
                    </a>
                  ) : (
                    <span className="text-muted">{m.view}</span>
                  )}
                </td>
                <td className="py-2.5 pl-4 text-muted">{m.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Card className="mt-6">
        <CardContent className="p-4">
          <h3 className="font-semibold text-foreground mb-2">Notas</h3>
          <ul className="text-sm text-muted list-disc pl-4 space-y-1">
            <li>El backend unificado (API REST) expone los mismos datos que generaban los scripts CLI.</li>
            <li>Los comandos CLI siguen funcionando normalmente si preferis terminal.</li>
            <li><code className="text-xs bg-slate-100 dark:bg-slate-700 px-1 rounded">make picks</code>, <code className="text-xs bg-slate-100 dark:bg-slate-700 px-1 rounded">make football</code>, <code className="text-xs bg-slate-100 dark:bg-slate-700 px-1 rounded">make mlb</code>, etc. estan disponibles en el Makefile.</li>
            <li>El progreso de backtest y calibracion ahora se muestra en tiempo real con barra de carga.</li>
            <li>El historial de bankroll (depositos, retiros, ganancias) se ve en la vista Bankroll.</li>
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
