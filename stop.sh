#!/data/data/com.termux/files/usr/bin/bash
echo "A parar serviços..."
pkill -f cloudflared 2>/dev/null || true
pkill -f "http.server 8081" 2>/dev/null || true
pkill -f radio_web.py 2>/dev/null || true
pkill -x mpd 2>/dev/null || true
# Espera que o MPD grave o estado (a fila) antes de largar o wake lock
for _ in $(seq 1 50); do
    pgrep -x mpd >/dev/null || break
    sleep 0.2
done
rm -f ~/tunnel-url.txt
termux-wake-unlock 2>/dev/null || true
echo "Tudo parado."
