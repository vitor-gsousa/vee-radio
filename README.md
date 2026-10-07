# VEE Rádio

Jukebox de rádio para um telemóvel Android antigo. Corre em [Termux](https://termux.dev) e usa o **MPD** para tocar streams de rádio, uma pequena app **Flask** como comando web e um **Cloudflare Tunnel** para aceder ao comando de qualquer lado.

## Ficheiros

| Ficheiro | Função |
| --- | --- |
| `install.sh` | Instala as dependências, obtém o código e configura tudo |
| `radio_web.py` | Comando web na porta `8080`: tocar, parar, volume, gerir as estações e as playlists |
| `templates/` | Páginas do comando web; no telemóvel ficam numa coluna e no PC em duas |
| `static/` | Estilos (`style.css`) e o script (`app.js`) do comando web |
| `mpd.conf` | Configuração do MPD (saída de áudio OpenSLES, porta `6600`) |
| `start.sh` / `stop.sh` | Arrancar e parar o MPD, o servidor web e o túnel |
| `config.sh` | Abre as configurações do comando no browser do telemóvel |
| `.env.example` | Modelo da configuração local (tópico do ntfy); a cópia `.env` não vai para o git |
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

Quando o túnel fica pronto, o browser do telemóvel abre uma página com o link `https://*.trycloudflare.com` e os botões **Copiar link** e **Partilhar…**, para o enviares por mensagem a quem quiseres. Essa página está em `http://localhost:8081` e só se abre no próprio telemóvel. O link também aparece no Termux e fica em `~/tunnel-url.txt`, mas não aparece no comando web: é a chave de acesso. Na rede local também podes usar `http://<IP-do-telemóvel>:8080`.

O link muda sempre que o `start.sh` corre.

### Receber o link por notificação (ntfy)

Para não teres de pegar no telemóvel da rádio, o `start.sh` pode enviar o link para um tópico do [ntfy](https://ntfy.sh). Instala a app ntfy no teu telemóvel, subscreve um tópico e escreve-o no ficheiro `~/vee-radio/.env` (o `install.sh` cria-o a partir do `.env.example`):

```bash
nano ~/vee-radio/.env
```

```
NTFY_TOPIC_URL=vee-radio-um-nome-dificil-de-adivinhar
```

Pode ser só o nome do tópico (usa o servidor público `ntfy.sh`) ou o endereço completo de outro servidor (`https://ntfy.exemplo.pt/radio`). Para tópicos com autenticação, define também `NTFY_TOKEN`. A partir daí, sempre que a rádio arranca (também pelo Termux:Boot) chega uma notificação com o link, e tocar nela abre o comando. A página de partilha deixa de abrir; só volta a abrir se o envio falhar.

No `ntfy.sh` qualquer pessoa que saiba o nome do tópico recebe o link, e quem tem o link controla a rádio. Usa um nome difícil de adivinhar: sem `NTFY_TOKEN`, o `start.sh` recusa tópicos do `ntfy.sh` com menos de 16 caracteres e sugere um.

```bash
~/stop.sh
```

Para tudo e liberta o wake lock.

### Usar sem acesso pela internet

Se só quiseres controlar a rádio no próprio telemóvel ou na rede de casa, desliga o túnel no `.env`:

```
TUNNEL_ENABLED=0
```

Assim o `start.sh` não cria o link público, não envia nada para o ntfy e abre o comando no browser do telemóvel, em `http://localhost:8080`. Na rede local continua a funcionar em `http://<IP-do-telemóvel>:8080`. Sem esta linha (ou com `1`) o túnel fica ligado.

### Configurações

Para ligar ou desligar o túnel e mudar o tópico e o token do ntfy sem editar o `.env` à mão, corre no Termux:

```bash
~/config.sh
```

Abre o comando no browser do telemóvel com a janela **Configurações**, e a partir daí o botão **⚙** fica na barra de cima desse browser. Sem túnel, o `start.sh` já abre o comando assim. **Guardar** aplica-se no próximo arranque; **Guardar e aplicar agora** reinicia já a rádio (a música para uns segundos e, com o túnel, o link muda).

As configurações não aparecem pelo link do túnel nem na rede local, e precisam de uma chave que só o Termux lê (`~/.config/vee-radio/chave`). Quem tivesse o link, ou outra app do telemóvel, podia trocar o tópico pelo seu e passar a receber os links seguintes.

### Arranque automático

Para a rádio arrancar sozinha sempre que o telemóvel liga:

1. Instala a app [Termux:Boot](https://f-droid.org/packages/com.termux.boot/) do F-Droid. Tem de vir da mesma loja que o Termux.
2. Abre o Termux:Boot uma vez. Sem isso, o Android não o deixa correr no arranque.
3. Nas definições do Android, desativa a otimização de bateria para o **Termux** e para o **Termux:Boot**.

No arranque, o script corre primeiro o `~/update.sh`, que vai buscar a versão mais recente ao GitHub (espera até 30 segundos pela rede; sem ela fica a versão que já estava), e depois o `~/start.sh`: a rádio e o comando local arrancam logo e, com o túnel ligado, espera-se até 2 minutos pela internet antes de o criar. O script fica em `~/.termux/boot/vee-radio`, é criado pelo `install.sh`, e o que acontece no arranque fica registado em `~/boot.log`.

## Atualizar

A rádio atualiza-se sozinha sempre que o telemóvel arranca (com o Termux:Boot). Para atualizar já, abre as Configurações (⚙, ou `~/config.sh` no Termux) e carrega em **Procurar atualizações**: se houver novidades, a rádio reinicia com elas e a música para uns segundos. No Termux, o mesmo é:

```bash
~/update.sh
```

O `update.sh` só avança para a versão do GitHub: se houver alterações locais que o impeçam, não mexe em nada e fica a versão atual. Quando o `install.sh` muda, corre-o também (atualiza os pacotes, por isso demora uns minutos). As playlists e os nomes guardados mantêm-se.

Numa instalação anterior a esta opção, atualiza uma vez à mão, para ficares com o `~/update.sh` e o novo arranque automático:

```bash
cd ~/vee-radio && git pull && bash install.sh && ~/start.sh
```

Se instalaste à mão, copiando os ficheiros sem `git clone`, a pasta `~/vee-radio` não tem `.git` e o `git pull` não funciona. Nesse caso, muda-lhe o nome e instala de novo:

```bash
mv ~/vee-radio ~/vee-radio.antigo
curl -sL https://raw.githubusercontent.com/vitor-gsousa/vee-radio/main/install.sh | bash
```

## Comando web

Tema escuro, como as apps de música. No telemóvel há três separadores em baixo (**Início**, **Descobrir**, **Playlists**) com o reprodutor por cima; no PC, uma barra lateral com os mesmos separadores e as playlists. A fonte é a [Manrope](https://fonts.google.com/specimen/Manrope) e os ícones são do [Phosphor](https://phosphoricons.com), ambos guardados em `static/`: a página não pede nada a outros sites. As bandeiras dos países usam a fonte "Twemoji Country Flags" (desenhos do [Twemoji](https://github.com/jdecked/twemoji), CC-BY 4.0, via [country-flag-emoji-polyfill](https://github.com/talkjs/country-flag-emoji-polyfill)), porque o Windows não tem bandeiras emoji e mostraria só as letras ("PT").

- **Início**: um resumo. O que está a tocar agora (toca-se no cartão para abrir o reprodutor) com atalho para a lista, as playlists, as rádios ouvidas recentemente (contam depois de 20 segundos a tocar) e os destaques das rádios guardadas: as mais ouvidas e as que estão em alta no radio-browser.info. Os números dos destaques são procurados em segundo plano e aparecem numa visita seguinte
- **Playlist**: cada playlist tem a sua página, em cartões com o logótipo. Na que está a tocar, tocar num cartão ouve essa estação e o ✕ remove-a (durante 2 minutos aparece um botão para anular); noutra playlist, tocar num cartão põe essa playlist a tocar a partir dessa estação, como no Spotify, e ⏮ ⏭ passam a segui-la. **Selecionar** cobre os cartões com caixas para escolher várias estações e copiá-las, movê-las para outra playlist ou removê-las. Dá para procurar pelo nome na lista e ordená-la pela ordem da playlist, pelas mais ouvidas, mais votadas ou pelas tendências (as que ganharam mais cliques desde o dia anterior) no radio-browser.info, ou de A a Z (só muda o que se vê: a playlist fica igual e ⏮ ⏭ seguem a ordem dela, como avisa uma nota). Os números do radio-browser ficam guardados no telemóvel por um dia, por isso a primeira vez pode demorar uns segundos
- **Reprodutor** (fixo em baixo, como no Spotify): estação e faixa atuais, tocar ou parar, estação anterior e seguinte. No PC tem também o volume (±5%), o site da rádio e o voto; no telemóvel estão no reprodutor aberto, que aparece ao tocar na estação, com o logótipo grande e um atalho para a estação na lista. Com os dados do radio-browser.info mostra a bandeira do país, a qualidade do stream e os géneros; o voto mostra o número de votos (o radio-browser só aceita um voto na mesma rádio a cada 10 minutos). Quando a rádio tem outras versões, a qualidade aparece num pill ("128 kbps ▾") e escolhe-se no reprodutor aberto; a troca fica guardada na playlist. Se uma versão não tocar, o comando passa sozinho para a seguinte e avisa; só aparece o erro se nenhuma tocar
- **Descobrir**: três páginas, que partilham os filtros de país, língua, qualidade mínima e ordenação (mais ouvidas, mais votadas, tendências, aleatórias ou todas de A a Z):
  - **Nome**: pesquisar no [radio-browser.info](https://www.radio-browser.info) pelo nome, ou juntar pelo endereço de um stream, com um nome opcional
  - **Temas**: as rádios de um tema (Notícias, Jazz, Fado, Anos 80...)
  - **Países**: as rádios de um país (Portugal, Brasil, Espanha...)

  A mesma rádio aparece uma só vez, mesmo que o radio-browser a tenha em várias versões (MP3 128, AAC 64...): o ▶ e o + usam a melhor (conta o bitrate e o codec, porque um AAC a 64 soa como um MP3 a 128) e o pill "N versões" mostra as outras. Em todos os resultados, o ▶ experimenta a rádio sem a juntar à lista (o reprodutor mostra "A experimentar" e um botão "Juntar") e o + junta-a à playlist escolhida em "Juntar a" (por omissão, a que está a tocar; noutra, a música não muda). Nos temas e países, "Juntar todas" junta-as também a essa playlist, ou, se se escolher, a outra ou a uma playlist nova, que passa a ser a do Início.
- **Playlists**: as playlists com uma capa feita dos logótipos das primeiras estações. O ▶ passa uma para o Início sem cortar a rádio que está a tocar: se ela estiver na playlist nova continua como estação dessa lista, se não estiver continua como "A experimentar" (com "Juntar") até se escolher outra; com a música parada, começa pela primeira estação. Tocar no nome abre a página da playlist sem mudar a música, onde também se juntam rádios, se muda o nome e se apaga

As janelas por cima da página ficam para as coisas pequenas: nova playlist, confirmar apagar, mudar o nome, configurações e o reprodutor aberto. Com JavaScript, mudar de página não recarrega tudo, a pesquisa de rádios corre enquanto se escreve, os filtros aplicam-se ao mudar, aparecem esqueletos a brilhar enquanto os dados e os logótipos carregam, os botões respondem sem recarregar a página e o reprodutor atualiza-se sozinho a cada 5 segundos (estação, música, volume).

A lista de estações é sempre a playlist ativa: juntar ou remover uma rádio guarda logo a alteração nessa playlist, sem botão de guardar. Para ter uma lista de favoritas, cria uma playlist nova com as estações atuais e remove as que não queres, ou cria-a vazia e junta as rádios uma a uma. Ao apagar a playlist ativa, as estações continuam a tocar mas deixam de estar guardadas, e a página oferece guardá-las com outro nome, com um botão ao lado desse aviso. A playlist ativa fica em `~/.config/mpd/playlist-ativa.txt`.

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
