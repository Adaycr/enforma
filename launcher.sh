#!/bin/bash
# EnForma Launcher
# - Si el servidor ya está corriendo: abre el navegador directamente
# - Si no está corriendo: lo arranca en una terminal y abre el navegador

DIR="$(cd "$(dirname "$0")" && pwd)"

if curl -s --max-time 1 http://localhost:8000/api/status > /dev/null 2>&1; then
    xdg-open http://localhost:8000
else
    if command -v gnome-terminal &>/dev/null; then
        gnome-terminal --title="EnForma" -- bash -c "cd '$DIR' && ./start.sh; echo ''; read -p 'Pulsa Enter para cerrar...'"
    elif command -v xterm &>/dev/null; then
        xterm -title "EnForma" -e bash -c "cd '$DIR' && ./start.sh; read -p 'Pulsa Enter para cerrar...'"
    else
        bash -c "cd '$DIR' && ./start.sh" &
    fi
fi
