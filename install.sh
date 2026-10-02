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
    pkg update -y && pkg upgrade -y
    pkg install -y mpd mpc python git nano ffmpeg libcurl ca-certificates cloudflared

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
    chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/stop.sh"

    echo "=== 5. A configurar o MPD ==="
    mkdir -p ~/.config/mpd/playlists
    if [ -f ~/.config/mpd/mpd.conf ] && [ ! -L ~/.config/mpd/mpd.conf ]; then
        mv ~/.config/mpd/mpd.conf ~/.config/mpd/mpd.conf.bak
        echo "mpd.conf anterior guardado em ~/.config/mpd/mpd.conf.bak"
    fi
    ln -sf "$INSTALL_DIR/mpd.conf" ~/.config/mpd/mpd.conf
    # Não sobrescreve a lista se já existir (pode ter sido alterada pelo comando web)
    cp -n "$INSTALL_DIR/radios.m3u" ~/.config/mpd/playlists/radios.m3u

    echo "=== 6. A criar atalhos na pasta pessoal ==="
    ln -sf "$INSTALL_DIR/start.sh" ~/start.sh
    ln -sf "$INSTALL_DIR/stop.sh" ~/stop.sh

    echo ""
    echo "=== Instalação concluída! ==="
    echo "Para arrancar a jukebox: ~/start.sh"
}

main "$@"
