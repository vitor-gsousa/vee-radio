#!/data/data/com.termux/files/usr/bin/bash
DIR="$(dirname "$(readlink -f "$0")")"
LINK_PORT=8081
LINK_DIR="$HOME/.vee-radio-link"

# Lê um valor do .env (KEY=valor), sem o executar como código; aceita aspas
# e terminações de linha do Windows
env_value() {
    [ -f "$DIR/.env" ] || return 0
    tr -d '\r' < "$DIR/.env" | sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" | tail -n 1 |
        sed "s/[[:space:]]*\$//; s/^[\"']//; s/[\"']\$//"
}

# Envia o link para o tópico do ntfy; falha se não houver tópico ou se o envio não correr bem
send_ntfy() {
    local topic token
    topic="$(env_value NTFY_TOPIC_URL)"
    token="$(env_value NTFY_TOKEN)"
    [ -n "$topic" ] || return 1
    # Só o nome do tópico: usa o servidor público ntfy.sh
    case "$topic" in
        http://*|https://*) ;;
        *) topic="https://ntfy.sh/$topic" ;;
    esac
    local auth=()
    [ -n "$token" ] && auth=(-H "Authorization: Bearer $token")
    # Os cabeçalhos vão só em ASCII. O texto vai pelo stdin, em bytes: se não
    # chegar em UTF-8, o ntfy mostra-o como um anexo em vez da mensagem
    printf 'Link do comando da rádio: %s' "$1" | curl -fsS --max-time 15 -o /dev/null "${auth[@]}" \
        -H "Title: Vee Radio" \
        -H "Tags: radio" \
        -H "Click: $1" \
        -H "Actions: view, Abrir comando, $1" \
        --data-binary @- \
        "$topic"
}

termux-wake-lock 2>/dev/null || true
pkill -f cloudflared 2>/dev/null || true
pkill -f "http.server $LINK_PORT" 2>/dev/null || true
pkill -f radio_web.py 2>/dev/null || true
pkill mpd 2>/dev/null || true
rm -f ~/tunnel-url.txt

echo "A iniciar MPD..."
mpd
sleep 1
# O MPD restaura a fila sozinho; se vier vazia, volta a carregar a playlist ativa
# do comando web (evita duplicados)
ACTIVE="$(cat ~/.config/mpd/playlist-ativa.txt 2>/dev/null)"
if [ -n "$ACTIVE" ] && [ -z "$(mpc playlist 2>/dev/null)" ]; then
    mpc load "$ACTIVE" >/dev/null 2>&1 || true
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

# Com um tópico do ntfy no .env, o link chega por notificação; sem ele (ou se o
# envio falhar), abre-se uma página só no próprio telemóvel (127.0.0.1) para
# copiar ou partilhar o link à mão
if [ -n "$(env_value NTFY_TOPIC_URL)" ] && send_ntfy "$URL"; then
    echo "Link enviado para o tópico do ntfy."
else
    [ -n "$(env_value NTFY_TOPIC_URL)" ] && echo "Não foi possível enviar o link para o ntfy; a abrir a página de partilha."
    mkdir -p "$LINK_DIR"
    sed "s|__URL__|$URL|g" "$DIR/link.html" > "$LINK_DIR/index.html"
    nohup python -m http.server $LINK_PORT --bind 127.0.0.1 --directory "$LINK_DIR" > /dev/null 2>&1 &
    sleep 1
    termux-open-url "http://localhost:$LINK_PORT/" 2>/dev/null || true
fi

echo ""
echo "Link do comando: $URL"
echo "Já podes fechar o Termux: tudo continua a correr em segundo plano."
