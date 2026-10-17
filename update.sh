#!/data/data/com.termux/files/usr/bin/bash
# Atualiza o código a partir do GitHub (só avança, nunca junta nem apaga
# alterações locais: se não der, fica a versão que já estava)
#   ~/update.sh          atualiza e, se houver novidades, reinicia (start.sh)
#   ~/update.sh --check  só diz se há novidades: sai com 0 se há (e escreve
#                        "instalar" se o install.sh mudou), 3 se não há, 1 se falhou
#   ~/update.sh --boot   no arranque do telemóvel: espera pela rede até 30 s e
#                        atualiza, sem reiniciar (o start.sh corre a seguir)
# Uma versão nova com erros (não compila, ou o comando web não responde depois de
# reiniciar) é revertida, e fica anotada para não se voltar a instalar
DIR="$(dirname "$(readlink -f "$0")")"
MODE="${1:-}"
FAILED_FILE="$HOME/.config/vee-radio/versao-falhada"

# Importa o comando web e compila os templates, sem arrancar nada: apanha erros de
# sintaxe, imports em falta e templates partidos antes de parar a música
code_ok() {
    for f in start.sh stop.sh config.sh update.sh install.sh; do
        bash -n "$f" || return 1
    done
    python - <<'EOF' >/dev/null
import radio_web
env = radio_web.app.jinja_env
for name in env.list_templates():
    env.get_template(name)
EOF
}

web_ok() {
    for _ in $(seq 1 30); do
        curl -fs -o /dev/null --max-time 3 http://127.0.0.1:8080/estado && return 0
        sleep 1
    done
    return 1
}

# Volta à versão de antes (--keep não toca em alterações locais) e anota a que falhou
roll_back() {
    mkdir -p "$(dirname "$FAILED_FILE")"
    echo "$NEW" > "$FAILED_FILE"
    if ! git reset --keep --quiet "$OLD"; then
        echo "$1, e não foi possível voltar à versão anterior: atualiza à mão no Termux."
        return 1
    fi
    chmod +x start.sh stop.sh config.sh update.sh install.sh
    echo "$1: voltou-se à versão $(git log -1 --format='%h')."
}

cd "$DIR" || exit 1
if [ ! -d .git ]; then
    echo "A pasta $DIR não tem .git: atualiza à mão (vê o README)."
    exit 1
fi

if [ "$MODE" = "--boot" ]; then
    for _ in $(seq 1 6); do
        curl -s -o /dev/null --max-time 5 https://github.com && break
        sleep 5
    done
fi

if ! git fetch --quiet; then
    echo "Sem ligação ao GitHub: fica a versão atual."
    exit 1
fi
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse '@{u}' 2>/dev/null)" || { echo "O ramo atual não segue nenhum ramo do GitHub."; exit 1; }
if [ "$OLD" = "$NEW" ]; then
    echo "Já está na versão mais recente."
    exit 3
fi
if ! git merge-base --is-ancestor HEAD '@{u}'; then
    echo "Há commits locais que não estão no GitHub: atualiza à mão no Termux."
    exit 1
fi
if [ "$NEW" = "$(cat "$FAILED_FILE" 2>/dev/null)" ]; then
    echo "A versão $(git rev-parse --short "$NEW") não funcionou e foi revertida: fica a atual até haver outra."
    exit 1
fi

if [ "$MODE" = "--check" ]; then
    git diff --quiet HEAD '@{u}' -- install.sh || echo "instalar"
    exit 0
fi

if ! git merge --ff-only --quiet '@{u}'; then
    echo "Não foi possível atualizar (ficheiros alterados à mão?): fica a versão atual."
    exit 1
fi
chmod +x start.sh stop.sh config.sh update.sh install.sh
if ! code_ok; then
    roll_back "A versão nova tem erros"
    exit 1
fi
echo "Atualizado: $(git log -1 --format='%h %s')"
# O install.sh instala dependências e refaz o arranque automático: só corre
# quando mudou, porque atualizar os pacotes demora
if ! git diff --quiet "$OLD" HEAD -- install.sh; then
    bash install.sh </dev/null || echo "O install.sh falhou: vê as mensagens acima."
fi

[ "$MODE" = "--boot" ] && exit 0
"$DIR/start.sh"
web_ok && exit 0
roll_back "O comando web não arrancou na versão nova" || exit 1
"$DIR/start.sh"
if ! web_ok; then
    # Também não arranca na versão anterior: a culpa não é da atualização
    rm -f "$FAILED_FILE"
    echo "O comando web também não arranca na versão anterior: vê o ~/web.log."
fi
exit 1
