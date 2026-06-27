.PHONY: picks football mlb mlb-manana mlb-pendientes mlb-historial mlb-resultado pendientes historial resultado backtest backtest-dl calibrate calibrate-apply resolver cerrar api frontend dev dev-setup install migrate clean help polymarket-setup polymarket-scan

PYTHON = ./venv/bin/python
PIP = ./venv/bin/pip

# ─── NBA ──────────────────────────────────────────────────────────────────────

picks:
	$(PYTHON) cli/picks.py

pendientes:
	$(PYTHON) cli/picks.py --pendientes

historial:
	$(PYTHON) cli/picks.py --historial

resultado:
	@[ "$(ID)" ] && [ "$(RESULT)" ] || (echo "Uso: make resultado ID=<id> RESULT=WIN|LOSS|PUSH" && exit 1)
	$(PYTHON) cli/picks.py --resultado $(ID) $(RESULT)

resolver:
	$(PYTHON) cli/picks.py --resolver

cerrar:
	$(PYTHON) cli/picks.py --cerrar

# ─── FOOTBALL ─────────────────────────────────────────────────────────────────

football:
	$(PYTHON) cli/picks_football.py

# ─── MLB ───────────────────────────────────────────────────────────────────────

mlb:
	$(PYTHON) cli/picks_mlb.py

mlb-manana:
	$(PYTHON) cli/picks_mlb.py --manana

mlb-pendientes:
	$(PYTHON) cli/picks_mlb.py --pendientes

mlb-historial:
	$(PYTHON) cli/picks_mlb.py --historial

mlb-resultado:
	@[ "$(ID)" ] && [ "$(RESULT)" ] || (echo "Uso: make mlb-resultado ID=<id> RESULT=WIN|LOSS|PUSH" && exit 1)
	$(PYTHON) cli/picks_mlb.py --resultado $(ID) $(RESULT)

# ─── CALIBRACIÓN ──────────────────────────────────────────────────────────────

backtest:
	$(PYTHON) cli/backtest.py

backtest-dl:
	$(PYTHON) cli/backtest.py --download

calibrate:
	$(PYTHON) cli/calibrate.py

calibrate-apply:
	$(PYTHON) cli/calibrate.py --apply

# ─── POLYMARKET ────────────────────────────────────────────────────────────────

polymarket-setup:
	$(PYTHON) -c "from src.polymarket.migrations import migrate; migrate(); print('Polymarket DB inicializada')"

polymarket-scan:
	$(PYTHON) -c "from src.service.orchestrator import JobManager; import time; jid = JobManager.submit('picks_polymarket', {'limit': 10, 'fetch_orderbooks': False, 'min_edge': 0.0}); print(f'Job {jid} iniciado'); time.sleep(5); s = JobManager.get_status(jid); result = s.get('result') or {}; print(f'Status: {s[\"status\"]}, Opportunities: {len(result.get(\"opportunities\", []))}')"

# ─── INFRA ──────────────────────────────────────────────────────────────────────

migrate:
	$(PYTHON) -m src.core.migrate

clean:
	rm -rf data/picks.db data/backtest.db picks_history.db* backtest_data.db settings.json football_cache.json src/web/
	echo "Archivos de datos y legado eliminados."

# ─── SERVIDORES ────────────────────────────────────────────────────────────────

api:
	$(PYTHON) -m src.api

frontend:
	cd frontend && npm run dev

dev:
	$(PYTHON) cli/dev.py

dev-setup:
	$(PYTHON) cli/dev.py --setup

# ─── SETUP ────────────────────────────────────────────────────────────────────

install:
	$(PIP) install -r requirements.txt
	cd frontend && npm install

help:
	@echo "NBA Picks Bot — comandos disponibles:"
	@echo ""
	@echo "  make picks           Generar picks NBA del día"
	@echo "  make pendientes      Ver picks sin resultado"
	@echo "  make historial       Ver record y ROI"
	@echo "  make resultado       Marcar resultado (ID=<id> RESULT=WIN|LOSS|PUSH)"
	@echo "  make resolver        Resolver pendientes automáticamente (ESPN)"
	@echo "  make cerrar          Guardar cuotas de cierre (CLV)"
	@echo ""
	@echo "  make football        Generar picks EPL del día"
	@echo ""
	@echo "  make backtest        Validar modelo (grid-search k + LR)"
	@echo "  make backtest-dl     Descargar datos históricos"
	@echo "  make calibrate       Analizar desempeño desde historial"
	@echo "  make calibrate-apply Aplicar recomendaciones al .env"
	@echo ""
	@echo "  make polymarket-setup       Inicializar base de datos Polymarket (data/polymarket.db)"
	@echo "  make polymarket-scan        Ejecutar escaneo rapido de mercados Polymarket"
	@echo ""
	@echo "  make api             Lanzar API REST (localhost:5000)"
	@echo "  make frontend        Lanzar frontend Vite (localhost:5173)"
	@echo "  make dev             API + frontend juntos en una terminal"
	@echo "  make dev-setup       Instalar deps y arrancar ambos"
	@echo "  make install         Instalar dependencias Python + Node"
	@echo ""
	@echo "  make migrate         Migrar datos a SQLite unificado (data/picks.db)"
	@echo "  make clean           Eliminar datos locales y archivos legacy"
