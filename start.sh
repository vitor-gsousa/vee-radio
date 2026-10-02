#!/data/data/com.termux/files/usr/bin/bash
DIR="$(dirname "$(readlink -f "$0")")"

termux-wake-lock 2>/dev/null || true
pkill -f radio_web.py 2>/dev/null || true
pkill mpd 2>/dev/null || true

echo "A iniciar MPD..."
mpd
sleep 1
# Só carrega as rádios se a fila restaurada pelo MPD estiver vazia (evita duplicados)
if [ -z "$(mpc playlist 2>/dev/null)" ]; then
    mpc load radios >/dev/null 2>&1 || true
fi

echo "A iniciar servidor Web (porta 8080)..."
nohup python "$DIR/radio_web.py" > ~/web.log 2>&1 &

echo "A iniciar Cloudflare Tunnel..."
cloudflared tunnel --url http://localhost:8080
