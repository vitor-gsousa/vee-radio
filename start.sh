#!/data/data/com.termux/files/usr/bin/bash
DIR="$(dirname "$(readlink -f "$0")")"
LINK_PORT=8081
LINK_DIR="$HOME/.vee-radio-link"
CONFIG_KEY_FILE="$HOME/.config/vee-radio/chave"
# --boot: chamado pelo Termux:Boot, quando a rede ainda pode não estar pronta
BOOT=0
[ "${1:-}" = "--boot" ] && BOOT=1

# Lê um valor do .env (KEY=valor), sem o executar como código; aceita aspas
# e terminações de linha do Windows
env_value() {
    [ -f "$DIR/.env" ] || return 0
    tr -d '\r' < "$DIR/.env" | sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" | tail -n 1 |
        sed "s/[[:space:]]*\$//; s/^[\"']//; s/[\"']\$//"
}

# curl com o token do ntfy (se houver) num ficheiro de configuração lido por um
# descritor, e não na linha de comando, onde ficaria visível na lista de processos.
# O <(...) tem de estar no próprio comando: guardado numa variável já vem fechado
ntfy_curl() {
    local token="$1"
    shift
    if [ -n "$token" ]; then
        curl -K <(printf 'header = "Authorization: Bearer %s"\n' "$token") "$@"
    else
        curl "$@"
    fi
}

# Túnel ligado, a não ser que o .env diga o contrário (TUNNEL_ENABLED=0); sem a
# chave fica ligado, como antes de haver a opção
tunnel_enabled() {
    case "$(env_value TUNNEL_ENABLED | tr '[:upper:]' '[:lower:]')" in
        0|false|off|no|nao|não) return 1 ;;
    esac
    return 0
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
    # No ntfy.sh público, quem souber o tópico recebe o link e controla a rádio:
    # sem token, um nome curto (fácil de adivinhar) não é usado
    local name="${topic%/}"
    name="${name##*/}"
    case "$topic" in
        https://ntfy.sh/*|http://ntfy.sh/*)
            if [ -z "$token" ] && [ "${#name}" -lt 16 ]; then
                echo "O tópico do ntfy \"$name\" é curto demais para o ntfy.sh público (mínimo 16 caracteres)."
                echo "Escolhe um nome difícil de adivinhar no .env, por exemplo: radio-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
                return 1
            fi
            ;;
    esac
    # Os cabeçalhos vão só em ASCII. O texto vai pelo stdin, em bytes: se não
    # chegar em UTF-8, o ntfy mostra-o como um anexo em vez da mensagem
    printf 'Link do VEE Rádio: %s' "$1" | ntfy_curl "$token" -fsS --max-time 15 -o /dev/null \
        -H "Title: VEE Radio" \
        -H "Tags: radio" \
        -H "Click: $1" \
        -H "Actions: view, Abrir comando, $1" \
        --data-binary @- \
        "$topic"
}

# Envia o link para o Teams por um webhook dos Workflows (Power Automate); falha
# se não houver webhook ou se o envio não correr bem. O endereço do webhook é uma
# credencial (tem a assinatura sig=...): vai ao curl por um descritor, e não na
# linha de comando, onde ficaria visível na lista de processos
send_teams() {
    local webhook
    webhook="$(env_value TEAMS_WEBHOOK_URL)"
    [ -n "$webhook" ] || return 1
    # Vai entre aspas no ficheiro de configuração do curl: aspas, barras invertidas
    # ou espaços estragavam-no
    case "$webhook" in
        https://*) ;;
        *) echo "O webhook do Teams tem de começar por https://."; return 1 ;;
    esac
    case "$webhook" in
        *[\"\\[:space:]]*) echo "O webhook do Teams tem caracteres inválidos (aspas, \\ ou espaços)."; return 1 ;;
    esac
    # O modelo dos Workflows só aceita um Adaptive Card dentro de attachments. O link
    # do túnel só tem letras, números, - e . (vem do grep), por isso vai tal e qual no JSON.
    # Em UTF-8 pelo stdin, por causa do "á"
    printf '{"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","content":{"$schema":"http://adaptivecards.io/schemas/adaptive-card.json","type":"AdaptiveCard","version":"1.4","body":[{"type":"TextBlock","text":"VEE Rádio","weight":"Bolder","size":"Medium"},{"type":"TextBlock","text":"Link do comando: %s","wrap":true}],"actions":[{"type":"Action.OpenUrl","title":"Abrir comando","url":"%s"}]}}]}' "$1" "$1" |
        curl -K <(printf 'url = "%s"\n' "$webhook") -fsS --max-time 15 -o /dev/null \
            -H "Content-Type: application/json; charset=utf-8" \
            --data-binary @-
}

