#!/data/data/com.termux/files/usr/bin/bash
echo "A parar serviços..."
pkill -f cloudflared 2>/dev/null || true
pkill -f "http.server 8081" 2>/dev/null || true
pkill -f radio_web.py 2>/dev/null || true
pkill mpd 2>/dev/null || true
rm -f ~/tunnel-url.txt
termux-wake-unlock 2>/dev/null || true
echo "Tudo parado."
