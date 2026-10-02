# Vee Radio

Jukebox de rádio para um telemóvel Android antigo. Corre em [Termux](https://termux.dev) e usa o **MPD** para tocar streams de rádio, uma pequena app **Flask** como comando web e um **Cloudflare Tunnel** para aceder ao comando de qualquer lado.

## Ficheiros

| Ficheiro | Função |
| --- | --- |
| `install.sh` | Instala as dependências, obtém o código e configura tudo |
| `radio_web.py` | Comando web na porta `8080`: tocar, parar, volume, gerir a fila e as playlists |
| `mpd.conf` | Configuração do MPD (saída de áudio OpenSLES, porta `6600`) |
| `radios.m3u` | Lista inicial de rádios portuguesas |
| `start.sh` / `stop.sh` | Arrancar e parar o MPD, o servidor web e o túnel |

## Requisitos

- Telemóvel Android com o [Termux](https://f-droid.org/packages/com.termux/) instalado (a versão do F-Droid, não a da Play Store)
- Ligação à internet

## Instalação

No Termux:

```bash
curl -sL https://raw.githubusercontent.com/vitor-gsousa/vee-radio/main/install.sh | bash
```

O `install.sh`:

1. Instala os pacotes (`mpd`, `mpc`, `python`, `git`, `ffmpeg`, `cloudflared`) e as bibliotecas Python (`flask`, `python-mpd2`)
2. Pede acesso ao armazenamento. Aceita o pedido do Android quando aparecer.
3. Clona o repositório para `~/vee-radio`
4. Liga `~/.config/mpd/mpd.conf` ao `mpd.conf` do repositório e copia o `radios.m3u` para as playlists, se ainda não existir
5. Cria os atalhos `~/start.sh` e `~/stop.sh`

Pode correr-se mais vezes sem estragar nada: atualiza o código e mantém as playlists guardadas.

Variáveis opcionais:

- `VEE_RADIO_REPO`: URL do repositório a clonar
- `VEE_RADIO_DIR`: pasta de instalação (por omissão, `~/vee-radio`)

## Utilização

```bash
~/start.sh
```

Ativa o wake lock do Android, inicia o MPD, carrega a playlist `radios` se a fila estiver vazia, arranca o servidor web em segundo plano e abre o Cloudflare Tunnel. O link `https://*.trycloudflare.com` aparece no ecrã. Na rede local também podes usar `http://<IP-do-telemóvel>:8080`.

```bash
~/stop.sh
```

Para tudo e liberta o wake lock.

## Atualizar

```bash
cd ~/vee-radio && git pull && ~/start.sh
```

Alterações ao `radios.m3u` não substituem a playlist já instalada. Para repor a lista do repositório:

```bash
cp ~/vee-radio/radios.m3u ~/.config/mpd/playlists/
```

## Comando web

- **A Tocar Agora**: estação e faixa atuais, estado, parar e volume (±5%)
- **Fila Atual**: tocar ou remover estações, limpar a fila
- **Procurar Rádio**: pesquisar no [radio-browser.info](https://www.radio-browser.info) por nome, com opção de mostrar só rádios portuguesas, e juntar o resultado à fila já com o nome
- **Adicionar Novo Stream**: juntar um URL de stream à fila, com um nome opcional que passa a aparecer no botão
- **Playlists Guardadas**: carregar uma playlist ou guardar a fila atual com um nome

Os nomes das estações vêm das linhas `#EXTINF` das playlists. Os nomes dados ao adicionar um stream ficam em `~/.config/mpd/nomes.m3u`, e guardar uma playlist mantém-nos.

Logs do servidor web em `~/web.log`.

## Notas

- O link do Cloudflare muda sempre que o `start.sh` arranca.
- O comando web não tem autenticação: qualquer pessoa com o link pode controlar a rádio.
- Os scripts têm de ter finais de linha LF. O `.gitattributes` garante isso mesmo quando se edita no Windows.
