"""
dev.py — Lanza API + frontend concurrentemente en una sola terminal.

Uso:
    python cli/dev.py               → API (5000) + frontend (5173)
    python cli/dev.py --api         → solo API
    python cli/dev.py --frontend    → solo frontend
    python cli/dev.py --setup       → instalar deps y arrancar ambos
    python cli/dev.py --setup-only  → solo instalar deps
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
VENV_PYTHON = ROOT / "venv" / "bin" / "python"
FRONTEND_DIR = ROOT / "frontend"

_processes: list[subprocess.Popen] = []


def run_api():
    proc = subprocess.Popen(
        [str(VENV_PYTHON), "-m", "src.api"],
        stdout=sys.stdout,
        stderr=sys.stderr,
        cwd=str(ROOT),
    )
    _processes.append(proc)
    return proc


def run_frontend():
    proc = subprocess.Popen(
        ["npm", "run", "dev"],
        stdout=sys.stdout,
        stderr=sys.stderr,
        cwd=str(FRONTEND_DIR),
    )
    _processes.append(proc)
    return proc


def setup():
    print("\n  Instalando dependencias Python...")
    subprocess.run(
        [str(ROOT / "venv" / "bin" / "pip"), "install", "-r", "requirements.txt"],
        cwd=str(ROOT), check=False
    )
    print("  Instalando dependencias Node...")
    subprocess.run(
        ["npm", "install"],
        cwd=str(FRONTEND_DIR), check=False
    )
    print("  Listo.\n")


def kill_all():
    for proc in _processes:
        try:
            proc.terminate()
        except Exception:
            pass
    time.sleep(0.3)
    for proc in _processes:
        try:
            proc.kill()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Dev launcher")
    parser.add_argument("--api", action="store_true")
    parser.add_argument("--frontend", action="store_true")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--setup-only", action="store_true")
    args = parser.parse_args()

    def _handle_sig(sig, frame):
        print("\n  Deteniendo...")
        kill_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    if args.setup_only:
        setup()
        return

    if args.setup:
        setup()

    run_both = not args.api and not args.frontend

    if run_both or args.api:
        print("  API → http://localhost:5000")
        run_api()
    if run_both or args.frontend:
        time.sleep(1)
        print("  Frontend → http://localhost:5173")
        run_frontend()

    print("  Ctrl+C para detener.\n")
    try:
        for proc in _processes:
            proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        kill_all()


if __name__ == "__main__":
    main()
