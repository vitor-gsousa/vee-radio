#!/data/data/com.termux/files/usr/bin/bash
# Cópia de segurança das playlists e do que se escolheu à mão, num .tar.gz em
# Documents/vee-radio no armazenamento do telemóvel: sobrevive a apagar o Termux.
#   ~/backup.sh                    faz uma cópia agora
#   ~/backup.sh --se-preciso       só se a última tiver mais de um dia (o vigia
#                                  do start.sh chama-o assim, de hora a hora)
#   ~/backup.sh --repor [ficheiro] repõe uma cópia (sem ficheiro, a mais recente)
# O .env e a chave das Configurações ficam de fora: no armazenamento partilhado
# qualquer app com acesso aos ficheiros os lia, e têm o token do ntfy e o webhook
DIR="$(dirname "$(readlink -f "$0")")"
KEEP=7
if [ -d "$HOME/storage/shared" ]; then
    DEST="$HOME/storage/shared/Documents/vee-radio"
else
    DEST="$HOME/vee-radio-copias"
fi
# Caminhos a partir da pasta pessoal: as playlists, os nomes escritos à mão, a
# playlist ativa, as ouvidas recentemente, a rádio do dia das playlists diárias
# e os logótipos escolhidos
FILES=(
    .config/mpd/playlists
    .config/mpd/nomes.m3u
    .config/mpd/playlist-ativa.txt
    .config/vee-radio/historico.json
    .config/vee-radio/diaria.json
    .cache/vee-radio/logos.json
)

latest() {
    find "$DEST" -maxdepth 1 -name 'vee-radio-*.tar.gz' 2>/dev/null | sort | tail -n 1
}

make_backup() {
    local present=() f out
    for f in "${FILES[@]}"; do
        [ -e "$HOME/$f" ] && present+=("$f")
    done
    if [ "${#present[@]}" = 0 ]; then
        echo "Ainda não há playlists para guardar."
        return 0
    fi
    mkdir -p "$DEST" || return 1
    out="$DEST/vee-radio-$(date +%F-%H%M%S).tar.gz"
    # Escreve num .tmp e só depois lhe dá o nome: uma cópia a meio nunca conta como a última
    if ! tar -czf "$out.tmp" -C "$HOME" "${present[@]}"; then
        rm -f "$out.tmp"
        echo "Não foi possível fazer a cópia em $DEST."
        return 1
    fi
    mv -f "$out.tmp" "$out"
    echo "Cópia feita: $out"
}

# Fica com as KEEP mais recentes (o nome tem a data, por isso a ordem é a do sort)
prune() {
    find "$DEST" -maxdepth 1 -name 'vee-radio-*.tar.gz' | sort -r | tail -n +$((KEEP + 1)) |
        while read -r old; do rm -f "$old"; done
}

case "${1:-}" in
    --se-preciso)
        last="$(latest)"
        if [ -n "$last" ] && [ -n "$(find "$last" -mtime -1)" ]; then
            exit 0
        fi
        make_backup && prune
        ;;
    --repor)
        file="${2:-$(latest)}"
        if [ -z "$file" ] || [ ! -f "$file" ]; then
            echo "Não há nenhuma cópia para repor em $DEST."
            exit 1
        fi
        if ! tar -tzf "$file" >/dev/null 2>&1; then
            echo "O ficheiro $file não é uma cópia válida."
            exit 1
        fi
        echo "A repor $file."
        # Primeiro guarda o que está agora, para se poder voltar atrás (sem apagar
        # cópias antigas, que podiam ser a que se está a repor)
        make_backup || exit 1
        "$DIR/stop.sh"
        tar -xzf "$file" -C "$HOME" || { echo "Não foi possível repor a cópia."; exit 1; }
        # Sem o estado do MPD a fila arranca vazia, e o start.sh carrega a playlist
        # ativa reposta. Com ele, a fila antiga voltava e era gravada por cima
        rm -f "$HOME/.config/mpd/state" "$HOME/.config/vee-radio/a-ouvir.json"
        exec "$DIR/start.sh"
        ;;
    "")
        make_backup && prune
        ;;
    *)
        echo "Uso: ~/backup.sh [--se-preciso | --repor [ficheiro]]"
        exit 1
        ;;
esac
