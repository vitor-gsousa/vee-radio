#!/data/data/com.termux/files/usr/bin/bash
DIR="$(dirname "$(readlink -f "$0")")"
LINK_PORT=8081
LINK_DIR="$HOME/.vee-radio-link"
CONFIG_KEY_FILE="$HOME/.config/vee-radio/chave"
# --boot: chamado pelo Termux:Boot, quando a rede ainda pode não estar pronta
# --vigiar: o vigia que o próprio start.sh deixa em segundo plano (ver supervise)
BOOT=0
[ "${1:-}" = "--boot" ] && BOOT=1
SUPERVISOR_LOG="$HOME/vigia.log"
SUPERVISOR_INTERVAL=30

# Lê um valor do .env (KEY=valor), sem o executar como código; aceita aspas
# e terminações de linha do Windows
env_value() {
    [ -f "$DIR/.env" ] || return 0
    tr -d '\r' < "$DIR/.env" | sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" | tail -n 1 |
        sed "s/[[:space:]]*\$//; s/^[\"']//; s/[\"']\$//"
}

# Nome da app, mudado em Configurações (Geral). O radio_web.py só o aceita sem
# aspas duplas, `, $, \, |, & nem < >, por isso pode ir para o sed e para o HTML
app_name() {
    local name
    name="$(env_value APP_NAME)"
    printf '%s' "${name:-VEE Rádio}"
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
    # Os cabeçalhos vão só em ASCII: o título (o nome da app, que pode ter acentos)
    # vai codificado como no RFC 2047, que o ntfy descodifica. O texto vai pelo
    # stdin, em bytes: se não chegar em UTF-8, o ntfy mostra-o como um anexo em vez da mensagem
    local app
    app="$(app_name)"
    printf 'Link do %s: %s' "$app" "$1" | ntfy_curl "$token" -fsS --max-time 15 -o /dev/null \
        -H "Title: =?UTF-8?B?$(printf '%s' "$app" | base64 | tr -d '\n')?=" \
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
    # do túnel só tem letras, números, - e . (vem do grep), e o nome da app não tem aspas
    # duplas nem \ (o radio_web.py recusa-os), por isso vão tal e qual no JSON.
    # Em UTF-8 pelo stdin, por causa dos acentos
    printf '{"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","content":{"$schema":"http://adaptivecards.io/schemas/adaptive-card.json","type":"AdaptiveCard","version":"1.4","body":[{"type":"TextBlock","text":"%s","weight":"Bolder","size":"Medium"},{"type":"TextBlock","text":"Link do comando: %s","wrap":true}],"actions":[{"type":"Action.OpenUrl","title":"Abrir comando","url":"%s"}]}}]}' "$(app_name)" "$1" "$1" |
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

# Guarda o log anterior em .1 em vez de o apagar: depois de uma queda, o erro
# que a causou ainda lá está
rotate_log() {
    [ -f "$1" ] && mv -f "$1" "$1.1"
    return 0
}

start_mpd() {
    mpd
    # Num telemóvel lento o MPD pode demorar a aceitar ligações; sem isto, a fila
    # parecia vazia e a playlist ativa não era carregada
    for _ in $(seq 1 50); do
        mpc status >/dev/null 2>&1 && break
        sleep 0.2
    done
    # O MPD restaura a fila sozinho; se vier vazia, volta a carregar a playlist ativa
    # do comando web (evita duplicados)
    local active
    active="$(cat ~/.config/mpd/playlist-ativa.txt 2>/dev/null)"
    if [ -z "$(mpc playlist 2>/dev/null)" ]; then
        # A rádio que se estava a experimentar já não está na fila. Se ficasse marcada
        # e estivesse na playlist carregada, a página tratava-a como experiência
        rm -f ~/.config/vee-radio/a-ouvir.json
        [ -n "$active" ] && { mpc load "$active" >/dev/null 2>&1 || true; }
    fi
}

start_web() {
    rotate_log ~/web.log
    nohup python "$DIR/radio_web.py" > ~/web.log 2>&1 &
}

# Qualquer resposta HTTP serve (com o MPD em baixo o /estado dá erro, mas o
# servidor está vivo); só conta como parado quando não responde de todo
web_answers() {
    curl -s -o /dev/null --max-time 10 http://127.0.0.1:8080/estado
}

# Cria o túnel e espera pelo link, que só aparece no log depois de o túnel estar
# criado. Escreve-o em ~/tunnel-url.txt e em $URL. Sem link em 60 s desiste e
# para o cloudflared, para o vigia tentar outra vez mais tarde
start_tunnel() {
    rotate_log ~/tunnel.log
    rm -f ~/tunnel-url.txt
    nohup cloudflared tunnel --url http://localhost:8080 > ~/tunnel.log 2>&1 &
    URL=""
    for _ in $(seq 1 60); do
        URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' ~/tunnel.log 2>/dev/null | grep -v '//api\.' | head -n 1)"
        [ -n "$URL" ] && break
        sleep 1
    done
    if [ -z "$URL" ]; then
        stop_and_wait -f "cloudflared tunnel"
        return 1
    fi
    echo "$URL" > ~/tunnel-url.txt
}

# O link vai para cada destino que estiver no .env (tópico do ntfy, webhook do
# Teams). Se não houver nenhum, ou se nenhum envio correr bem, abre-se uma página
# só no próprio telemóvel (127.0.0.1) para copiar ou partilhar o link à mão
share_link() {
    local sent=0 tried=0
    if [ -n "$(env_value NTFY_TOPIC_URL)" ]; then
        tried=1
        if send_ntfy "$1"; then
            echo "Link enviado para o tópico do ntfy."
            sent=1
        else
            echo "Não foi possível enviar o link para o ntfy."
        fi
    fi
    if [ -n "$(env_value TEAMS_WEBHOOK_URL)" ]; then
        tried=1
        if send_teams "$1"; then
            echo "Link enviado para o Teams."
            sent=1
        else
            echo "Não foi possível enviar o link para o Teams."
        fi
    fi
    if [ "$sent" = 0 ]; then
        [ "$tried" = 1 ] && echo "A abrir a página de partilha."
        stop_and_wait -f "http.server $LINK_PORT"
        mkdir -p "$LINK_DIR"
        sed "s|__URL__|$1|g; s|__NOME__|$(app_name)|g" "$DIR/link.html" > "$LINK_DIR/index.html"
        # Fecha ao fim de 10 min: o link é a chave de acesso, e qualquer app do
        # telemóvel consegue abrir localhost:8081. A página já aberta continua a funcionar
        nohup timeout 600 python -m http.server $LINK_PORT --bind 127.0.0.1 --directory "$LINK_DIR" > /dev/null 2>&1 &
        sleep 1
        termux-open-url "http://localhost:$LINK_PORT/" 2>/dev/null || true
    fi
}

# O vigia. O Android pode matar processos do Termux (a partir do Android 12, o
# "phantom process killer"), e o cloudflared sai quando perde a rede. A cada 30 s
# volta a arrancar o que tiver parado. Um túnel novo tem outro link, que se envia
# outra vez. Sem internet, espera cada vez mais entre tentativas, até 10 min
supervise() {
    local tunnel=0 web_fails=0 tunnel_wait=0 tunnel_next=0 backup_tick=0
    # Lido uma vez, como no arranque: "Guardar" nas Configurações só se aplica no próximo start.sh
    tunnel_enabled && tunnel=1
    while true; do
        sleep "$SUPERVISOR_INTERVAL"
        # De hora a hora (120 voltas), a cópia de segurança diária, se ainda não houver uma de hoje
        if [ "$backup_tick" = 0 ]; then
            "$DIR/backup.sh" --se-preciso | sed "s/^/$(date '+%F %T') /"
        fi
        backup_tick=$(( (backup_tick + 1) % 120 ))
        if ! pgrep -x mpd >/dev/null; then
            echo "$(date '+%F %T') O MPD parou: a arrancar outra vez."
            start_mpd
        fi
        # Um servidor encravado também conta, ao fim de três falhas seguidas (1 min e meio)
        if pgrep -f radio_web.py >/dev/null && web_answers; then
            web_fails=0
        else
            web_fails=$((web_fails + 1))
            if ! pgrep -f radio_web.py >/dev/null || [ "$web_fails" -ge 3 ]; then
                echo "$(date '+%F %T') O comando web não responde: a arrancar outra vez."
                stop_and_wait -f radio_web.py
                start_web
                web_fails=0
            fi
        fi
        [ "$tunnel" = 1 ] || continue
        if pgrep -f "cloudflared tunnel" >/dev/null; then
            tunnel_wait=0
            continue
        fi
        [ "$(date +%s)" -ge "$tunnel_next" ] || continue
        echo "$(date '+%F %T') O túnel parou: a criar outro."
        if start_tunnel; then
            echo "$(date '+%F %T') Link novo: $URL"
            share_link "$URL"
            tunnel_wait=0
        else
            tunnel_wait=$(( tunnel_wait ? tunnel_wait * 2 : 60 ))
            [ "$tunnel_wait" -gt 600 ] && tunnel_wait=600
            tunnel_next=$(( $(date +%s) + tunnel_wait ))
            echo "$(date '+%F %T') Sem link do túnel: tenta outra vez daqui a ${tunnel_wait} s."
        fi
    done
}

if [ "${1:-}" = "--vigiar" ]; then
    supervise
    exit 0
fi

termux-wake-lock 2>/dev/null || true
# O vigia primeiro, para não voltar a arrancar o que se está a parar
stop_and_wait -f "start.sh --vigiar"
stop_and_wait -f cloudflared
stop_and_wait -f "http.server $LINK_PORT"
stop_and_wait -f radio_web.py
stop_and_wait -x mpd
rm -f ~/tunnel-url.txt

echo "A iniciar MPD..."
start_mpd

echo "A iniciar servidor Web (porta 8080)..."
start_web

rotate_log "$SUPERVISOR_LOG"
nohup "$DIR/start.sh" --vigiar > "$SUPERVISOR_LOG" 2>&1 &

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
if ! start_tunnel; then
    echo "Ainda não foi possível obter o link do túnel (vê o ~/tunnel.log)."
    echo "O vigia tenta outra vez em segundo plano e envia o link quando o tiver."
    echo "Na rede local, o comando continua em http://<IP-do-telemóvel>:8080"
    exit 1
fi
share_link "$URL"

echo ""
echo "Link do comando: $URL"
echo "Já podes fechar o Termux: tudo continua a correr em segundo plano."