# Pede ao processo que termine e espera até 10 s que saia: o pkill não espera, e
# um MPD antigo ainda a gravar o estado ocupa a porta 6600 e o pid_file, e o novo
# não arranca. Se não sair a bem, é terminado à força
stop_and_wait() {
    pkill "$@" 2>/dev/null || return 0
    for _ in $(seq 1 50); do
        pgrep "$@" >/dev/null || return 0
        sleep 0.2
    done
    pkill -9 "$@" 2>/dev/null || true
    sleep 0.5
}

termux-wake-lock 2>/dev/null || true
stop_and_wait -f cloudflared
stop_and_wait -f "http.server $LINK_PORT"
stop_and_wait -f radio_web.py
stop_and_wait -x mpd
rm -f ~/tunnel-url.txt

echo "A iniciar MPD..."
mpd
# Num telemóvel lento o MPD pode demorar a aceitar ligações; sem isto, a fila
# parecia vazia e a playlist ativa não era carregada
for _ in $(seq 1 50); do
    mpc status >/dev/null 2>&1 && break
    sleep 0.2
done
# O MPD restaura a fila sozinho; se vier vazia, volta a carregar a playlist ativa
# do comando web (evita duplicados)
ACTIVE="$(cat ~/.config/mpd/playlist-ativa.txt 2>/dev/null)"
if [ -z "$(mpc playlist 2>/dev/null)" ]; then
    # A rádio que se estava a experimentar já não está na fila. Se ficasse marcada
    # e estivesse na playlist carregada, a página tratava-a como experiência
    rm -f ~/.config/vee-radio/a-ouvir.json
    [ -n "$ACTIVE" ] && { mpc load "$ACTIVE" >/dev/null 2>&1 || true; }
fi

echo "A iniciar servidor Web (porta 8080)..."
nohup python "$DIR/radio_web.py" > ~/web.log 2>&1 &

# Sem túnel (TUNNEL_ENABLED=0 no .env, ou desligado nas Configurações do comando):
# o comando fica só neste telemóvel e na rede local, e abre-se já no browser
if ! tunnel_enabled; then
    for _ in $(seq 1 50); do
        curl -s -o /dev/null --max-time 2 http://127.0.0.1:8080/ && break
        sleep 0.2
    done
    # Abre o comando já com a chave das Configurações (o comando web cria-a ao
    # arrancar). Ao reiniciar pelas Configurações não se abre outra vez: já está aberto
    if [ -z "${VEE_RADIO_NO_OPEN:-}" ]; then
        termux-open-url "http://localhost:8080/entrar?chave=$(cat "$CONFIG_KEY_FILE" 2>/dev/null)" 2>/dev/null || true
    fi
    echo ""
    echo "Túnel desligado (TUNNEL_ENABLED=0 no .env): sem link público."
    echo "Comando neste telemóvel: http://localhost:8080"
    echo "Na rede local: http://<IP-do-telemóvel>:8080"
    echo "Já podes fechar o Termux: tudo continua a correr em segundo plano."
    exit 0
fi

# No arranque do telemóvel a rede pode demorar; espera até 2 minutos pela
# internet antes de criar o túnel (a rádio e o comando local já estão a correr)
if [ "$BOOT" = 1 ]; then
    for _ in $(seq 1 24); do
        curl -s -o /dev/null --max-time 5 https://www.cloudflare.com && break
        sleep 5
    done
fi

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

# O link vai para cada destino que estiver no .env (tópico do ntfy, webhook do
# Teams). Se não houver nenhum, ou se nenhum envio correr bem, abre-se uma página
# só no próprio telemóvel (127.0.0.1) para copiar ou partilhar o link à mão
SENT=0
TRIED=0
if [ -n "$(env_value NTFY_TOPIC_URL)" ]; then
    TRIED=1
    if send_ntfy "$URL"; then
        echo "Link enviado para o tópico do ntfy."
        SENT=1
    else
        echo "Não foi possível enviar o link para o ntfy."
    fi
fi
if [ -n "$(env_value TEAMS_WEBHOOK_URL)" ]; then
    TRIED=1
    if send_teams "$URL"; then
        echo "Link enviado para o Teams."
        SENT=1
    else
        echo "Não foi possível enviar o link para o Teams."
    fi
fi
if [ "$SENT" = 0 ]; then
    [ "$TRIED" = 1 ] && echo "A abrir a página de partilha."
    mkdir -p "$LINK_DIR"
    sed "s|__URL__|$URL|g" "$DIR/link.html" > "$LINK_DIR/index.html"
    nohup python -m http.server $LINK_PORT --bind 127.0.0.1 --directory "$LINK_DIR" > /dev/null 2>&1 &
    sleep 1
    termux-open-url "http://localhost:$LINK_PORT/" 2>/dev/null || true
fi

echo ""
echo "Link do comando: $URL"
echo "Já podes fechar o Termux: tudo continua a correr em segundo plano."
