#!/data/data/com.termux/files/usr/bin/bash
# Atualiza o código a partir do GitHub (só avança, nunca junta nem apaga
# alterações locais: se não der, fica a versão que já estava)
#   ~/update.sh          atualiza e, se houver novidades, reinicia (start.sh)
#   ~/update.sh --check  só diz se há novidades: sai com 0 se há (e escreve
#                        "instalar" se o install.sh mudou), 3 se não há, 1 se falhou
#   ~/update.sh --boot   no arranque do telemóvel: espera pela rede até 30 s e
#                        atualiza, sem reiniciar (o start.sh corre a seguir)
DIR="$(dirname "$(readlink -f "$0")")"
MODE="${1:-}"

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

if [ "$MODE" = "--check" ]; then
    git diff --quiet HEAD '@{u}' -- install.sh || echo "instalar"
    exit 0
fi

if ! git merge --ff-only --quiet '@{u}'; then
    echo "Não foi possível atualizar (ficheiros alterados à mão?): fica a versão atual."
    exit 1
fi
chmod +x start.sh stop.sh config.sh update.sh install.sh
echo "Atualizado: $(git log -1 --format='%h %s')"
# O install.sh instala dependências e refaz o arranque automático: só corre
# quando mudou, porque atualizar os pacotes demora
if ! git diff --quiet "$OLD" HEAD -- install.sh; then
    bash install.sh </dev/null || echo "O install.sh falhou: vê as mensagens acima."
fi

[ "$MODE" = "--boot" ] && exit 0
exec "$DIR/start.sh"
