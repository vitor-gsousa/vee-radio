#!/data/data/com.termux/files/usr/bin/bash
echo "A parar serviços..."
pkill -f cloudflared 2>/dev/null || true
pkill -f radio_web.py 2>/dev/null || true
pkill mpd 2>/dev/null || true
termux-wake-unlock 2>/dev/null || true
echo "Tudo parado."
