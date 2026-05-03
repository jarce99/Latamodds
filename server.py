"""
LATAMODDS — Feed server
Corre el ingestor al arrancar y cada hora vía APScheduler.
Sirve latamodds_feed.json en GET /feed.json para que Vercel lo proxee.
"""
import os
import time
import logging
from flask import Flask, jsonify, send_file, abort
from apscheduler.schedulers.background import BackgroundScheduler
import latamodds_ingest as ingest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
FEED_FILE = "latamodds_feed.json"
START_TS  = time.time()

# ─── INGEST JOB ───────────────────────────────────────────────────────────────

def run_ingest():
    log.info("▶ Iniciando ingestor...")
    try:
        ingest.run()
        log.info("✓ Ingestor completado.")
    except Exception as exc:
        log.error("✗ Error en ingestor: %s", exc, exc_info=True)

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/feed.json")
def feed():
    if not os.path.exists(FEED_FILE):
        abort(503, description="Feed aún no generado — espera 1-2 minutos")
    resp = send_file(FEED_FILE, mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/health")
def health():
    return jsonify({
        "status":      "ok",
        "feed_exists": os.path.exists(FEED_FILE),
        "uptime_s":    int(time.time() - START_TS),
    })

@app.route("/")
def index():
    return jsonify({
        "service":  "LATAMODDS Feed API",
        "feed":     "/feed.json",
        "health":   "/health",
        "interval": "cada 1 hora",
    })

# ─── SCHEDULER ────────────────────────────────────────────────────────────────
# Se inicializa al importar el módulo (gunicorn --workers 1).
# Con workers > 1 cada worker lanzaría su propio scheduler — por eso usamos 1.

def _start_scheduler():
    run_ingest()                          # Correr inmediatamente al arrancar
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(run_ingest, "interval", hours=1, id="ingest",
                  misfire_grace_time=300)
    sched.start()
    log.info("Scheduler activo — próxima ejecución en 1 hora.")
    return sched

_scheduler = _start_scheduler()

# ─── MAIN (dev local) ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
