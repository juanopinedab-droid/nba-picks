# HOLA!

Tu codigo era un spaguetti cuando lo empece a reparar, lol. Buenas formulas, btw.

---

## Como agregar un nuevo deporte/modulo

Si algún día querés meter otro deporte (F1, UFC, tenis, lo que sea), seguí esto. El proyecto ya tiene 3 deportes (NBA, MLB, Futbol/EPL) y todos siguen la misma arquitectura. Copiá el pattern de MLB que es el mas completo.

### Estructura de archivos (lo que necesitas crear)

```
src/<deporte>/
├── __init__.py
├── collector_<deporte>.py    # fetch datos de APIs externas
├── analyzer_<deporte>.py     # modelo matematico, genera picks
├── odds_utils.py             # (opcional) power_devig, american_to_prob
└── line_tracker.py           # (opcional) tracking de CLV

src/service/
└── picks_<deporte>.py        # orquestador: collector → analyzer → DB

cli/
└── picks_<deporte>.py        # CLI wrapper

src/api/routes/
└── <deporte>.py              # endpoints REST: POST /run, GET /status/pending/history

frontend/src/pages/
├── <Deporte>Picks.tsx        # pagina principal de generacion
├── <Deporte>Pending.tsx      # (opcional) picks pendientes dedicados
└── <Deporte>History.tsx      # (opcional) historial dedicado
```

### Paso a paso

#### 1. Collector — traer datos

Creá `src/<deporte>/collector_<deporte>.py`. Debe tener al menos una funcion que devuelva los partidos/eventos del dia:

```python
def get_todays_games() -> list[dict]:
    """Devuelve lista de juegos con odds y metadata."""
    games = []
    # llamar a The Odds API, ESPN, u otra fuente
    # ...
    return games
```

Usa `from ..core import config` para leer API keys y settings del `.env`.

#### 2. Analyzer — el modelo

Creá `src/<deporte>/analyzer_<deporte>.py`. Debe tener al menos:

```python
class ModelParams:
    """Parametros configurables desde el frontend/CLI."""
    min_edge: float = 0.04
    max_picks: int = 5
    # ... todos los thresholds que quieras exponer

def analyze_games(games: list[dict], params: ModelParams) -> dict:
    """Ejecuta el modelo y devuelve picks."""
    return {
        "games": [...],    # juegos analizados con stats
        "picks": [...],    # picks con edge positivo
        "bankroll": 0,
    }
```

Las formulas matematicas van aca. No las toques si ya funcionan.

#### 3. Service — orquestador

Creá `src/service/picks_<deporte>.py` con esta firma exacta:

```python
def execute(params: dict, ctx) -> dict:
    """
    params: lo que manda el frontend/CLI (min_edge, allow_over, etc.)
    ctx: objeto JobContext con .log_line() y .set_progress()
    """
    ctx.log_line("Iniciando...")
    ctx.set_progress(0.0)

    # 1. Fetch datos
    games = collector.get_todays_games()
    ctx.log_line(f"{len(games)} juegos encontrados")
    ctx.set_progress(0.2)

    # 2. Construir ModelParams desde params
    model = ModelParams(
        min_edge = params.get("min_edge", 0.04),
        # ... mapear params → ModelParams
    )

    # 3. Analizar
    result = analyzer.analyze_games(games, model)

    # 4. Guardar picks en DB
    if result["picks"]:
        for pick in result["picks"]:
            database.save_pick(pick, stake_cop=...)

    ctx.set_progress(1.0)
    ctx.log_line("[OK] Completado")
    return result
```

El `ctx` es un `JobContext` que ya maneja progreso, logs y guardado automatico en la tabla `jobs`.

#### 4. API routes — exponer al frontend

Creá `src/api/routes/<deporte>.py`:

```python
from flask import Blueprint, jsonify, request
from ...core import database
from ...service.orchestrator import JobManager

bp = Blueprint("<deporte>", __name__)

@bp.route("/<deporte>/run", methods=["POST"])
def run():
    data = request.get_json() or {}
    params = {}
    for key in ("min_edge", "max_picks", "allow_over", ...):
        if key in data and data[key] is not None:
            params[key] = data[key]
    job_id = JobManager.submit("picks_<deporte>", params)
    return jsonify({"job_id": job_id, "status": "started"})

@bp.route("/<deporte>/pending")
def pending():
    return jsonify({"picks": database.get_pending()})

@bp.route("/<deporte>/history")
def history():
    return jsonify({"picks": database.get_pending_with_details()})
```

