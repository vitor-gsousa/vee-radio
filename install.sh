#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_URL="${VEE_RADIO_REPO:-https://github.com/vitor-gsousa/vee-radio.git}"
INSTALL_DIR="${VEE_RADIO_DIR:-$HOME/vee-radio}"

# Tudo dentro de main: com "curl | bash" o script é lido por inteiro antes de correr
main() {
    # Se o script for corrido a partir de um clone local, usa essa pasta
    local SCRIPT_PATH="${BASH_SOURCE[0]:-}"
    if [ -n "$SCRIPT_PATH" ] && [ -f "$(dirname "$SCRIPT_PATH")/radio_web.py" ]; then
        INSTALL_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    fi

    echo "=== 1. A atualizar repositórios e a instalar dependências ==="
    # Com "curl | bash" não há teclado para as perguntas do dpkg sobre ficheiros de
    # configuração alterados (o openssl faz uma) e a atualização falhava; fica
    # sempre a versão já instalada. O stdin vem de /dev/null pelo mesmo motivo.
    local APT_OPTS=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
    export DEBIAN_FRONTEND=noninteractive
    # Termina uma instalação que tenha ficado a meio (por exemplo, numa tentativa anterior)
    dpkg --force-confdef --force-confold --configure -a </dev/null
    # apt-get e não pkg: o "pkg update" também atualiza os pacotes, mas sem as opções acima
    apt-get update </dev/null
    apt-get "${APT_OPTS[@]}" full-upgrade </dev/null
    apt-get "${APT_OPTS[@]}" install mpd mpc python git nano ffmpeg curl libcurl ca-certificates cloudflared </dev/null

    echo "=== 2. A instalar bibliotecas Python ==="
    pip install flask python-mpd2

    echo "=== 3. A pedir acesso ao armazenamento ==="
    if [ ! -d ~/storage/shared ]; then
        termux-setup-storage
        # Espera que o utilizador aceite a permissão no Android
        for _ in $(seq 1 30); do
            [ -d ~/storage/shared ] && break
            sleep 1
        done
    fi
    mkdir -p ~/storage/shared/Music || echo "Aviso: sem acesso ao armazenamento, a pasta Music não foi criada."

    echo "=== 4. A obter o código em $INSTALL_DIR ==="
    if [ -d "$INSTALL_DIR/.git" ]; then
        git -C "$INSTALL_DIR" pull --ff-only
    elif [ -f "$INSTALL_DIR/radio_web.py" ]; then
        echo "A usar os ficheiros já existentes."
    else
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
    chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/stop.sh" "$INSTALL_DIR/config.sh"
    # Configuração local (tópico do ntfy); nunca se substitui um .env que já exista
    if [ ! -f "$INSTALL_DIR/.env" ] && [ -f "$INSTALL_DIR/.env.example" ]; then
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    fi
    echo "=== 5. A configurar o MPD ==="
    mkdir -p ~/.config/mpd/playlists
    if [ -f ~/.config/mpd/mpd.conf ] && [ ! -L ~/.config/mpd/mpd.conf ]; then
        mv ~/.config/mpd/mpd.conf ~/.config/mpd/mpd.conf.bak
        echo "mpd.conf anterior guardado em ~/.config/mpd/mpd.conf.bak"
    fi
    ln -sf "$INSTALL_DIR/mpd.conf" ~/.config/mpd/mpd.conf

    echo "=== 6. A criar atalhos na pasta pessoal ==="
    ln -sf "$INSTALL_DIR/start.sh" ~/start.sh
    ln -sf "$INSTALL_DIR/stop.sh" ~/stop.sh
    ln -sf "$INSTALL_DIR/config.sh" ~/config.sh

    echo "=== 7. A configurar o arranque automático (Termux:Boot) ==="
    mkdir -p ~/.termux/boot
    cat > ~/.termux/boot/vee-radio <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
# O start.sh espera pela internet só se o túnel estiver ligado (--boot); sem
# túnel, a rádio e o comando local arrancam logo
~/start.sh --boot > ~/boot.log 2>&1
BOOT
    chmod +x ~/.termux/boot/vee-radio

    echo ""
    echo "=== Instalação concluída! ==="
    echo "Para arrancar a jukebox: ~/start.sh"
    echo "Para arrancar sozinha quando o telemóvel liga, instala a app Termux:Boot (F-Droid) e abre-a uma vez."
    echo "Para receber o link por notificação (ntfy), define o tópico em $INSTALL_DIR/.env"
}

main "$@"
