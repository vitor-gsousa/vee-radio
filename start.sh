#!/data/data/com.termux/files/usr/bin/bash
DIR="$(dirname "$(readlink -f "$0")")"
LINK_PORT=8081
LINK_DIR="$HOME/.vee-radio-link"

termux-wake-lock 2>/dev/null || true
pkill -f cloudflared 2>/dev/null || true
pkill -f "http.server $LINK_PORT" 2>/dev/null || true
pkill -f radio_web.py 2>/dev/null || true
pkill mpd 2>/dev/null || true
rm -f ~/tunnel-url.txt

echo "A iniciar MPD..."
mpd
sleep 1
# Só carrega as rádios se a fila restaurada pelo MPD estiver vazia (evita duplicados)
if [ -z "$(mpc playlist 2>/dev/null)" ]; then
    # O comando web passa a guardar nesta playlist o que se juntar ou remover
    mpc load radios >/dev/null 2>&1 && echo radios > ~/.config/mpd/playlist-ativa.txt
fi

echo "A iniciar servidor Web (porta 8080)..."
nohup python "$DIR/radio_web.py" > ~/web.log 2>&1 &

echo "A iniciar Cloudflare Tunnel..."
nohup cloudflared tunnel --url http://localhost:8080 > ~/tunnel.log 2>&1 &

# O link só aparece no log depois de o túnel estar criado
URL=""
for _ in $(seq 1 60); do
    URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' ~/tunnel.log 2>/dev/null | grep -v '//api\.' | head -n 1)"
    [ -n "$URL" ] && break
    sleep 1
done

if [ -z "$URL" ]; then
    echo "Não foi possível obter o link do túnel. Vê o ~/tunnel.log."
    echo "Na rede local, o comando continua em http://<IP-do-telemóvel>:8080"
    exit 1
fi
echo "$URL" > ~/tunnel-url.txt

# Página só no próprio telemóvel (127.0.0.1) para copiar ou partilhar o link
mkdir -p "$LINK_DIR"
sed "s|__URL__|$URL|g" "$DIR/link.html" > "$LINK_DIR/index.html"
nohup python -m http.server $LINK_PORT --bind 127.0.0.1 --directory "$LINK_DIR" > /dev/null 2>&1 &
sleep 1
termux-open-url "http://localhost:$LINK_PORT/" 2>/dev/null || true

echo ""
echo "Link do comando: $URL"
echo "Já podes fechar o Termux: tudo continua a correr em segundo plano."