Registrá el blueprint en `src/api/__init__.py`:

```python
from .routes.<deporte> import bp as <deporte>_bp
app.register_blueprint(<deporte>_bp, url_prefix="/api")
```

Y registrá el job type en `src/service/orchestrator.py`:

```python
_JOB_REGISTRY = {
    ...
    "picks_<deporte>": "src.service.picks_<deporte>",
}
```

#### 5. Frontend — pagina React

Creá `frontend/src/pages/<Deporte>Picks.tsx`. Copiá el patron de `MlbPicks.tsx`:

```tsx
import { useJobs, useJob } from '@/lib/JobsContext'
import { api } from '@/lib/api'

const JOB_TYPE = '<deporte>'

export function <Deporte>PicksPage() {
  const { startJob } = useJobs()
  const { job, isRunning, log, progress } = useJob(JOB_TYPE)

  const generate = async () => {
    const res = await api.picks.<deporte>.generate({ min_edge: 0.04 })
    if (res.job_id) startJob(res.job_id, JOB_TYPE)
  }

  // render picks, progress bar, parametros, etc.
}
```

Agregá los endpoints en `frontend/src/lib/api.ts`:

```ts
<deporte>: {
  generate: (params?: Record<string, any>) =>
    fetchJSON(`${BASE}/<deporte>/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params || {}),
    }),
  status: () => fetchJSON(`${BASE}/<deporte>/status`),
  pending: () => fetchJSON(`${BASE}/<deporte>/pending`),
  history: () => fetchJSON(`${BASE}/<deporte>/history`),
},
```

Agregá la ruta en `frontend/src/App.tsx` y el item en `frontend/src/components/Sidebar.tsx`.

#### 6. CLI — opcional

Creá `cli/picks_<deporte>.py` con argparse. Ejemplo minimo:

```python
import argparse, requests
p = argparse.ArgumentParser()
p.add_argument("--min-edge", type=float, default=0.04)
args = p.parse_args()
r = requests.post("http://localhost:5000/api/<deporte>/run",
                  json={"min_edge": args.min_edge})
print(r.json())
```

Agregá los targets en el `Makefile`:

```makefile
<deporte>:
    ./venv/bin/python cli/picks_<deporte>.py
<deporte>-pendientes:
    curl -s http://localhost:5000/api/<deporte>/pending | python3 -m json.tool
```

### Checklist final

- [ ] Collector devuelve datos reales
- [ ] Analyzer produce picks con edge, confidence, reasons
- [ ] Service usa `ctx.log_line()` y `ctx.set_progress()` para feedback en tiempo real
- [ ] API route acepta parametros y devuelve `job_id`
- [ ] Registrar job type en `orchestrator.py`
- [ ] Registrar blueprint en `api/__init__.py`
- [ ] Los picks se guardan en `data/picks.db` con `sport="<deporte>"`
- [ ] El `useJob()` hook del frontend maneja running/completed/failed automaticamente
- [ ] Panel de parametros colapsable con toggles y sliders
- [ ] Mensaje claro cuando 0 picks (X juegos analizados, ninguno supera el umbral)
- [ ] Assets visuales (icono en sidebar, gif de carga, favicon si aplica)
- [ ] Entrada en `Help.tsx` documentando el equivalente CLI → frontend
- [ ] `make <deporte>` en el Makefile

### Notas

- **No necesitas manejar polling ni estado de jobs en el frontend** — `JobsContext` ya lo hace. Solo llamá `startJob(jobId, tipo)` y leé `useJob(tipo)`.
- **El orquestador ya maneja concurrencia** — `JobManager.submit()` crea un thread separado. No bloquea otros requests.
- **La DB es compartida** — todos los deportes guardan en `data/picks.db`. Usa `sport="<deporte>"` para filtrar.
- **El modelo matematico no se toca** si ya fue validado. Solo se reorganizan imports y se parametrizan constantes.
- **WAL mode esta activado en SQLite** — soporta lecturas concurrentes mientras se escribe. No deberias ver `database is locked`.
