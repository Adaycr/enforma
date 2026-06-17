#!/bin/bash
# Sync matutino de EnForma — se ejecuta cada mañana via cron
# Solo sincroniza si la app está corriendo

LOGFILE="$HOME/Escritorio/enforma/data/sync_morning.log"
ENDPOINT="http://localhost:8000/api/sync"

echo "$(date '+%Y-%m-%d %H:%M') — Iniciando sync matutino" >> "$LOGFILE"

# Comprobar que la app está levantada
if ! curl -sf http://localhost:8000/api/status > /dev/null 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M') — App no está corriendo, saltando sync" >> "$LOGFILE"
    exit 0
fi

RESULT=$(curl -sf -X POST "$ENDPOINT" 2>&1)
if [ $? -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M') — OK: $RESULT" >> "$LOGFILE"
else
    echo "$(date '+%Y-%m-%d %H:%M') — ERROR: $RESULT" >> "$LOGFILE"
fi
