# Vee Radio

Jukebox de rádio para um telemóvel Android antigo. Corre em [Termux](https://termux.dev) e usa o **MPD** para tocar streams de rádio, uma pequena app **Flask** como comando web e um **Cloudflare Tunnel** para aceder ao comando de qualquer lado.

## Ficheiros

| Ficheiro | Função |
| --- | --- |
| `install.sh` | Instala as dependências, obtém o código e configura tudo |
| `radio_web.py` | Comando web na porta `8080`: tocar, parar, volume, gerir as estações e as playlists |
| `templates/` | Páginas do comando web; no telemóvel ficam numa coluna e no PC em duas |
| `mpd.conf` | Configuração do MPD (saída de áudio OpenSLES, porta `6600`) |
| `start.sh` / `stop.sh` | Arrancar e parar o MPD, o servidor web e o túnel |
| `link.html` | Página que mostra o link do túnel no telemóvel, com botões para copiar e partilhar |

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
4. Liga `~/.config/mpd/mpd.conf` ao `mpd.conf` do repositório
5. Cria os atalhos `~/start.sh` e `~/stop.sh`
6. Cria o script de arranque automático para o Termux:Boot

Pode correr-se mais vezes sem estragar nada: atualiza o código e mantém as playlists guardadas.

Variáveis opcionais:

- `VEE_RADIO_REPO`: URL do repositório a clonar
- `VEE_RADIO_DIR`: pasta de instalação (por omissão, `~/vee-radio`)

## Utilização

```bash
~/start.sh
```

Ativa o wake lock do Android, inicia o MPD, volta a carregar a última playlist ativa se a fila estiver vazia, e arranca o servidor web e o Cloudflare Tunnel em segundo plano. Depois disso já podes fechar o Termux.

Quando o túnel fica pronto, o browser do telemóvel abre uma página com o link `https://*.trycloudflare.com` e os botões **Copiar link** e **Partilhar…**, para o enviares por mensagem a quem quiseres. Essa página está em `http://localhost:8081` e só se abre no próprio telemóvel. O link também aparece no Termux, fica em `~/tunnel-url.txt` e é mostrado no fundo do comando web. Na rede local também podes usar `http://<IP-do-telemóvel>:8080`.

O link muda sempre que o `start.sh` corre.

```bash
~/stop.sh
```

Para tudo e liberta o wake lock.

### Arranque automático

Para a rádio arrancar sozinha sempre que o telemóvel liga:

1. Instala a app [Termux:Boot](https://f-droid.org/packages/com.termux.boot/) do F-Droid. Tem de vir da mesma loja que o Termux.
2. Abre o Termux:Boot uma vez. Sem isso, o Android não o deixa correr no arranque.
3. Nas definições do Android, desativa a otimização de bateria para o **Termux** e para o **Termux:Boot**.

No arranque, o script espera até 2 minutos pela internet e depois corre o `~/start.sh`. O script fica em `~/.termux/boot/vee-radio`, é criado pelo `install.sh`, e o que acontece no arranque fica registado em `~/boot.log`.

## Atualizar

```bash
cd ~/vee-radio && git pull && bash install.sh && ~/start.sh
```

Convém correr outra vez o `install.sh` depois do `git pull`, porque algumas novidades, como o arranque automático, são configuradas por ele. As playlists e os nomes guardados mantêm-se.

Se instalaste à mão, copiando os ficheiros sem `git clone`, a pasta `~/vee-radio` não tem `.git` e o `git pull` não funciona. Nesse caso, muda-lhe o nome e instala de novo:

```bash
mv ~/vee-radio ~/vee-radio.antigo
curl -sL https://raw.githubusercontent.com/vitor-gsousa/vee-radio/main/install.sh | bash
```

## Comando web

- **A Tocar Agora**: estação e faixa atuais, tocar ou parar, volume (±5%)
- **Estações**: a lista de rádios da playlist ativa, cujo nome aparece no topo. Toca-se numa estação para a ouvir e o ✕ remove-a.
- **Juntar rádio**: pesquisar no [radio-browser.info](https://www.radio-browser.info) por nome, com opção de mostrar só rádios portuguesas, ou colar o URL de um stream com um nome opcional
- **Temas**: escolher um tema (Notícias, Jazz, Fado, Anos 80...), com opção de mostrar só rádios portuguesas, e juntar as rádios mais ouvidas desse tema a uma playlist nova ou já existente, todas de uma vez ou uma a uma
- **Playlists**: trocar de playlist, criar uma nova (vazia ou com as estações atuais) e apagar playlists

Os formulários abrem em janelas por cima da página, sem JavaScript.

A lista de estações é sempre a playlist ativa: juntar ou remover uma rádio guarda logo a alteração nessa playlist, sem botão de guardar. Para ter uma lista de favoritas, cria uma playlist nova com as estações atuais e remove as que não queres, ou cria-a vazia e junta as rádios uma a uma. Ao apagar a playlist ativa, as estações continuam a tocar mas deixam de estar guardadas, e a página oferece guardá-las com outro nome. A playlist ativa fica em `~/.config/mpd/playlist-ativa.txt`.

Os nomes das estações vêm das linhas `#EXTINF` das playlists. Os nomes dados ao juntar um stream ficam também em `~/.config/mpd/nomes.m3u`.

Cada estação aparece num cartão com o seu logótipo:

- **De onde vem:** do atributo `tvg-logo` do `#EXTINF`, por exemplo `#EXTINF:-1 tvg-logo="https://.../logo.png",Antena 1`.
  - As rádios encontradas na pesquisa ou nos temas trazem-no do radio-browser.info.
  - Para as outras, o comando procura o URL do stream no radio-browser.info.
- **Onde fica guardado:** o telemóvel descarrega cada logótipo uma vez e guarda-o em `~/.cache/vee-radio/logos`.
- **Sem logótipo:** o cartão mostra as iniciais da rádio. Para voltar a descarregar os logótipos, apaga essa pasta.

## Resolução de problemas

| Problema | O que ver |
| --- | --- |
| O link não aparece | `~/tunnel.log`. O telemóvel tem de ter internet. Volta a correr o `~/start.sh`. |
| O comando web dá erro ou não abre | `~/web.log` |
| A página diz "MPD indisponível" | O MPD parou. Corre o `~/start.sh`. |
| Não arrancou sozinho depois de ligar o telemóvel | `~/boot.log`. Confirma os passos do [arranque automático](#arranque-automático). |
| A rádio para ao fim de algum tempo | O Android está a matar o Termux. Desativa a otimização de bateria para o Termux. |
| Uma rádio aparece no botão mas não toca | Corre `mpc status` para ver o erro. O stream pode ter mudado de endereço. Procura a rádio outra vez no comando web. |

Para ver o estado diretamente no Termux: `mpc status`, `mpc playlist` e `cat ~/tunnel-url.txt`.

## Notas

- O link do Cloudflare muda sempre que o `start.sh` arranca. Para ter um link fixo, é preciso um túnel Cloudflare com nome, o que exige conta e domínio.
- A pesquisa de rádios esconde os streams HLS (`.m3u8`), porque o MPD nem sempre os consegue tocar.
- O comando web não tem autenticação: qualquer pessoa com o link pode controlar a rádio.
- Os scripts têm de ter finais de linha LF. O `.gitattributes` garante isso mesmo quando se edita no Windows.
