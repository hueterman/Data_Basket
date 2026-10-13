#!/usr/bin/env bash
# ============================================================
# Equivalente Linux (Raspberry Pi) de scripts/weekly_ingest_current_seasons.bat
# -- misma lista de fuentes y mismo --no-cache (ver el .bat para el razonamiento
# completo: temporadas EN CURSO, list_games() se cachea entero, etc.).
#
# Pensado para el timer de systemd deploy/raspberry/databasket-ingest.timer,
# pero se puede ejecutar a mano:  bash scripts/weekly_ingest_current_seasons.sh
#
# Log por ejecucion en logs/ingest_current_seasons/AAAA-MM-DD_HHMM.log (se
# conservan los 12 mas recientes). last_run_summary.json lo escribe
# scripts/ingest_data.py en esa misma carpeta, igual que en Windows.
# ============================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

LOGDIR="logs/ingest_current_seasons"
mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/$(date +%Y-%m-%d_%H%M).log"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

export PYTHONUNBUFFERED=1   # el log se va escribiendo en vivo, no al final

"$PY" scripts/ingest_data.py \
  --sources acb,feb,lfendesa,lfchallenge,lf2,ligau,copaespaa,segundafeb,tercerafeb,nba,wnba \
  --no-cache > "$LOGFILE" 2>&1
EXITCODE=$?

echo "Ingesta semanal terminada (exit=$EXITCODE) -- log completo en $LOGFILE"

# Rotacion: solo los 12 logs mas recientes
ls -1t "$LOGDIR"/*.log 2>/dev/null | tail -n +13 | xargs -r rm -f

exit "$EXITCODE"
