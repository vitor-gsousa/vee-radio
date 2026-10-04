#!/data/data/com.termux/files/usr/bin/bash
# Abre as Configurações do comando no browser deste telemóvel. A chave só pode
# ser lida pelo Termux: as outras apps do telemóvel chegam a localhost:8080, mas
# sem ela não mudam nada
KEY_FILE="$HOME/.config/vee-radio/chave"

if ! curl -s -o /dev/null --max-time 3 http://127.0.0.1:8080/; then
    echo "O comando web não está a correr. Arranca-o com ~/start.sh."
    exit 1
fi
if [ ! -s "$KEY_FILE" ]; then
    echo "Ainda não há chave das configurações ($KEY_FILE). Reinicia com ~/start.sh."
    exit 1
fi
termux-open-url "http://localhost:8080/entrar?ir=config&chave=$(cat "$KEY_FILE")"
echo "Configurações abertas no browser."
