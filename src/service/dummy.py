import time

def execute(params: dict, ctx) -> dict:
    """Job dummy para testear el orchestrator con progreso real."""
    steps = int(params.get("steps", 10))
    delay = float(params.get("delay", 0.3))

    ctx.log_line(f"Iniciando job dummy con {steps} pasos...")
    for i in range(1, steps + 1):
        time.sleep(delay)
        ctx.set_progress(i / steps)
        ctx.log_line(f"Paso {i}/{steps} completado")

    ctx.log_line("Job dummy finalizado.")
    return {"steps_completed": steps, "message": "ok"}
