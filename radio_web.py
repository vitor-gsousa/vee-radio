import gzip
import hashlib
import hmac
import html
import http.client
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from flask import Flask, Request, Response, g, jsonify, render_template, request, redirect, url_for
from markupsafe import Markup
from mpd import CommandError, MPDClient, MPDError
try:
    # Opcional (python-pillow no Termux): reduz os logótipos grandes; sem ele ficam como vêm
    from PIL import Image
except ImportError:
    Image = None

class BigFormRequest(Request):
    # O "Juntar" de um tema manda uma página de rádios (PAGE_SIZE), com 4 campos
    # cada; o limite do Werkzeug é de 1000 campos por formulário
    max_form_parts = 5000

app = Flask(__name__)
app.request_class = BigFormRequest
# O "Juntar" de uma página de rádios fica bem abaixo disto; acima é abuso
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

@app.template_filter("flag")
def flag(code):
    # Bandeira em emoji a partir do código ISO do país (PT -> 🇵🇹)
    if not re.fullmatch(r"[A-Za-z]{2}", code or ""):
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code.upper())

# Tem de coincidir com o playlist_directory do mpd.conf
PLAYLIST_DIR = os.path.expanduser("~/.config/mpd/playlists")
# Nomes dados a streams adicionados pela web, ainda que não estejam em nenhuma playlist
NAMES_FILE = os.path.expanduser("~/.config/mpd/nomes.m3u")
# Playlist que está carregada na fila; o start.sh volta a carregá-la se a fila estiver vazia
ACTIVE_FILE = os.path.expanduser("~/.config/mpd/playlist-ativa.txt")
# Configuração local (túnel e ntfy), lida pelo start.sh. Muda-se na janela
# Configurações, que só aparece no próprio telemóvel (is_local)
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
ENV_EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.example")
START_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start.sh")
# Vai buscar a versão mais recente ao GitHub (botão nas Configurações; também corre no arranque do telemóvel)
UPDATE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update.sh")
RESTART_LOG = os.path.expanduser("~/restart.log")
# Chave das Configurações: as outras apps do telemóvel também chegam a
# localhost:8080, mas este ficheiro só o Termux o lê. O start.sh (sem túnel) e o
# ~/config.sh abrem /entrar com ela, e o browser fica com ela num cookie
CONFIG_KEY_FILE = os.path.expanduser("~/.config/vee-radio/chave")
CONFIG_COOKIE = "vee_config"
# Escrito pelo start.sh com o link do Cloudflare Tunnel. Não aparece na página (é a
# chave de acesso e chega por ntfy ou pela página local); só serve para validar o Origin
TUNNEL_URL_FILE = os.path.expanduser("~/tunnel-url.txt")
# Logótipos descarregados, um ficheiro por stream
LOGO_DIR = os.path.expanduser("~/.cache/vee-radio/logos")
# Só URLs simples: o logótipo vai entre aspas no #EXTINF e num url() de CSS
LOGO_URL_RE = re.compile(r"^https?://[^\s\"',()\\]+$")
LOGO_MAX_BYTES = 512 * 1024
# Depois de uma falha, só volta a tentar descarregar o logótipo passado um dia
LOGO_RETRY_SECONDS = 24 * 3600
# Cliques e votos das estações no radio-browser, para ordenar a lista pelas mais
# ouvidas, votadas ou em tendência: URL do stream -> {"uuid", "clickcount", "votes", "clicktrend", "t"}
STATS_FILE = os.path.expanduser("~/.cache/vee-radio/stats.json")
STATS_MAX_AGE = 24 * 3600
# Depois de uma falha de rede, volta a tentar passada uma hora
STATS_RETRY_SECONDS = 3600
# Muda quando se juntam campos às entradas, para as antigas serem atualizadas
STATS_VERSION = 3
# O radio-browser só aceita um voto por IP na mesma rádio a cada 10 minutos
VOTE_COOLDOWN = 600
HOMEPAGE_RE = re.compile(r"^https?://[^\s\"'<>]+$")
UUID_RE = re.compile(r"^[0-9a-f-]{36}$")
API = "https://all.api.radio-browser.info"
# Cores das iniciais quando a rádio não tem logótipo
TILE_COLORS = ["#f38ba8", "#fab387", "#f9e2af", "#a6e3a1", "#94e2d5", "#89dceb", "#89b4fa", "#cba6f7", "#f5c2e7"]

# Temas: etiquetas (tags) do radio-browser.info com nome em português. A lista é
# fixa porque as etiquetas mais usadas na API estão em várias línguas e misturam
# géneros com países ("méxico", "fm", "radio")
THEMES = [
    ("news", "Notícias"), ("talk", "Conversa"), ("sports", "Desporto"),
    ("pop", "Pop"), ("hits", "Êxitos"), ("rock", "Rock"), ("metal", "Metal"), ("indie", "Indie"),
    ("dance", "Dança"), ("house", "House"), ("electronic", "Eletrónica"), ("hip hop", "Hip-Hop"),
    ("jazz", "Jazz"), ("blues", "Blues"), ("soul", "Soul"), ("reggae", "Reggae"), ("latin", "Latina"),
    ("chillout", "Chillout"), ("lounge", "Lounge"), ("ambient", "Ambiente"), ("classical", "Clássica"),
    ("70s", "Anos 70"), ("80s", "Anos 80"), ("90s", "Anos 90"), ("oldies", "Clássicos"),
    ("fado", "Fado"), ("portuguese", "Música portuguesa"), ("folk", "Folk"), ("country", "Country"),
    ("kids", "Infantil"),
]
THEME_LABELS = dict(THEMES)
# Países do filtro e da janela "Países", com nome em português. A lista é fixa
# porque a API só dá os nomes em inglês ("The United States Of America"); fica
# Portugal e o Brasil à frente e os restantes por ordem alfabética
COUNTRIES = [
    ("PT", "Portugal"), ("BR", "Brasil"),
    ("ZA", "África do Sul"), ("DE", "Alemanha"), ("AO", "Angola"), ("AR", "Argentina"), ("AU", "Austrália"),
    ("AT", "Áustria"), ("BE", "Bélgica"), ("CV", "Cabo Verde"), ("CA", "Canadá"), ("CZ", "Chéquia"),
    ("CL", "Chile"), ("CN", "China"), ("CO", "Colômbia"), ("KR", "Coreia do Sul"), ("CU", "Cuba"),
    ("DK", "Dinamarca"), ("EG", "Egito"), ("ES", "Espanha"), ("US", "Estados Unidos"), ("PH", "Filipinas"),
    ("FI", "Finlândia"), ("FR", "França"), ("GR", "Grécia"), ("HU", "Hungria"), ("IN", "Índia"),
    ("ID", "Indonésia"), ("IE", "Irlanda"), ("IL", "Israel"), ("IT", "Itália"), ("JP", "Japão"),
    ("LU", "Luxemburgo"), ("MA", "Marrocos"), ("MX", "México"), ("MZ", "Moçambique"), ("NG", "Nigéria"),
    ("NO", "Noruega"), ("NZ", "Nova Zelândia"), ("NL", "Países Baixos"), ("PE", "Peru"), ("PL", "Polónia"),
    ("GB", "Reino Unido"), ("RO", "Roménia"), ("RU", "Rússia"), ("SE", "Suécia"), ("CH", "Suíça"),
    ("TR", "Turquia"), ("UA", "Ucrânia"), ("VE", "Venezuela"),
]
COUNTRY_LABELS = dict(COUNTRIES)
# Ordenações da API (campo order, do maior para o menor; "name" de A a Z); "random"
# serve para descobrir rádios novas
ORDERS = [("clickcount", "mais ouvidas"), ("votes", "mais votadas"), ("clicktrend", "tendências"), ("random", "aleatórias"),
          ("name", "de A a Z")]
ORDER_LABELS = dict(ORDERS)
# Resultados da pesquisa, dos temas e dos países, por página: o radio-browser
# devolve-os aos bocados (offset), por isso um tema com milhares de rádios vê-se
# todo, página a página, com qualquer ordenação. MAX_PAGE só trava endereços absurdos
PAGE_SIZE = 100
MAX_PAGE = 500
# Ordenações da lista de estações na página. Só mudam o que se vê: a playlist,
# e o anterior/seguinte, continuam pela ordem guardada
LIST_ORDERS = [("", "ordem da playlist"), ("clickcount", ORDER_LABELS["clickcount"]), ("votes", ORDER_LABELS["votes"]),
               ("clicktrend", ORDER_LABELS["clicktrend"]), ("name", "de A a Z"), ("-name", "de Z a A")]
POPULAR_ORDERS = ("clickcount", "votes", "clicktrend")
# Línguas pelo nome em inglês, como estão na API. O filtro language apanha partes
# do nome, por isso "portuguese" inclui "brazilian portuguese"
LANGUAGES = [
    ("portuguese", "Português"), ("english", "Inglês"), ("spanish", "Espanhol"), ("french", "Francês"),
    ("german", "Alemão"), ("italian", "Italiano"), ("dutch", "Neerlandês"), ("greek", "Grego"),
    ("polish", "Polaco"), ("romanian", "Romeno"), ("russian", "Russo"), ("ukrainian", "Ucraniano"),
    ("turkish", "Turco"), ("arabic", "Árabe"), ("hindi", "Hindi"), ("chinese", "Chinês"),
]
LANGUAGE_LABELS = dict(LANGUAGES)
# Qualidade mínima do stream, em kbps (bitrateMin da API)
BITRATES = [64, 128, 192]

STREAM_FAILED_HINT ='O endereço pode ter mudado. Procura a rádio outra vez em "Descobrir" e remove a antiga.'

STATES = {"play": "A tocar", "pause": "Em pausa", "stop": "Parado"}

# A estação que se escolheu tocar (id do MPD, None depois de parar), para o vigia
# do stream saber o que o utilizador quer ouvir. seq muda a cada escolha
_choice = {"seq": 0, "id": None}
_choice_lock = threading.Lock()

def note_choice(song_id):
    with _choice_lock:
        _choice["seq"] += 1
        _choice["id"] = None if song_id is None else str(song_id)

class RadioClient(MPDClient):
    # As ligações das rotas: tocar e parar ficam registados para o vigia. Assim
    # distingue um stream que caiu (o MPD salta sozinho para a seguinte, ou para)
    # de um ⏭ ou ⏹, sem cada rota ter de se lembrar disso
    # Mesmo quando o MPD responde com erro (o stream não ligou), a escolha conta:
    # o vigia deixa a anterior e tenta esta
    def playid(self, *args):
        try:
            return super().playid(*args)
        finally:
            note_choice(args[0] if args else self.status().get("songid"))

    def play(self, *args):
        try:
            return super().play(*args)
        finally:
            note_choice(self.status().get("songid"))

    def stop(self):
        result = super().stop()
        note_choice(None)
        return result

@contextmanager
def mpd_client(client_class=RadioClient):
    # Abre uma ligação ao MPD e garante que é fechada mesmo que um comando falhe
    client = client_class()
    client.connect("localhost", 6600)
    try:
        yield client
    finally:
        try:
            client.close()
        except (MPDError, OSError):
            pass
        client.disconnect()

# Os nomes das estações vêm dos #EXTINF dos ficheiros .m3u e não das tags do MPD:
# quando um stream toca, o MPD substitui as tags dessa entrada da fila pelas do
# stream (ICY) e o nome original perde-se. O logótipo vai no atributo tvg-logo,
# como nas listas de IPTV: #EXTINF:-1 tvg-logo="https://...",Nome
EXTINF_RE = re.compile(r'#EXTINF:((?:[^,"]|"[^"]*")*),?(.*)')

def read_m3u(path):
    # Lista de (url, nome, logótipo)
    entries = []
    name = logo = None
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return entries
    with f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                m = EXTINF_RE.match(line)
                attr = re.search(r'tvg-logo="([^"]*)"', m.group(1))
                name = m.group(2).strip() or None
                logo = attr.group(1) if attr and LOGO_URL_RE.match(attr.group(1)) else None
            elif line and not line.startswith("#"):
                entries.append((line, name, logo))
                name = logo = None
    return entries

def extinf(name, logo):
    attrs = f' tvg-logo="{logo}"' if logo else ""
    return f"#EXTINF:-1{attrs},{name or ''}\n"

def write_m3u(path, entries):
    # Escreve num ficheiro à parte e troca-o de uma vez: quem ler a playlist ao
    # mesmo tempo (outro pedido, o MPD) nunca a apanha vazia ou a meio
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for url, name, logo in entries:
            if name or logo:
                f.write(extinf(name, logo))
            f.write(url + "\n")
    os.replace(tmp, path)

# O protocolo do MPD separa os comandos por linhas e o python-mpd2 não escapa as
# quebras de linha: um URL ou nome com uma delas faria correr outro comando (kill)
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Só streams da internet: sem isto, um caminho relativo tocava ficheiros da pasta
# Music e outros protocolos do MPD (smb://, nfs://) chegavam à rede local
STREAM_URL_RE = re.compile(r"^https?://\S+$", re.I)

# Tamanhos máximos do que vem dos formulários: o nome da playlist é um nome de
# ficheiro (255 bytes no máximo) e o resto vai para os .m3u, que são lidos a cada pedido
MAX_URL = 2048
MAX_NAME = 200
MAX_PLAYLIST_BYTES = 200

def safe_url(url):
    return bool(url) and len(url) <= MAX_URL and bool(STREAM_URL_RE.match(url)) and not CONTROL_RE.search(url)

def clean_name(name):
    # Junta espaços e quebras de linha para o nome não partir o formato m3u
    return " ".join((name or "").split())[:MAX_NAME]

def playlist_path(name):
    if not name or "/" in name or "\\" in name or name.startswith(".") or CONTROL_RE.search(name) \
            or len(name.encode("utf-8")) > MAX_PLAYLIST_BYTES:
        return None
    return os.path.join(PLAYLIST_DIR, name + ".m3u")

BAD_NAME = ("Nome inválido", "O nome da playlist não pode ter / nem \\ nem quebras de linha, nem começar por ponto, "
            "e tem de ser curto.", 400)

# Juntar, remover e trocar de playlist leem a fila e reescrevem os .m3u: dois
# pedidos ao mesmo tempo (vários comandos abertos) não se podem misturar
QUEUE_LOCK = threading.Lock()

def xml_tag(text, *tags):
    # Expressão regular em vez de parser XML: o título ICY pode chegar cortado
    for tag in tags:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
        if m and m.group(1).strip():
            return html.unescape(m.group(1).strip())
    return ""

def clean_title(title):
    # As rádios da Bauer (Comercial, M80, Cidade...) mandam um XML no título ICY
    # em vez de "Artista - Música"; fica só o artista e a música, ou nada
    if not title.lstrip().startswith("<"):
        return title
    artist = xml_tag(title, "DB_DALET_ARTIST_NAME", "DB_LEAD_ARTIST_NAME")
    song = xml_tag(title, "DB_DALET_TITLE_NAME", "DB_SONG_NAME")
    # Sem artista, o DB_DALET_TITLE_NAME costuma ser o slogan da rádio
    return f"{artist} - {song}" if artist and song else ""

def file_sig(path):
    # Muda sempre que o ficheiro é reescrito: o write_m3u troca-o por outro (inode novo)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return (path, None)
    return (path, st.st_ino, st.st_mtime_ns, st.st_size)

# As playlists só são lidas outra vez quando algum ficheiro muda: o /estado (a cada
# 5 segundos, por cada pessoa com a página aberta), a página e cada logótipo
# precisam dos nomes, e ler tudo de cada vez era o que mais pesava
_index_cache = {"key": None, "info": {}, "counts": {}, "covers": {}}
_index_lock = threading.Lock()

def station_index():
    paths = []
    if os.path.isdir(PLAYLIST_DIR):
        paths += [os.path.join(PLAYLIST_DIR, f) for f in sorted(os.listdir(PLAYLIST_DIR)) if f.endswith(".m3u")]
    key = tuple(file_sig(p) for p in paths + [NAMES_FILE])
    with _index_lock:
        if _index_cache["key"] == key:
            return _index_cache["info"], _index_cache["counts"], _index_cache["covers"]
    info, counts, covers, members = {}, {}, {}, {}
    for path in paths + [NAMES_FILE]:
        entries = read_m3u(path)
        if path != NAMES_FILE:
            name = os.path.basename(path)[:-len(".m3u")]
            counts[name] = len(entries)
            members[name] = {u for u, _, _ in entries}
            # Capa da playlist, como no Spotify: os logótipos das primeiras quatro estações
            covers[name] = [(logo_key(u), n or "") for u, n, _ in entries[:4]]
        for url, name, logo in entries:
            entry = info.setdefault(url, {"name": None, "logo": None})
            if name:
                entry["name"] = name
            if logo:
                entry["logo"] = logo
    with _index_lock:
        _index_cache.update(key=key, info=info, counts=counts, covers=covers, members=members)
    return info, counts, covers

def playlist_members():
    # Nome da playlist -> URLs que tem (para o ✓ do menu "Juntar a"). Partilhado: só para ler
    station_index()
    return _index_cache["members"]

def station_info():
    # URL -> {"name", "logo"}, juntando as playlists guardadas e o ficheiro de
    # nomes; cada ficheiro sobrepõe-se aos anteriores. É partilhado: só para ler
    return station_index()[0]

def logo_key(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    # Só um SVG que comece como SVG: uma página HTML com um ícone <svg> lá dentro
    # (a de um router, por exemplo) não pode passar por imagem e ser devolvida
    head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if (head.startswith(b"<svg") or head.startswith(b"<?xml")) and b"<svg" in head and b"<html" not in head:
        return "image/svg+xml"
    return None

def fetch(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": "vee-radio/1.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read(limit + 1)

# Os logótipos vêm de endereços escritos por quem usa o link público: o telemóvel
# não pode ir buscá-los à rede local (router, MPD, a própria app). O endereço é
# verificado no momento de ligar, e a ligação é feita ao IP verificado: verificar
# antes e deixar o urllib resolver outra vez deixava um DNS trocar o IP pelo meio
def public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    host, port = address[:2]
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            # ValueError e não OSError: o logótipo fica como falhado por um dia, e não
            # como falha de rede, que voltava a tentar ao fim de uma hora
            raise ValueError(f"endereço não público: {host}")
    return socket.create_connection(infos[0][4][:2], timeout, source_address)

class PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = public_connection

class PublicHTTPSConnection(http.client.HTTPSConnection):
    # O TLS continua a usar o nome do servidor (SNI e certificado), só a ligação vai ao IP verificado
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = public_connection

class PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(PublicHTTPConnection, req)

class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PublicHTTPSConnection, req, context=self._context)

class PublicRedirects(urllib.request.HTTPRedirectHandler):
    # Os redirecionamentos passam pelas mesmas ligações; aqui só se recusa sair do
    # http(s), porque o urllib também seguiria para ftp://
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme not in ("http", "https"):
            raise urllib.error.URLError(f"redirecionamento recusado: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

# Sem proxies do ambiente: a verificação tem de ser feita ao destino e não ao proxy
_public_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicHTTPHandler,
                                             PublicHTTPSHandler, PublicRedirects)

def fetch_public(url, limit):
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"endereço recusado: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "vee-radio/1.0"})
    with _public_opener.open(req, timeout=8) as r:
        return r.read(limit + 1)

def api_get(path, **params):
    return json.loads(fetch(API + path + "?" + urllib.parse.urlencode(params), 4 * 1024 * 1024))

def api_stations(path, **params):
    # Lista de estações da API. Uma resposta que não é uma lista (um erro em JSON)
    # passa a ValueError, como o JSON inválido, e as entradas estranhas ficam de fora
    found = api_get(path, **params)
    if not isinstance(found, list):
        raise ValueError(f"resposta inesperada do radio-browser em {path}")
    return [st for st in found if isinstance(st, dict)]

def to_int(value):
    # Números da API, que podem vir a null ou noutro formato
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def lookup_logo(stream_url):
    # Só procura pelo URL exato do stream: pelo nome aparecem rádios de outros países
    for s in api_stations("/json/stations/byurl", url=stream_url):
        if LOGO_URL_RE.match(s.get("favicon") or ""):
            return s["favicon"]
    return None

# A barra (em segundo plano), a ordenação da lista e os votos escrevem no mesmo
# ficheiro; cada escrita relê-o e junta só as suas entradas
STATS_LOCK = threading.Lock()

# O /estado lê os detalhes da estação a tocar a cada 5 segundos: o ficheiro só é
# interpretado outra vez quando muda
_stats_cache = {"key": None, "stats": {}}

def read_stats_file():
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}

def load_stats():
    # Partilhado entre pedidos: só para ler. Quem escreve usa o merge_stats
    key = file_sig(STATS_FILE)
    if _stats_cache["key"] != key:
        _stats_cache.update(key=key, stats=read_stats_file())
    return _stats_cache["stats"]

# Entradas de rádios que já não estão em nenhuma playlist saem ao fim de um mês;
# sem isto o ficheiro crescia com cada rádio que alguma vez passou por um tema
STATS_KEEP_SECONDS = 30 * 24 * 3600

def prune_stats(stats, now):
    known = station_info()
    return {url: e for url, e in stats.items()
            if url in known or now - max(e.get("t") or 0, e.get("voted_at") or 0) < STATS_KEEP_SECONDS}

def save_stats(stats):
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, f)
    os.replace(tmp, STATS_FILE)

def merge_stats(updates):
    # Grava entradas novas mantendo o momento do último voto
    with STATS_LOCK:
        stats = read_stats_file()
        for url, entry in updates.items():
            voted = stats.get(url, {}).get("voted_at")
            stats[url] = {**entry, "voted_at": voted} if voted and "voted_at" not in entry else entry
        stats = prune_stats(stats, time.time())
        save_stats(stats)
        return stats

def stat_entry(station):
    # Números e detalhes da rádio para a lista e para a barra do reprodutor
    tags = []
    for tag in str(station.get("tags") or "").split(","):
        tag = " ".join(tag.split())[:20]
        if tag and tag.lower() not in (t.lower() for t in tags):
            tags.append(tag)
    homepage = station.get("homepage") or ""
    return {"v": STATS_VERSION, "uuid": station.get("stationuuid"), "t": time.time(),
            "clickcount": to_int(station.get("clickcount")), "votes": to_int(station.get("votes")),
            "clicktrend": to_int(station.get("clicktrend")),
            "countrycode": (station.get("countrycode") or "").upper(), "tags": tags[:3],
            "homepage": homepage if HOMEPAGE_RE.match(homepage) else "",
            "codec": station.get("codec") or "", "bitrate": to_int(station.get("bitrate"))}

def stats_stale(entry, now):
    return now - entry.get("t", 0) > STATS_MAX_AGE or entry.get("v") != STATS_VERSION

def remember_uuids(pairs):
    # Rádios juntadas a partir da pesquisa ou de um tema: guardar o uuid deixa
    # atualizar os números de muitas de uma vez, sem as procurar pelo URL
    pairs = [(u, i) for u, i in pairs if u and UUID_RE.match(i or "")]
    if not pairs:
        return
    known = load_stats()
    merge_stats({url: {"uuid": uuid, "clickcount": 0, "votes": 0, "t": 0}
                 for url, uuid in pairs if known.get(url, {}).get("uuid") != uuid})

def station_stats(urls):
    # Números e detalhes do radio-browser das estações, guardados por um dia. As
    # de uuid conhecido atualizam-se num só pedido; as outras procuram-se pelo URL, em paralelo
    stats = load_stats()
    now = time.time()
    updates = {}
    def stale():
        return [u for u in urls if u not in updates and stats_stale(stats.get(u, {}), now)]
    # Depois de uma falha de rede não se pede mais nada: com a API em baixo, cada
    # pedido esperava pelo timeout e ordenar uma lista grande demorava minutos
    api_down = threading.Event()
    by_uuid = {stats[u]["uuid"]: u for u in stale() if stats.get(u, {}).get("uuid")}
    uuids = list(by_uuid)
    for i in range(0, len(uuids), 100):
        try:
            for st in api_stations("/json/stations/byuuid", uuids=",".join(uuids[i:i + 100])):
                if st.get("stationuuid") in by_uuid:
                    updates[by_uuid[st["stationuuid"]]] = stat_entry(st)
        except OSError:
            api_down.set()
            break
        except ValueError:
            break
    def lookup(url):
        old = stats.get(url, {"uuid": None, "clickcount": 0, "votes": 0})
        retry = {**old, "v": STATS_VERSION, "t": now - STATS_MAX_AGE + STATS_RETRY_SECONDS}
        if api_down.is_set():
            return url, retry
        try:
            found = api_stations("/json/stations/byurl", url=url)
        except OSError:
            api_down.set()
            return url, retry
        except ValueError:
            return url, retry
        # A mesma rádio aparece várias vezes; fica a mais ouvida
        best = max(found, key=lambda st: to_int(st.get("clickcount")), default=None)
        return url, stat_entry(best) if best else {"v": STATS_VERSION, "uuid": None, "clickcount": 0, "votes": 0, "t": now}
    missing = stale()
    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            updates.update(pool.map(lookup, missing))
    return merge_stats(updates) if updates else stats

# Estações a ser procuradas em segundo plano para a barra, para não pedir a mesma duas vezes
_refreshing = set()
_refreshing_lock = threading.Lock()

def station_details(url):
    # Detalhes da estação a tocar, só do que está guardado: a barra é pedida a
    # cada 5 segundos e não pode esperar pela API. Se faltarem ou estiverem
    # velhos, procura-os em segundo plano e a barra mostra-os no pedido seguinte
    entry = load_stats().get(url, {})
    if stats_stale(entry, time.time()):
        with _refreshing_lock:
            if url in _refreshing:
                return entry
            _refreshing.add(url)
        def refresh():
            try:
                station_stats([url])
            finally:
                with _refreshing_lock:
                    _refreshing.discard(url)
        threading.Thread(target=refresh, daemon=True).start()
    return entry

def refresh_stats_later(urls):
    # O mesmo para várias estações de uma vez (os destaques do Início); só corre
    # uma destas atualizações de cada vez
    with _refreshing_lock:
        if "*" in _refreshing:
            return
        _refreshing.add("*")
    def refresh():
        try:
            station_stats(urls)
        finally:
            with _refreshing_lock:
                _refreshing.discard("*")
    threading.Thread(target=refresh, daemon=True).start()

@app.template_filter("compact")
def compact(n):
    # 14908 -> "14,9 mil"
    if n < 1000:
        return str(n)
    if n < 1000000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " mil"
    return f"{n / 1000000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " M"

def initials_svg(name):
    words = re.findall(r"\w+", name or "")
    text = "".join(w[0] for w in words[:2]).upper() or "?"
    color = TILE_COLORS[int(hashlib.sha1((name or "").encode("utf-8")).hexdigest(), 16) % len(TILE_COLORS)]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="{color}"/>'
            f'<text x="50" y="50" dy=".35em" text-anchor="middle" font-family="sans-serif" font-weight="bold" '
            f'font-size="{40 if len(text) > 1 else 50}" fill="#11111b">{html.escape(text)}</text></svg>')

# A fila do MPD é sempre a playlist ativa: juntar ou remover uma estação na
# página reescreve o ficheiro dessa playlist. Sem playlist ativa (por exemplo,
# depois de a apagar) a fila fica só no MPD até ser guardada com outro nome.
def active_playlist():
    try:
        with open(ACTIVE_FILE, encoding="utf-8") as f:
            name = f.read().strip()
    except FileNotFoundError:
        return ""
    path = playlist_path(name)
    return name if path and os.path.isfile(path) else ""

def set_active(name):
    if name:
        with open(ACTIVE_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write(name + "\n")
    elif os.path.exists(ACTIVE_FILE):
        os.remove(ACTIVE_FILE)

# Rádio a experimentar ("▶" nos resultados): está na fila do MPD, porque o MPD só
# toca o que lá está, mas não conta como estação da lista nem vai para a playlist.
# Fica num ficheiro e não em memória: depois de reiniciar a app, a sincronização
# seguinte guardava-a na playlist
PREVIEW_FILE = os.path.expanduser("~/.config/vee-radio/a-ouvir.json")

def read_preview():
    # {"url", "name", "logo"} ou None
    try:
        with open(PREVIEW_FILE, encoding="utf-8") as f:
            preview = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    return preview if isinstance(preview, dict) and safe_url(preview.get("url") or "") else None

def write_state(path, entry):
    # Pequenos ficheiros de estado em JSON; None apaga-o
    if entry:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entry, f)
        os.replace(tmp, path)
    elif os.path.exists(path):
        os.remove(path)

def set_preview(entry):
    write_state(PREVIEW_FILE, entry)

# Última estação removida com o ✕, para se poder anular durante uns minutos. O
# nome e o logótipo vão com ela: se só estavam nessa playlist, saíram com a remoção
REMOVED_FILE = os.path.expanduser("~/.config/vee-radio/removida.json")
UNDO_SECONDS = 120

def read_removed():
    # {"url", "name", "logo", "pos", "playlist", "t"}, só enquanto se pode anular
    # e se a playlist ativa ainda é a mesma
    try:
        with open(REMOVED_FILE, encoding="utf-8") as f:
            removed = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(removed, dict) or not safe_url(removed.get("url") or ""):
        return None
    if time.time() - to_int(removed.get("t")) >= UNDO_SECONDS or removed.get("playlist") != active_playlist():
        return None
    return removed

# Ouvidas recentemente (no Início): a mais recente primeiro, sem repetidas. Uma
# estação só conta depois de HISTORY_MIN_SECONDS a tocar, para não encher a lista
# ao saltar estações com ⏭
HISTORY_FILE = os.path.expanduser("~/.config/vee-radio/historico.json")
HISTORY_SIZE = 20
HISTORY_MIN_SECONDS = 20
HISTORY_LOCK = threading.Lock()
_history_last = None

def read_history():
    # [{"url", "name", "logo", "t"}]
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, ValueError):
        return []
    if not isinstance(history, list):
        return []
    return [h for h in history if isinstance(h, dict) and safe_url(h.get("url") or "")]

def note_played(url, name, logo):
    # Chamado a cada /estado: só lê e escreve o ficheiro quando a estação muda
    global _history_last
    if url == _history_last:
        return
    with HISTORY_LOCK:
        history = read_history()
        if not (history and history[0]["url"] == url):
            entry = {"url": url, "name": name or None, "logo": logo if LOGO_URL_RE.match(logo or "") else None,
                     "t": time.time()}
            write_state(HISTORY_FILE, [entry] + [h for h in history if h["url"] != url][:HISTORY_SIZE - 1])
        _history_last = url

def removed_context():
    # A faixa "Anular" da página, com os segundos que faltam para o script a esconder
    removed = read_removed()
    if not removed:
        return None
    return {"name": removed.get("name") or removed["url"],
            "left": max(1, UNDO_SECONDS - int(time.time() - to_int(removed.get("t"))))}

def preview_song(queue):
    # A entrada da fila que está a ser experimentada, ou None
    preview = read_preview()
    return next((s for s in queue if s["file"] == preview["url"]), None) if preview else None

def drop_preview(c, keep_url=None):
    # Tocou-se outra estação: a que se estava a experimentar sai da fila
    preview = read_preview()
    if not preview or preview["url"] == keep_url:
        return
    song = preview_song(c.playlistinfo())
    if song:
        try:
            c.deleteid(song["id"])
        except CommandError:
            pass
    set_preview(None)

def queue_entries(c):
    # O save do MPD só escreve os URLs, por isso as playlists são escritas aqui para manter os nomes
    info = station_info()
    entries = []
    queue = c.playlistinfo()
    trying = preview_song(queue)
    for s in queue:
        if s is trying:
            continue
        known = info.get(s["file"], {})
        entries.append((s["file"], known.get("name") or s.get("name"), known.get("logo")))
    return entries

def sync_active(c, restore=None):
    # restore: URL -> (nome, logótipo) de estações que voltam e que já não estão
    # em nenhum ficheiro (anular a remoção)
    name = active_playlist()
    if name:
        entries = queue_entries(c)
        if restore:
            entries = [(u, n or restore.get(u, (None, None))[0], l or restore.get(u, (None, None))[1])
                       for u, n, l in entries]
        write_m3u(playlist_path(name), entries)

def chosen_playlist():
    # Playlist escolhida em "Juntar a" (campo para): uma playlist guardada que não
    # é a que está a tocar. Vazio quer dizer a lista principal (a fila)
    name = request.values.get("para", "").strip()
    path = playlist_path(name)
    return name if path and os.path.isfile(path) and name != active_playlist() else ""

def append_to_playlist(c, name, new_entries):
    # Junta (url, nome, logótipo) que ainda não estão na playlist e devolve quantas
    # entraram. Na playlist a tocar entram também na fila, num só pedido ao MPD; a
    # que se estava a experimentar já está na fila e passa a ser da lista
    path = playlist_path(name)
    entries = read_m3u(path)
    known = {u for u, _, _ in entries}
    new = []
    for url, title, logo in new_entries:
        if url not in known:
            new.append((url, title, logo))
            known.add(url)
    if not new:
        return 0
    if name == active_playlist():
        preview = read_preview()
        if preview and preview["url"] in known:
            set_preview(None)
        in_queue = {s["file"] for s in c.playlistinfo()}
        to_add = [u for u, _, _ in new if u not in in_queue]
        if to_add:
            c.command_list_ok_begin()
            for url in to_add:
                c.add(url)
            c.command_list_end()
        write_m3u(path, entries + new)
        sync_active(c)
    else:
        write_m3u(path, entries + new)
    return len(new)

def remove_from_playlist(c, name, urls):
    # Na playlist a tocar sai da fila (e a sincronização reescreve o ficheiro)
    if name == active_playlist():
        for s in listed(c.playlistinfo()):
            if s["file"] in urls:
                try:
                    c.deleteid(s["id"])
                except CommandError:
                    pass
        sync_active(c)
    else:
        path = playlist_path(name)
        write_m3u(path, [e for e in read_m3u(path) if e[0] not in urls])

def error_page(title, message, code):
    return render_template("error.html", title=title, message=message), code

@app.errorhandler(OSError)
def mpd_unreachable(e):
    return error_page("MPD indisponível", "Não foi possível ligar ao MPD. Confirma que está a correr (~/start.sh).", 503)

@app.errorhandler(MPDError)
def mpd_failed(e):
    if "Failed to decode" in str(e):
        # O stream não respondeu (link mudou, rádio em baixo): explica em vez de mostrar só o erro técnico
        return error_page("Esta rádio não está a responder", f"{STREAM_FAILED_HINT} ({e})", 502)
    return error_page("Erro do MPD", str(e), 500)

def search_filters():
    # País, língua, qualidade e ordenação vindos do formulário; valores desconhecidos ficam nos de omissão
    country = request.args.get("pais", "")
    language = request.args.get("lingua", "")
    order = request.args.get("ordem", "")
    kbps = request.args.get("kbps", "")
    return {"pais": country if country in COUNTRY_LABELS else "",
            "lingua": language if language in LANGUAGE_LABELS else "",
            "kbps": int(kbps) if kbps.isdigit() and int(kbps) in BITRATES else 0,
            "ordem": order if order in ORDER_LABELS else "clickcount"}

def display_namer(info):
    def display_name(song):
        return info.get(song["file"], {}).get("name") or song.get("name") or song.get("title") or song["file"]
    return display_name

def player_context(status, current, info):
    # Dados da barra do reprodutor, usados na página e no /estado
    display_name = display_namer(info)
    playing = status.get("state") != "stop" and bool(current.get("file"))
    try:
        volume = int(status.get("volume", -1))
    except ValueError:
        volume = -1
    details = station_details(current["file"]) if current.get("file") else {}
    # O bitrate do MPD é o real, enquanto toca; o da API é o anunciado pela rádio
    live = status.get("bitrate")
    kbps = int(live) if playing and live and live.isdigit() and live != "0" else details.get("bitrate") or 0
    # A experimentar: o nome vem dos resultados, porque ainda não está em nenhum .m3u
    preview = read_preview()
    if not (preview and current.get("file") == preview["url"]):
        preview = None
    station = ""
    if current.get("file"):
        station = info.get(current["file"], {}).get("name") or (preview or {}).get("name") or display_name(current)
    # Outras versões da rádio (para escolher no reprodutor aberto)
    versions = []
    if current.get("file"):
        alts = station_versions(current["file"], station, details.get("countrycode"))
        if len(alts) > 1:
            versions = [{**v, "current": v["url"] == current["file"]} for v in alts]
    if playing and current.get("file"):
        try:
            elapsed = float(status.get("elapsed") or 0)
        except ValueError:
            elapsed = 0
        if elapsed >= HISTORY_MIN_SECONDS:
            note_played(current["file"], station,
                        info.get(current["file"], {}).get("logo") or (preview or {}).get("logo"))
    # O vigia do stream: a voltar a ligar, falhou, ou passou para outra versão
    watch = watch_view(current.get("file"))
    stream_note = (f"A voltar a ligar… (tentativa {watch['attempt']} de {len(RETRY_DELAYS)})"
                   if watch.get("attempt") else watch.get("text", ""))
    return {"status": status, "state": STATES.get(status.get("state"), status.get("state")),
            "stream_note": stream_note, "stream_failed": bool(watch.get("failed")),
            "details": details, "kbps": kbps, "versions": versions,
            "voted": time.time() - (details.get("voted_at") or 0) < VOTE_COOLDOWN,
            "current_station": station,
            "current_title": clean_title(current.get("title", "")),
            "current_key": logo_key(current["file"]) if current.get("file") else "",
            "current_id": current.get("id") if playing else None,
            # Cartão da estação atual, mesmo parada; a que se experimenta não tem
            "tile_id": current.get("id") if current.get("file") and not preview else None,
            "preview": preview, "active": active_playlist(), "list_href": queue_url(),
            "volume": volume, "has_stations": status.get("playlistlength", "0") != "0"}

def with_sig(player):
    # Assinatura do que a barra mostra: o script manda-a no /estado e, se não mudou,
    # a barra não é feita outra vez nem enviada
    d = player["details"]
    parts = [player["current_station"], player["current_title"], player["current_key"], player["status"].get("state"),
             player["volume"], player["has_stations"], player["kbps"], d.get("countrycode"), ",".join(d.get("tags") or []),
             d.get("homepage"), d.get("uuid"), d.get("votes"), player["voted"], bool(player["preview"]), player["active"], player["tile_id"],
             ",".join(v["url"] for v in player["versions"]), player["stream_note"]]
    player["sig"] = hashlib.sha1("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:12]
    return player

def listed(queue):
    # As estações da lista: a fila sem a rádio que se está a experimentar
    trying = preview_song(queue)
    return [s for s in queue if s is not trying]

def queue_sig(queue):
    # Muda só quando se juntam, removem ou trocam estações. A versão da fila do
    # MPD (status.playlist) também muda quando o stream a tocar muda de música,
    # e o script recarregava a página a cada música. A rádio a experimentar fica
    # de fora: experimentar uma não recarrega a página
    return hashlib.sha1(",".join(s["id"] for s in listed(queue)).encode()).hexdigest()[:12]

def station_cards(items, current_id):
    # Cartões de uma lista de estações ({"id", "file", "name"}; id None fora da
    # fila), com o filtro e a ordenação do URL. As escondidas pelo filtro vão na
    # página com hidden, para o script as mostrar enquanto se escreve
    list_filter = request.args.get("filtro", "").strip()
    list_order = request.args.get("ordenar", "")
    if list_order not in dict(LIST_ORDERS):
        list_order = ""
    wanted = plain_text(list_filter)
    cards = []
    for s in items:
        # O nome já normalizado vai na página (data-plain), para o filtro do script
        # não ter de o normalizar de novo a cada tecla
        plain = plain_text(s["name"])
        cards.append({**s, "key": logo_key(s["file"]), "plain": plain, "hidden": wanted not in plain,
                      "current": s["id"] is not None and s["id"] == current_id})
    if list_order in POPULAR_ORDERS:
        stats = station_stats([c["file"] for c in cards])
        for c in cards:
            c["stat"] = stats.get(c["file"], {}).get(list_order, 0)
        # sort estável: em caso de empate fica a ordem da playlist
        cards.sort(key=lambda c: -c["stat"])
    elif list_order in ("name", "-name"):
        cards.sort(key=lambda c: sort_key(c["name"]), reverse=list_order == "-name")
    return cards, list_filter, list_order

def render_page(template, page, results=None, query="", filters=None, theme=None, view=None, tab=None,
                dashboard=None, pages=None):
    # Todas as páginas têm a navegação, o reprodutor e as janelas pequenas (nova
    # playlist, configurações); page diz qual dos separadores está aceso
    with mpd_client() as c:
        status = c.status()
        current = watched_song(c, c.currentsong())
        queue = listed(c.playlistinfo())
        stored_playlists = sorted(p['playlist'] for p in c.listplaylists())

    info = station_info()
    display_name = display_namer(info)
    player = with_sig(player_context(status, current, info))
    # As estações da fila (a lista que está a tocar), na ordem da playlist
    stations = [{"id": s["id"], "file": s["file"], "name": display_name(s)} for s in queue]
    # A página de uma playlist: cartões com filtro e ordenação. Na que está a tocar
    # vêm da fila (com o id do MPD); nas outras, do ficheiro
    cards, list_filter, list_order, list_url = [], "", "", "/"
    if view is not None:
        cards, list_filter, list_order = station_cards(stations if view["live"] else view["entries"],
                                                       player["current_id"] if view["live"] else None)
        # Para onde voltam os cartões depois de tocar ou remover: esta página com o
        # mesmo filtro, e nunca /search ou /tema, que voltariam a pedir tudo à API
        list_params = {k: v for k, v in (("filtro", list_filter), ("ordenar", list_order)) if v}
        list_url = view["url"] + (("&" if "?" in view["url"] else "?") + urllib.parse.urlencode(list_params)
                                  if list_params else "")
    _, counts, covers = station_index()
    playlists = [{"name": p, "count": counts.get(p, 0), "cover": covers.get(p, [])} for p in stored_playlists]
    # A estação que falhou: a que o vigia desistiu de religar, ou pelo URL na
    # mensagem de erro do MPD ("Failed to decode http://...")
    failure = stream_failure()
    error = status.get("error", "")
    failed = next((s for s in stations if (failure and s["file"] == failure["url"])
                   or (error and s["file"] in error)), None)
    stream_error = failure["text"] if failure else error
    # Menu do + nos resultados de Descobrir: as playlists para onde se pode juntar,
    # cada uma com os URLs que já tem (✓). Primeiro a do "para" (veio do "Juntar
    # rádios" de uma playlist), depois a que está a tocar (valor vazio: a fila)
    para = chosen_playlist()
    active = active_playlist()
    members = playlist_members()
    add_targets = [{"value": "", "label": active or "Lista por guardar", "playing": True,
                    "urls": {s["file"] for s in queue}}]
    add_targets += [{"value": p, "label": p, "playing": False, "urls": members.get(p, set())}
                    for p in stored_playlists if p != active]
    add_targets.sort(key=lambda t: t["value"] != para if para else not t["playing"])
    saved_urls = set().union(*(t["urls"] for t in add_targets))
    return render_template(template, **player, page=page, tab=tab,
                                  stations=stations, cards=cards, add_targets=add_targets, saved_urls=saved_urls, para=para, view=view, queue_sig=queue_sig(queue),
                                  list_filter=list_filter, list_order=list_order, list_url=list_url,
                                  list_orders=LIST_ORDERS,
                                  playlists=playlists,
                                  results=results, query=query, filters=filters or search_filters(),
                                  themes=THEMES, countries=COUNTRIES, orders=ORDERS, languages=LANGUAGES,
                                  bitrates=BITRATES, theme=theme, dashboard=dashboard,
                                  pages=pages or {"page": 1, "more": False},
                                  failed=failed, stream_error=stream_error, stream_lost=bool(failure),
                                  failed_name=failed["name"] if failed else (failure or {}).get("name"),
                                  removed=removed_context(),
                                  config=config_context() if can_configure() else None)

def tunnel_host():
    try:
        with open(TUNNEL_URL_FILE, encoding="utf-8") as f:
            return urllib.parse.urlsplit(f.read().strip()).hostname or ""
    except FileNotFoundError:
        return ""

def known_host(hostname):
    # DNS rebinding: uma página de outro domínio que passe a apontar para o IP do
    # telemóvel chega aqui com o Host desse domínio. Só se aceita localhost, um IP
    # (na rede local, http://<IP>:8080) e o túnel; os nomes *.trycloudflare.com
    # são da Cloudflare e não podem apontar para a rede local
    if hostname in ("localhost", tunnel_host()) or hostname.endswith(".trycloudflare.com"):
        return True
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False

def is_local():
    # Só o browser do próprio telemóvel (http://localhost:8080). Pelo túnel o pedido
    # também chega de 127.0.0.1, mas a Cloudflare junta sempre o Cf-Connecting-IP
    # (quem o tenta mandar vê-o substituído) e o cloudflared o X-Forwarded-For
    if any(h in request.headers for h in ("Cf-Connecting-IP", "Cf-Ray", "X-Forwarded-For")):
        return False
    hostname = urllib.parse.urlsplit("//" + request.host).hostname or ""
    return request.remote_addr in ("127.0.0.1", "::1") and hostname in ("localhost", "127.0.0.1", "::1")

def config_key():
    try:
        with open(CONFIG_KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def ensure_config_key():
    if config_key():
        return
    os.makedirs(os.path.dirname(CONFIG_KEY_FILE), exist_ok=True)
    try:
        fd = os.open(CONFIG_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with open(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(secrets.token_urlsafe(24) + "\n")

def same_key(given, key):
    return bool(key) and hmac.compare_digest(given.encode("utf-8"), key.encode("utf-8"))

def can_configure():
    return is_local() and same_key(request.cookies.get(CONFIG_COOKIE, ""), config_key())

@app.before_request
def check_origin():
    hostname = urllib.parse.urlsplit("//" + request.host).hostname or ""
    if not known_host(hostname):
        return error_page("Endereço desconhecido", "Abre o comando pelo link do túnel ou pelo IP do telemóvel.", 400)
    if request.method != "POST":
        return None
    # CSRF: uma página de outro site, aberta por alguém na mesma rede ou com o
    # link, não pode carregar nos botões. O Sec-Fetch-Site é a resposta do próprio
    # browser; os browsers que não o mandam mandam o Origin, que tem de ser este
    # endereço ou o do túnel (o cloudflared pode reescrever o Host para localhost)
    site = request.headers.get("Sec-Fetch-Site")
    if site:
        allowed = site in ("same-origin", "none")
    else:
        origin = request.headers.get("Origin")
        allowed = origin is None or urllib.parse.urlsplit(origin).netloc in (request.host, tunnel_host())
    if not allowed:
        return error_page("Pedido recusado", "Este pedido veio de outro site.", 403)
    return None

# Pedidos feitos pelo static/app.js. Sem JavaScript tudo continua a funcionar com
# formulários e redirects; com ele, a página atualiza-se sem recarregar.
def from_script():
    return request.headers.get("X-Requested-With") == "fetch"

# Os ficheiros estáticos levam o hash no URL (asset_url): podem ficar um ano em cache
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 365 * 24 * 3600
_asset_hashes = {}

@app.template_global()
def asset_url(filename):
    path = os.path.join(app.static_folder, filename)
    sig = file_sig(path)
    if _asset_hashes.get(filename, (None,))[0] != sig:
        with open(path, "rb") as f:
            _asset_hashes[filename] = (sig, hashlib.sha1(f.read()).hexdigest()[:10])
    return url_for("static", filename=filename, v=_asset_hashes[filename][1])

@app.template_global()
def icon(name, cls=""):
    # Um ícone do static/icons.svg. O endereço do ficheiro (com o hash) só se
    # calcula uma vez por pedido: a lista pode ter centenas de ícones
    if "icons_url" not in g:
        g.icons_url = asset_url("icons.svg")
    classes = "i " + cls if cls else "i"
    return Markup(f'<svg class="{html.escape(classes)}" aria-hidden="true" focusable="false">'
                  f'<use href="{g.icons_url}#{html.escape(name)}"></use></svg>')

COMPRESSIBLE = ("text/html", "application/json", "image/svg+xml", "text/css", "text/javascript", "application/javascript")

@app.after_request
def compress(response):
    # A página com 500 estações tem ~470 KB e comprimida ~30 KB. Pesa na rede local
    # e entre o telemóvel e a Cloudflare, que só comprime daí para a frente
    if (response.status_code != 200 or response.direct_passthrough or "Content-Encoding" in response.headers
            or response.mimetype not in COMPRESSIBLE or "gzip" not in request.headers.get("Accept-Encoding", "")):
        if response.mimetype in COMPRESSIBLE:
            response.vary.add("Accept-Encoding")
        return response
    data = response.get_data()
    if len(data) < 1024:
        return response
    response.set_data(gzip.compress(data, compresslevel=5))
    response.headers["Content-Encoding"] = "gzip"
    response.vary.add("Accept-Encoding")
    return response

@app.after_request
def redirect_for_script(response):
    # O fetch segue os redirects sozinho e perde o #janela do destino; para o
    # script, o redirect passa a um cabeçalho e é ele que pede a página nova
    if from_script() and response.status_code in (301, 302, 303):
        location, notice = response.headers["Location"], response.headers.get("X-Notice")
        response = Response(status=204)
        response.headers["X-Location"] = location
        if notice:
            response.headers["X-Notice"] = notice
    return response

@app.route("/estado")
def estado(notice=None):
    # Estado do reprodutor para o script: a barra já feita em HTML (o mesmo
    # template da página) e o que é preciso para saber se a página mudou.
    # O notice é uma mensagem curta para o script mostrar (por exemplo, depois de votar)
    with mpd_client() as c:
        status = c.status()
        current = watched_song(c, c.currentsong())
        queue = c.playlistinfo()
    player = with_sig(player_context(status, current, station_info()))
    # O polling manda o sig da barra que tem: se for igual, não se volta a fazer
    html_player = None if request.args.get("sig") == player["sig"] else render_template("player.html", **player)
    failure = stream_failure()
    return jsonify(player=html_player, sig=player["sig"], id=player["current_id"],
                   station=player["current_station"], playing=status.get("state") == "play",
                   queue=queue_sig(queue), error=status.get("error", "") or (failure or {}).get("text", ""),
                   failed=(failure or {}).get("text", ""), notice=notice)

def back():
    # Volta à página de onde veio o formulário (resultados da pesquisa, lista
    # filtrada); só aceita caminhos locais. Os browsers tratam "\" como "/", por
    # isso "/\site.com" seria um link para outro site
    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url \
            and not CONTROL_RE.search(next_url):
        return redirect(next_url)
    return redirect("/")

def with_notice(response, text):
    # Mensagem curta para o script mostrar depois de seguir o redirect (por exemplo,
    # para onde foi a rádio juntada). Vai em URL-encoding porque os cabeçalhos são
    # ASCII; sem script não aparece, e a página mostra o ✓
    response.headers["X-Notice"] = urllib.parse.quote(text)
    return response

def plural(n, one, many):
    return f"{n} {one if n == 1 else many}"

def done():
    # Os botões do reprodutor devolvem o estado ao script, em vez da página inteira
    return estado() if from_script() else back()

HIGHLIGHTS = 6

@app.route("/")
def index():
    # Início: o que está a tocar, as playlists, as ouvidas recentemente e os
    # destaques das rádios guardadas. Os destaques usam só os números do
    # radio-browser já guardados (stats.json): o Início não espera pela API
    info = station_info()
    recent = [{**h, "name": info.get(h["url"], {}).get("name") or h.get("name") or h["url"],
               "key": logo_key(h["url"])} for h in read_history()[:12]]
    stats = load_stats()
    mine = [(url, stats[url]) for url in info if url in stats]
    # Os números em falta ou com mais de um dia vêm em segundo plano: aparecem na visita seguinte
    now = time.time()
    stale = [url for url in info if stats_stale(stats.get(url, {}), now)]
    if stale:
        refresh_stats_later(stale)

    def top(field, positive=False):
        ranked = sorted(((e.get(field) or 0, url) for url, e in mine), reverse=True)
        return [{"url": url, "name": info[url].get("name") or url, "logo": info[url].get("logo"),
                 "key": logo_key(url), "stat": n} for n, url in ranked[:HIGHLIGHTS] if n > 0 or not positive]

    return render_page("home.html", "inicio", dashboard={
        "recent": recent, "popular": top("clickcount", True), "trending": top("clicktrend", True)})

# Descobrir: pesquisa por nome, temas e países são páginas, com os resultados na mesma página
@app.route("/descobrir")
def discover():
    return render_page("discover.html", "descobrir", tab="nome")

@app.route("/descobrir/temas")
def discover_themes():
    return render_page("discover.html", "descobrir", tab="temas")

@app.route("/descobrir/paises")
def discover_countries():
    return render_page("discover.html", "descobrir", tab="paises")

@app.route("/playlists")
def playlists_page():
    return render_page("playlists.html", "playlists")

@app.route("/logo/<key>")
def logo(key):
    # Descarrega o logótipo uma vez e guarda-o no telemóvel. Só procura streams que
    # já estão nas playlists ou na fila, para o link público não servir para pôr
    # o telemóvel a pedir endereços quaisquer. Sem logótipo, mostra as iniciais.
    name = request.args.get("n", "")
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        return initials_response(name)
    path = os.path.join(LOGO_DIR, key)
    try:
        with open(path, "rb") as f:
            data = f.read()
        # Logótipos guardados antes de haver redução: reduz-se uma vez, ao servir
        if len(data) > LOGO_SHRINK_ABOVE:
            smaller = shrink_logo(data)
            if smaller is not data:
                write_logo(path, smaller)
                data = smaller
        return image_response(data)
    except FileNotFoundError:
        pass
    failed = path + ".falhou"
    if os.path.exists(failed) and time.time() - os.path.getmtime(failed) < LOGO_RETRY_SECONDS:
        return initials_response(name)
    info = station_info()
    stream_url = next((u for u in info if logo_key(u) == key), None)
    if stream_url is None:
        with mpd_client() as c:
            stream_url = next((s["file"] for s in c.playlistinfo() if logo_key(s["file"]) == key), None)
    if stream_url is None:
        return initials_response(name)
    os.makedirs(LOGO_DIR, exist_ok=True)
    transient = False
    preview = read_preview()
    known_logo = info.get(stream_url, {}).get("logo")
    if not known_logo and preview and preview["url"] == stream_url:
        known_logo = preview.get("logo")
    try:
        logo_url = known_logo or lookup_logo(stream_url)
        data = fetch_public(logo_url, LOGO_MAX_BYTES) if logo_url else b""
    except (OSError, ValueError) as e:
        data = b""
        # Sem rede ou com o servidor em baixo volta a tentar daqui a uma hora; um
        # 404 ou uma resposta que não é imagem só amanhã
        transient = isinstance(e, OSError) and not (isinstance(e, urllib.error.HTTPError) and e.code < 500)
    if len(data) > LOGO_MAX_BYTES or not image_type(data):
        open(failed, "w").close()
        if transient:
            retry_at = time.time() - LOGO_RETRY_SECONDS + STATS_RETRY_SECONDS
            os.utime(failed, (retry_at, retry_at))
        return initials_response(name)
    data = shrink_logo(data)
    write_logo(path, data)
    return image_response(data)

def write_logo(path, data):
    # Ficheiro à parte e troca de uma vez: outro pedido ao mesmo tempo nunca serve
    # (e deixa o browser guardar por um dia) uma imagem a meio
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

# Os cartões têm até ~150 px; 256 chega para ecrãs de alta densidade. Muitos
# favicons vêm com 512 ou 1024 px e centenas de KB
LOGO_SIZE = 256
LOGO_SHRINK_ABOVE = 24 * 1024

def shrink_logo(data):
    # Devolve o mesmo objeto quando não reduz (sem Pillow, SVG, já pequeno ou erro)
    if Image is None or len(data) <= LOGO_SHRINK_ABOVE or image_type(data) in (None, "image/svg+xml"):
        return data
    try:
        with Image.open(io.BytesIO(data)) as im:
            if max(im.size) <= LOGO_SIZE and image_type(data) != "image/x-icon":
                return data
            im.thumbnail((LOGO_SIZE, LOGO_SIZE))
            out = io.BytesIO()
            im.convert("RGBA").save(out, "PNG", optimize=True)
    except Exception:
        # Imagem estranha ou demasiado grande para o Pillow: fica a original
        return data
    smaller = out.getvalue()
    return smaller if len(smaller) < len(data) else data

def image_response(data):
    r = Response(data, mimetype=image_type(data) or "application/octet-stream")
    r.headers["Cache-Control"] = "public, max-age=86400"
    # Um SVG de fora aberto diretamente não pode correr scripts nesta origem
    r.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r

def initials_response(name):
    r = Response(initials_svg(name), mimetype="image/svg+xml")
    r.headers["Cache-Control"] = "public, max-age=3600"
    return r

# Juntar uma rádio a partir da pesquisa ou de um tema volta à mesma página para
# mostrar o ✓, o que repetia o pedido ao radio-browser (até 1000 rádios) por cada
# rádio juntada. Os resultados ficam uns minutos em memória
SEARCH_CACHE_SECONDS = 300
SEARCH_CACHE_SIZE = 24
_search_cache = {}
_search_lock = threading.Lock()

def page_number():
    page = request.args.get("pag", "")
    return int(page) if page.isdigit() and 1 <= int(page) <= MAX_PAGE else 1

@app.template_global()
def page_url(page):
    # Este endereço noutra página de resultados (a 1 sem o pag)
    args = request.args.to_dict(flat=False)
    args.pop("pag", None)
    if page > 1:
        args["pag"] = [str(page)]
    return request.path + ("?" + urllib.parse.urlencode(args, doseq=True) if args else "")

def find_stations(filters, page, **criteria):
    # Uma página de resultados: (rádios, há mais páginas)
    key = (tuple(sorted(filters.items())), page, tuple(sorted(criteria.items())))
    now = time.time()
    with _search_lock:
        hit = _search_cache.get(key)
        if hit and now - hit[0] < SEARCH_CACHE_SECONDS:
            return list(hit[1]), hit[2]
    results, more = search_stations(filters, page, **criteria)
    with _search_lock:
        _search_cache[key] = (now, results, more)
        for old in sorted(_search_cache, key=lambda k: _search_cache[k][0])[:-SEARCH_CACHE_SIZE]:
            del _search_cache[old]
    return list(results), more

# Quantas entradas antes da página se veem para tirar as rádios que já apareceram
DEDUP_LOOKBACK = 1000

def api_search(filters, offset, limit, **criteria):
    # Diretório público de rádios (radio-browser.info); o "all" encaminha para um servidor ativo
    params = {"limit": limit, "offset": offset, "hidebroken": "true", "order": filters["ordem"],
              "reverse": "false" if filters["ordem"] == "name" else "true", **criteria}
    if filters["pais"]:
        params["countrycode"] = filters["pais"]
    if filters["lingua"]:
        params["language"] = filters["lingua"]
    if filters["kbps"]:
        params["bitrateMin"] = filters["kbps"]
    url = API + "/json/stations/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "vee-radio/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        stations = json.load(r)
    if not isinstance(stations, list):
        raise ValueError("resposta inesperada do radio-browser")
    return stations

def search_stations(filters, page, **criteria):
    # Cada página são PAGE_SIZE entradas da API; os repetidos e os HLS ficam de
    # fora e as versões da mesma rádio juntam-se, por isso mostram-se um pouco menos.
    # Há mais páginas quando a API devolveu a página inteira
    start = (page - 1) * PAGE_SIZE
    stations = api_search(filters, start, PAGE_SIZE, **criteria)
    results = group_versions(clean_stations(stations))
    # A API põe as versões da mesma rádio onde calha, às vezes em páginas
    # diferentes: saem as que já apareceram nas páginas anteriores (até
    # DEDUP_LOOKBACK entradas para trás, num só pedido). Nas aleatórias cada
    # pedido é outro sorteio, por isso não há anteriores a comparar
    if start and filters["ordem"] != "random":
        first = max(0, start - DEDUP_LOOKBACK)
        try:
            before = clean_stations(api_search(filters, first, start - first, **criteria))
        except (OSError, ValueError):
            before = []  # sem a comparação, a página aparece na mesma
        seen = {group_key(s["name"], s["countrycode"]) for s in before}
        # Uma entrada sem país é da rádio com esse nome que já apareceu, e vice-versa;
        # com países diferentes são rádios diferentes ("Rock Radio" CZ e PL)
        seen_names = {name for name, _ in seen}
        countryless = {name for name, country in seen if not country}
        def repeated(r):
            name, country = group_key(r["name"], r["countrycode"])
            return (name, country) in seen or name in countryless or (not country and name in seen_names)
        results = [r for r in results if not repeated(r)]
    if filters["ordem"] == "name":
        # A API ordena os nomes tal como estão, com espaços e pontuação à frente
        # (" M80", ". Abdulbasit") e maiúsculas antes de minúsculas
        results.sort(key=lambda s: sort_key(s["name"]))
    return results, len(stations) >= PAGE_SIZE

def clean_stations(stations):
    # A mesma rádio aparece muitas vezes repetida com o mesmo URL. Os streams HLS
    # (.m3u8) ficam de fora porque o MPD nem sempre os consegue tocar; quase todas
    # as rádios têm também um stream normal.
    seen, results = set(), []
    for s in stations:
        # Os campos podem vir a null: sem URL a rádio fica de fora, o resto fica vazio
        if not isinstance(s, dict):
            continue
        url = s.get("url_resolved") or ""
        if not safe_url(url) or s.get("hls") or ".m3u8" in url or url in seen:
            continue
        seen.add(url)
        s["url_resolved"] = url
        if not LOGO_URL_RE.match(s.get("favicon") or ""):
            s["favicon"] = ""
        s["name"] = " ".join(str(s.get("name") or "").split())
        s["tags"] = str(s.get("tags") or "")
        s["stationuuid"] = s.get("stationuuid") or ""
        s["codec"] = str(s.get("codec") or "")
        s["bitrate"] = to_int(s.get("bitrate"))
        s["countrycode"] = str(s.get("countrycode") or "").upper()
        results.append(s)
    return results

# Versões da mesma rádio: no radio-browser a mesma rádio aparece várias vezes, com
# streams diferentes (MP3 128, AAC 64...). Os resultados mostram uma linha por rádio,
# com a melhor versão à frente, e as outras ficam guardadas para trocar no reprodutor
# e para tentar outra quando uma não toca. A mesma rádio é o mesmo nome (sem
# acentos nem marcas de qualidade como "128k" ou "AAC") no mesmo país: "Radio 1"
# de países diferentes não se juntam
QUALITY_WORDS_RE = re.compile(r"\b(\d{2,3}\s*k(bps|b)?|aac\+?|he-?aac|mp3|ogg|opus|flac|hq|lq|hd)\b", re.I)
# Quanto rende cada kbps: um AAC a 64 soa como um MP3 a 128
CODEC_FACTOR = {"MP3": 1.0, "AAC": 1.6, "AAC+": 1.8, "HE-AAC": 1.8, "OGG": 1.3, "OPUS": 1.9, "FLAC": 4.0}
VERSION_LABELS = {"AAC+": "AAC+", "HE-AAC": "AAC+"}

def group_key(name, country):
    text = QUALITY_WORDS_RE.sub(" ", plain_text(name or ""))
    return " ".join(re.sub(r"[^\w]+", " ", text).split()), (country or "").upper()

def quality(station):
    # Bitrate desconhecido conta como 64; os cliques só desempatam
    codec = str(station.get("codec") or "").upper()
    kbps = to_int(station.get("bitrate")) or 64
    return (kbps * CODEC_FACTOR.get(codec, 1.0), to_int(station.get("clickcount")))

def version_label(v):
    codec = str(v.get("codec") or "").upper()
    codec = VERSION_LABELS.get(codec, codec) or "?"
    return f"{codec} · {v['bitrate']} kbps" if v.get("bitrate") else codec

def group_versions(stations):
    # Uma entrada por rádio (a melhor versão, pela ordem da primeira que aparece),
    # com todas as versões em "variants", da melhor para a pior
    groups, order = {}, []
    for st in stations:
        key = group_key(st["name"], st.get("countrycode"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(st)
    results = []
    for key in order:
        members = sorted(groups[key], key=quality, reverse=True)
        best = dict(members[0])
        # O nome é o da primeira que aparece (a mais ouvida, com a ordenação por
        # omissão), que costuma ser o nome limpo, sem "AAC" nem "128k"
        best["name"] = groups[key][0]["name"]
        if not best["favicon"]:
            best["favicon"] = next((m["favicon"] for m in members if m["favicon"]), "")
        best["variants"] = [{"url": m["url_resolved"], "codec": m["codec"], "bitrate": m["bitrate"],
                             "uuid": m["stationuuid"], "label": version_label(m)} for m in members]
        results.append(best)
    return results

# Versões conhecidas de cada stream: URL -> {"t", "alts": [{url, codec, bitrate, uuid, label}]},
# com a mesma lista em todos os URLs da rádio. Vêm dos resultados da pesquisa (ao
# juntar ou ouvir) ou, para as rádios já guardadas, de uma procura em segundo plano
VERSIONS_FILE = os.path.expanduser("~/.cache/vee-radio/versoes.json")
VERSIONS_MAX_AGE = 7 * 24 * 3600
VERSIONS_LOCK = threading.Lock()
_versions_cache = {"key": None, "data": {}}

def load_versions():
    # Partilhado: só para ler
    key = file_sig(VERSIONS_FILE)
    if _versions_cache["key"] != key:
        try:
            with open(VERSIONS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            data = {}
        _versions_cache.update(key=key, data=data if isinstance(data, dict) else {})
    return _versions_cache["data"]

def save_versions(alts):
    # Guarda a lista em todos os URLs da rádio; as entradas de URLs que já não
    # estão em nenhuma playlist saem ao fim de um mês, como os números
    now = time.time()
    with VERSIONS_LOCK:
        try:
            with open(VERSIONS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            data = {}
        known = station_info()
        data = {u: e for u, e in data.items() if u in known or now - e.get("t", 0) < STATS_KEEP_SECONDS}
        for v in alts:
            data[v["url"]] = {"t": now, "alts": alts}
        os.makedirs(os.path.dirname(VERSIONS_FILE), exist_ok=True)
        tmp = VERSIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, VERSIONS_FILE)

def remember_versions(urls):
    # Ao juntar ou ouvir a partir dos resultados: as versões estão na pesquisa em memória
    urls = set(urls)
    with _search_lock:
        cached = [r for _, results, _ in _search_cache.values() for r in results]
    for r in cached:
        alts = r.get("variants") or []
        if len(alts) > 1 and urls & {v["url"] for v in alts}:
            save_versions(alts)
            urls -= {v["url"] for v in alts}

_versions_refreshing = set()

def station_versions(url, name, country):
    # Versões da rádio deste stream, só do que está guardado: o /estado não pode
    # esperar pela API. Se faltarem ou estiverem velhas, procura-as em segundo plano
    entry = load_versions().get(url)
    if entry and time.time() - entry.get("t", 0) < VERSIONS_MAX_AGE:
        return entry["alts"]
    if name and country:
        with _refreshing_lock:
            fresh = url not in _versions_refreshing
            _versions_refreshing.add(url)
        if fresh:
            threading.Thread(target=lookup_versions, args=(url, name, country), daemon=True).start()
    return entry["alts"] if entry else []

def lookup_versions(url, name, country):
    try:
        # Procura pelo nome como está (com acentos), só sem as marcas de qualidade
        query = " ".join(QUALITY_WORDS_RE.sub(" ", name).split()) or name
        found = clean_stations(api_stations("/json/stations/search", name=query, countrycode=country,
                                            hidebroken="true", limit=40))
        key = group_key(name, country)
        same = [st for st in found if group_key(st["name"], st["countrycode"]) == key]
        if url not in {st["url_resolved"] for st in same}:
            # O stream guardado não está no radio-browser (ou mudou): fica só ele
            same = []
        group = group_versions(same)
        alts = group[0]["variants"] if group else [{"url": url, "codec": "", "bitrate": 0, "uuid": "", "label": "?"}]
        save_versions(alts)
    except (OSError, ValueError):
        pass
    finally:
        with _refreshing_lock:
            _versions_refreshing.discard(url)

def plain_text(text):
    # Sem acentos nem maiúsculas, para comparar e ordenar nomes. Tem de dar o mesmo
    # que o plain() do static/app.js (NFKD, sem \p{M}, toLowerCase), senão o filtro
    # da lista mostra estações diferentes ao abrir a página e ao escrever
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.category(ch).startswith("M")).lower()

def sort_key(name):
    return re.sub(r"^\W+", "", plain_text(name))

API_DOWN = ("Pesquisa indisponível", "Não foi possível contactar o radio-browser.info. Tenta outra vez.", 502)

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    filters = search_filters()
    if not query:
        return render_page("discover.html", "descobrir", filters=filters, tab="nome")
    try:
        results, more = find_stations(filters, page_number(), name=query)
    except (OSError, ValueError):
        # Apanhado aqui para não cair no handler de OSError, que culpa o MPD
        return error_page(*API_DOWN)
    return render_page("discover.html", "descobrir", results, query, filters, tab="nome",
                       pages={"page": page_number(), "more": more})

@app.route("/tema")
def theme():
    # Um tema, um país ou os dois juntos ("Rock (Portugal)"); o nome é também o da playlist nova
    tag = request.args.get("t", "")
    filters = search_filters()
    if tag and tag not in THEME_LABELS:
        return redirect("/descobrir/temas")
    if not tag and not filters["pais"]:
        return redirect("/descobrir/paises")
    criteria = {"tag": tag, "tagExact": "true"} if tag else {}
    if tag and filters["pais"]:
        label = f"{THEME_LABELS[tag]} ({COUNTRY_LABELS[filters['pais']]})"
    else:
        label = THEME_LABELS.get(tag) or COUNTRY_LABELS[filters["pais"]]
    try:
        results, more = find_stations(filters, page_number(), **criteria)
    except (OSError, ValueError):
        return error_page(*API_DOWN)
    return render_page("discover.html", "descobrir", filters=filters, tab="tema", theme={"tag": tag, "label": label, "results": results},
                       pages={"page": page_number(), "more": more})

@app.route("/add_theme", methods=["POST"])
def add_theme():
    # Junta as rádios mostradas de um tema a uma playlist. Os URLs vêm do formulário
    # (o que a pessoa viu) em vez de se pedir outra vez a lista à API.
    label = request.form.get("label", "").strip()
    target = request.form.get("target", "").strip() or label
    path = playlist_path(target)
    if path is None:
        return error_page(*BAD_NAME)
    logos = [l if LOGO_URL_RE.match(l) else None for l in request.form.getlist("logo")]
    picked = list(zip(request.form.getlist("url"), request.form.getlist("name"), logos))
    remember_uuids(zip(request.form.getlist("url"), request.form.getlist("uuid")))
    remember_versions(request.form.getlist("url"))
    with QUEUE_LOCK, mpd_client() as c:
        existed = os.path.isfile(path)
        entries = read_m3u(path)
        known = {u for u, _, _ in entries}
        new = [(u, clean_name(n) or None, l) for u, n, l in picked if safe_url(u) and u not in known]
        if target == active_playlist():
            # A playlist está a tocar: junta à fila e sincroniza. O ficheiro é escrito
            # antes para a sincronização encontrar os nomes e logótipos das novas.
            # Todas num só pedido ao MPD, em vez de uma ida e volta por rádio.
            # A que se estava a experimentar já está na fila: passa a ser da lista
            preview = read_preview()
            trying = preview["url"] if preview and preview["url"] in {u for u, _, _ in new} else None
            if trying:
                set_preview(None)
            to_add = [u for u, _, _ in new if u != trying]
            if to_add:
                c.command_list_ok_begin()
                for url in to_add:
                    c.add(url)
                c.command_list_end()
            write_m3u(path, entries + new)
            sync_active(c)
            return with_notice(redirect(view_url(target)), joined_text(len(new), target))
        os.makedirs(PLAYLIST_DIR, exist_ok=True)
        write_m3u(path, entries + new)
        if existed:
            return with_notice(redirect(view_url(target)), joined_text(len(new), target))
        # Playlist nova: passa a ser a que toca, como em "Nova playlist",
        # e a rádio que está a tocar continua
        if not switch_queue(c, target) and new:
            c.play(0)
    return with_notice(redirect(view_url(target)),
                       f"Playlist «{target}» criada com {plural(len(new), 'rádio', 'rádios')}.")

def joined_text(n, target):
    if not n:
        return f"Já estavam todas em «{target}»."
    return f"{plural(n, 'rádio juntada', 'rádios juntadas')} a «{target}»."

# Os cartões usam o id da entrada na fila e não a posição: se a lista mudou
# noutro aparelho depois de a página ser feita, a posição já é de outra estação
@app.route("/play_id/<int:song_id>", methods=["POST"])
def play_id(song_id):
    with QUEUE_LOCK, mpd_client() as c:
        c.playid(song_id)
        drop_preview(c, keep_url=c.currentsong().get("file"))
    return done()

def step(c, delta):
    # Estação anterior ou seguinte, dando a volta no fim da lista; parado, ou a
    # experimentar uma rádio, começa pela primeira. A que se experimenta não conta
    ids = [s["id"] for s in listed(c.playlistinfo())]
    if not ids:
        return
    current = watched_song(c, c.currentsong()).get("id")
    c.playid(ids[(ids.index(current) + delta) % len(ids)] if current in ids else ids[0])
    drop_preview(c)

@app.route("/previous", methods=["POST"])
def previous():
    with QUEUE_LOCK, mpd_client() as c:
        step(c, -1)
    return done()

@app.route("/next", methods=["POST"])
def next_station():
    with QUEUE_LOCK, mpd_client() as c:
        step(c, +1)
    return done()

def replace_stream(c, song, new_url):
    # Troca o stream de uma entrada da fila por outra versão da mesma rádio, no mesmo
    # sítio, e toca-a. Nome e logótipo ficam os da estação (o URL novo ainda não
    # está em nenhum .m3u); a que se estava a experimentar continua experiência
    old = song["file"]
    known = station_info().get(old, {})
    preview = read_preview()
    new_id = c.addid(new_url, int(song["pos"]))
    try:
        c.playid(new_id)
    except CommandError:
        pass  # a versão nova não ligou: o vigia do stream volta a tentar
    c.deleteid(song["id"])
    if preview and preview["url"] == old:
        set_preview({**preview, "url": new_url})
    else:
        sync_active(c, {new_url: (known.get("name") or song.get("name"), known.get("logo"))})
    return str(new_id)

@app.route("/versao", methods=["POST"])
def change_version():
    # Outra versão (bitrate, codec) da estação a tocar, escolhida no reprodutor
    url = request.form.get("url", "").strip()
    with QUEUE_LOCK, mpd_client() as c:
        song = c.currentsong()
        alts = load_versions().get(song.get("file"), {}).get("alts", [])
        version = next((v for v in alts if v["url"] == url), None)
        if not version or not safe_url(url):
            return error_page("Versão desconhecida", "Essa versão não é desta rádio.", 400)
        if url != song["file"]:
            remember_uuids([(url, version.get("uuid", ""))])
            replace_stream(c, song, url)
    return done()

# Versões já tentadas por rádio (URL que falhou -> {URLs tentados}), para não andar
# às voltas: se nenhuma tocar fica o aviso. Esquecem-se ao fim de 10 minutos
FALLBACK_SECONDS = 600
_fallback = {}

def untried_version(song):
    # A versão seguinte da rádio que ainda não se tentou, ou None
    alts = load_versions().get(song["file"], {}).get("alts", [])
    group = tuple(sorted(v["url"] for v in alts))
    now = time.time()
    for key in [k for k, (t, _) in _fallback.items() if now - t > FALLBACK_SECONDS]:
        del _fallback[key]
    tried = _fallback.setdefault(group, (now, set()))[1]
    tried.add(song["file"])
    nxt = next((v for v in alts if v["url"] not in tried), None)
    if nxt:
        tried.add(nxt["url"])
    return nxt

# Vigia do stream. Quando a ligação cai a meio, o MPD não dá erro: salta sozinho
# para a estação seguinte da fila, ou para se era a última; quando não liga e há
# mais estações, também salta. O vigia corre numa thread, mesmo sem ninguém com a
# página aberta: volta a pôr a estação escolhida e tenta outra vez, até
# len(RETRY_DELAYS) vezes seguidas, à espera de cada intervalo (a rede pode estar
# a voltar). Só conta como recuperada depois de STABLE_SECONDS a tocar, porque um
# stream que liga e volta a cair também é uma tentativa falhada. Esgotadas as
# tentativas, passa para outra versão da rádio, se houver; senão para e avisa
RETRY_DELAYS = (1, 3, 5, 10, 15)
STABLE_SECONDS = 15
WATCH_INTERVAL = 1
VERSION_NOTICE_SECONDS = 30
# O que o reprodutor mostra: {"url", "id", "attempt"} a voltar a ligar, {"url", "id",
# "failed", "name", "text"} depois de falhar, {"url", "text", "until"} depois de
# mudar de versão
_watch = {}

def watched_song(c, current):
    # A estação que o vigia está a religar, ou que falhou, é a atual para o
    # reprodutor e os botões, mesmo que o cursor do MPD tenha ficado na seguinte
    song_id = _watch.get("id")
    if not song_id or current.get("id") == song_id:
        return current
    try:
        return c.playlistid(song_id)[0]
    except (CommandError, IndexError):
        return current

def watch_view(url):
    # O estado do vigia, se é da estação atual e ainda vale
    view = _watch
    if not url or view.get("url") != url or view.get("until", float("inf")) < time.time():
        return {}
    return view

def stream_failure():
    # A estação que deixou de tocar e não voltou, ou None
    return _watch if _watch.get("failed") else None

def failure_text(name):
    return f"«{name}» deixou de tocar: o stream falhou {len(RETRY_DELAYS)} vezes seguidas." if name \
        else f"O stream falhou {len(RETRY_DELAYS)} vezes seguidas."

def station_name(song):
    preview = read_preview()
    return (station_info().get(song["file"], {}).get("name")
            or (preview["name"] if preview and preview["url"] == song["file"] else None)
            or song.get("name") or "")

def replay(c, song_id):
    # O play do MPD pode logo dar erro quando o stream não liga; a volta seguinte
    # do vigia vê-o parado (ou noutra estação) e conta a tentativa
    try:
        c.playid(song_id)
    except CommandError:
        pass

def watch_step(c, st):
    # Uma volta do vigia. st guarda o que se vigia: wanted (id da estação que devia
    # estar a tocar), attempts (tentativas seguidas) e retry_at (próxima tentativa)
    global _watch
    with _choice_lock:
        seq, chosen = _choice["seq"], _choice["id"]
    status = c.status()
    state, song_id = status.get("state"), status.get("songid")
    if seq != st["seq"]:
        # Escolha nova (tocar, parar, outra estação): é essa que se vigia
        st.update(seq=seq, wanted=chosen, attempts=0, retry_at=None)
        _watch = {}
        return
    wanted = st["wanted"]
    if wanted is None:
        # A tocar sem escolha registada: ao arrancar, ou pelo mpc
        if state == "play":
            st.update(wanted=song_id, attempts=0, retry_at=None)
            _watch = {}
        return
    if state == "pause":
        return
    if state == "play" and song_id == wanted:
        try:
            elapsed = float(status.get("elapsed") or 0)
        except ValueError:
            elapsed = 0
        if st["attempts"] and elapsed >= STABLE_SECONDS:
            st["attempts"] = 0
            if _watch.get("attempt"):
                _watch = {}
        return
    song = next((s for s in c.playlistinfo() if s["id"] == wanted), None)
    if song is None:
        # Saiu da fila (removida noutro sítio): passa a vigiar a que toca agora
        st.update(wanted=song_id if state == "play" else None, attempts=0, retry_at=None)
        _watch = {}
        return
    if st["retry_at"] is not None:
        if time.time() >= st["retry_at"]:
            st["retry_at"] = None
            replay(c, wanted)
        return
    # O stream caiu ou não ligou. Se o MPD passou para outra estação, para-a: o
    # reprodutor, o ▶ e o ⏮ ⏭ usam a que se está a religar (watched_song)
    if state == "play":
        c.stop()
    if st["attempts"] < len(RETRY_DELAYS):
        st["retry_at"] = time.time() + RETRY_DELAYS[st["attempts"]]
        st["attempts"] += 1
        _watch = {"url": song["file"], "id": wanted, "attempt": st["attempts"]}
        return
    name = station_name(song)
    nxt = untried_version(song)
    if nxt:
        remember_uuids([(nxt["url"], nxt.get("uuid", ""))])
        st.update(wanted=replace_stream(c, song, nxt["url"]), attempts=0, retry_at=None)
        _watch = {"url": nxt["url"], "until": time.time() + VERSION_NOTICE_SECONDS,
                  "text": f"Não tocou nessa versão: a tocar em {nxt['label']}."}
        return
    c.stop()
    st.update(wanted=None, attempts=0, retry_at=None)
    _watch = {"url": song["file"], "id": wanted, "failed": True, "name": name, "text": failure_text(name)}

def watch_stream():
    # Ligação própria ao MPD (sem registar as escolhas), aberta outra vez quando o
    # MPD reinicia. As mudanças à fila fazem-se com o QUEUE_LOCK, como nas rotas
    while True:
        try:
            with mpd_client(MPDClient) as c:
                with _choice_lock:
                    st = {"seq": _choice["seq"], "wanted": None, "attempts": 0, "retry_at": None}
                while True:
                    with QUEUE_LOCK:
                        watch_step(c, st)
                    time.sleep(WATCH_INTERVAL)
        except Exception as e:  # o vigia não pode morrer: volta a ligar e continua
            print(f"vigia do stream: {e!r}", flush=True)
            time.sleep(5)

@app.route("/ouvir", methods=["POST"])
def listen():
    # Toca uma rádio dos resultados sem a juntar à lista. Fica no fim da fila até
    # se tocar outra estação ou se carregar em "+ Juntar" no reprodutor
    url = request.form.get("url", "").strip()
    name = clean_name(request.form.get("name", ""))
    logo = request.form.get("logo", "").strip()
    if not safe_url(url):
        return error_page("Endereço inválido", "O endereço do stream tem de começar por http:// ou https://.", 400)
    remember_uuids([(url, request.form.get("uuid", ""))])
    remember_versions([url])
    with QUEUE_LOCK, mpd_client() as c:
        song = next((s for s in c.playlistinfo() if s["file"] == url), None)
        if song:
            # Já está na lista (ou já se está a experimentar): só a toca
            c.playid(song["id"])
            drop_preview(c, keep_url=url)
        else:
            song_id = c.addid(url)
            c.playid(song_id)
            drop_preview(c)
            set_preview({"url": url, "name": name or None, "logo": logo if LOGO_URL_RE.match(logo) else None})
    return done()

@app.route("/vote", methods=["POST"])
def vote():
    # Vota no radio-browser na estação que está a tocar. O voto conta para o IP
    # do telemóvel, que só pode votar na mesma rádio uma vez a cada 10 minutos
    with mpd_client() as c:
        url = c.currentsong().get("file")
    entry = load_stats().get(url, {}) if url else {}
    uuid = entry.get("uuid") or ""
    if not UUID_RE.match(uuid):
        notice = {"ok": False, "text": "Esta rádio não está no radio-browser.info."}
    else:
        try:
            answer = api_get("/json/vote/" + uuid)
        except (OSError, ValueError):
            answer = {}
        if answer.get("ok"):
            merge_stats({url: {**entry, "votes": entry.get("votes", 0) + 1, "voted_at": time.time()}})
            notice = {"ok": True, "text": "Voto registado no radio-browser.info."}
        elif "often" in str(answer.get("message", "")):
            notice = {"ok": False, "text": "Já votaste nesta rádio há pouco. Só se pode votar uma vez a cada 10 minutos."}
        else:
            notice = {"ok": False, "text": "Não foi possível votar. Tenta outra vez."}
    return estado(notice) if from_script() else back()

@app.route("/play", methods=["POST"])
def play():
    with mpd_client() as c:
        # Depois de o stream falhar, o ▶ tenta outra vez essa estação
        song = watched_song(c, c.currentsong())
        if song.get("id"):
            c.playid(song["id"])
        else:
            c.play()
    return done()

@app.route("/stop", methods=["POST"])
def stop():
    with mpd_client() as c:
        c.stop()
    return done()

@app.route("/volup", methods=["POST"])
def volup():
    with mpd_client() as c:
        c.volume(+5)
    return done()

@app.route("/voldown", methods=["POST"])
def voldown():
    with mpd_client() as c:
        c.volume(-5)
    return done()

@app.route("/add_stream", methods=["POST"])
def add_stream():
    url = request.form.get("url", "").strip()
    name = clean_name(request.form.get("name", ""))
    logo = request.form.get("logo", "").strip()
    if not LOGO_URL_RE.match(logo):
        logo = ""
    if url and not safe_url(url):
        return error_page("Endereço inválido", "O endereço do stream tem de começar por http:// ou https://, "
                          "não pode ter quebras de linha e tem de ter menos de 2048 caracteres.", 400)
    # "Nova playlist" no menu do +: cria-a já com esta rádio, sem a pôr a tocar
    new_list = request.form.get("nova_playlist", "").strip() if request.form.get("criar") else None
    if new_list is not None and playlist_path(new_list) is None:
        return error_page(*BAD_NAME)
    if url:
        remember_uuids([(url, request.form.get("uuid", ""))])
        remember_versions([url])
        with QUEUE_LOCK:
            # O nome vai para o ficheiro antes de sincronizar, para a playlist ficar com ele.
            # Só se escreve se mudar alguma coisa: o ficheiro é lido a cada pedido e
            # não pode crescer com cada vez que se junta a mesma rádio
            known = {u: (n, l) for u, n, l in read_m3u(NAMES_FILE)}
            if (name or logo) and known.get(url) != (name or None, logo or None):
                with open(NAMES_FILE, "a", encoding="utf-8", newline="\n") as f:
                    f.write(extinf(name, logo) + url + "\n")
            if logo:
                # Pode ter falhado antes, sem logótipo; agora há um para tentar
                try:
                    os.remove(os.path.join(LOGO_DIR, logo_key(url) + ".falhou"))
                except FileNotFoundError:
                    pass
            label = f"«{name}»" if name else "A rádio"
            with mpd_client() as c:
                if new_list is not None:
                    created = not os.path.isfile(playlist_path(new_list))
                    os.makedirs(PLAYLIST_DIR, exist_ok=True)
                    added = append_to_playlist(c, new_list, [(url, name or None, logo or None)])
                    if created:
                        return with_notice(back(), f"Playlist «{new_list}» criada com {label if name else 'a rádio'}.")
                    # Já existia (outro aparelho criou-a entretanto): junta-se como a qualquer outra
                    return with_notice(back(), f"{label} {'juntada a' if added else 'já estava em'} «{new_list}».")
                target = chosen_playlist()
                if target:
                    # Para outra playlist: só o ficheiro, a música não muda
                    if append_to_playlist(c, target, [(url, name or None, logo or None)]):
                        return with_notice(back(), f"{label} juntada a «{target}».")
                    return with_notice(back(), f"{label} já estava em «{target}».")
                # A rádio que se estava a experimentar já está na fila: deixa de ser
                # experiência e passa a ser da lista, no mesmo sítio
                preview = read_preview()
                promoted = bool(preview and preview["url"] == url)
                if promoted:
                    set_preview(None)
                # Dois toques em "+ Rádio" (ou dois aparelhos) não a juntam duas vezes
                added = url not in {s["file"] for s in c.playlistinfo()}
                if added:
                    c.add(url)
                if added or promoted:
                    sync_active(c)
                where = f"«{active_playlist()}»" if active_playlist() else "à lista que está a tocar"
                if added or promoted:
                    return with_notice(back(), f"{label} juntada a {where}.")
                return with_notice(back(), f"{label} já estava em {where}.")
    return back()

@app.route("/remove/<int:song_id>", methods=["POST"])
def remove(song_id):
    with QUEUE_LOCK, mpd_client() as c:
        queue = listed(c.playlistinfo())
        pos = next((i for i, s in enumerate(queue) if s["id"] == str(song_id)), None)
        # O nome e o logótipo lidos antes: a sincronização tira-os da playlist
        known = station_info().get(queue[pos]["file"], {}) if pos is not None else {}
        try:
            c.deleteid(song_id)
        except CommandError:
            # Já tinha sido removida (noutro aparelho, ou com dois toques no ✕)
            pos = None
        # Removeu-se a rádio do aviso "não tocou": o aviso vai com ela
        if pos is not None and queue[pos]["file"] in c.status().get("error", ""):
            c.clearerror()
        sync_active(c)
        if pos is not None:
            song = queue[pos]
            write_state(REMOVED_FILE, {"url": song["file"], "name": known.get("name") or display_namer({})(song),
                                       "logo": known.get("logo"), "pos": pos, "playlist": active_playlist(),
                                       "t": int(time.time())})
    return back()

@app.route("/anular", methods=["POST"])
def undo_remove():
    # Volta a pôr a última estação removida no mesmo sítio da lista
    with QUEUE_LOCK, mpd_client() as c:
        removed = read_removed()
        if removed:
            url = removed["url"]
            queue = c.playlistinfo()
            if url not in {s["file"] for s in queue}:
                shown = listed(queue)
                pos = to_int(removed.get("pos"))
                c.addid(url, int(shown[pos]["pos"]) if pos < len(shown) else len(queue))
            else:
                # Entretanto estava a ser experimentada: passa a ser da lista
                preview = read_preview()
                if preview and preview["url"] == url:
                    set_preview(None)
            logo = removed.get("logo")
            sync_active(c, {url: (removed.get("name"), logo if LOGO_URL_RE.match(logo or "") else None)})
            write_state(REMOVED_FILE, None)
    return back()

def view_url(name, **extra):
    return "/playlist?" + urllib.parse.urlencode({"nome": name, **extra})

# Mensagem depois de editar uma playlist: (singular, plural)
EDIT_DONE = {"copiadas": ("copiada para", "copiadas para"), "movidas": ("movida para", "movidas para"),
             "removidas": ("removida", "removidas")}

def queue_url():
    # A página da lista que está a tocar: a playlist ativa, ou a fila por guardar
    name = active_playlist()
    return view_url(name) if name else "/playlist"

@app.route("/playlist")
def view_playlist():
    # Uma playlist em cartões. A que está a tocar vem da fila do MPD; as outras, do
    # ficheiro, e tocar num cartão põe-nas a tocar a partir dessa estação.
    # Sem nome: a lista que está a tocar, ou a fila quando não está guardada
    name = request.args.get("nome", "").strip()
    active = active_playlist()
    if not name and active:
        keep = {k: v for k, v in request.args.items() if k in ("filtro", "ordenar")}
        return redirect(view_url(active, **keep))
    entries = []
    if name:
        path = playlist_path(name)
        if path is None or not os.path.isfile(path):
            return redirect("/playlists")
        if name != active:
            info = station_info()
            entries = [{"id": None, "file": url, "name": title or info.get(url, {}).get("name") or url}
                       for url, title, _ in read_m3u(path)]
    # Mensagem depois de copiar, mover ou remover (o redirect traz o que se fez)
    done = request.args.get("feito", "")
    notice = None
    if done in EDIT_DONE:
        count = to_int(request.args.get("n"))
        notice = f"✓ {count} {'estação' if count == 1 else 'estações'} {EDIT_DONE[done][count != 1]}"
        if done != "removidas":
            notice += f" «{request.args.get('destino', '')}»"
            skipped = to_int(request.args.get("ja"))
            if skipped:
                notice += f" ({skipped} já lá {'estava' if skipped == 1 else 'estavam'})"
        notice += "."
    live = name == active
    return render_page("playlist.html", "playlists", view={"name": name, "entries": entries, "notice": notice,
                       "live": live, "url": view_url(name) if name else "/playlist"})

@app.route("/tocar_em", methods=["POST"])
def play_in():
    # Cartão de uma playlist que não está a tocar: passa a tocar essa playlist, a
    # começar nessa estação (como no Spotify); ⏮ ⏭ passam a seguir esta playlist
    name = request.form.get("nome", "").strip()
    url = request.form.get("url", "").strip()
    path = playlist_path(name)
    if path is None or not os.path.isfile(path):
        return error_page("Playlist inexistente", "Essa playlist já não existe.", 404)
    with QUEUE_LOCK, mpd_client() as c:
        if name != active_playlist():
            switch_queue(c, name)
        song = next((s for s in listed(c.playlistinfo()) if s["file"] == url), None)
        # A que já está a tocar não recomeça
        if song and not (c.status().get("state") == "play" and c.currentsong().get("id") == song["id"]):
            c.playid(song["id"])
            drop_preview(c, keep_url=url)
    return back()

@app.route("/playlist_edit", methods=["POST"])
def edit_playlist():
    # Copiar, mover ou remover as estações escolhidas de uma playlist, esteja ou não a tocar
    name = request.form.get("nome", "").strip()
    path = playlist_path(name)
    if path is None or not os.path.isfile(path):
        return error_page("Playlist inexistente", "Essa playlist já não existe.", 404)
    action = request.form.get("acao", "")
    urls = {u for u in request.form.getlist("url") if safe_url(u)}
    with QUEUE_LOCK, mpd_client() as c:
        picked = [e for e in read_m3u(path) if e[0] in urls]
        if not picked or action not in ("copiar", "mover", "remover"):
            return redirect(view_url(name))
        extra = {"n": len(picked)}
        if action in ("copiar", "mover"):
            dest = request.form.get("destino", "").strip()
            dest_path = playlist_path(dest)
            if dest_path is None or not os.path.isfile(dest_path) or dest == name:
                return error_page("Playlist inexistente", "Escolhe outra playlist para onde copiar ou mover.", 400)
            added = append_to_playlist(c, dest, picked)
            extra.update(destino=dest, ja=len(picked) - added)
        if action in ("mover", "remover"):
            remove_from_playlist(c, name, urls)
    extra["feito"] = {"copiar": "copiadas", "mover": "movidas", "remover": "removidas"}[action]
    # O ✕ de um cartão volta à lista com o mesmo filtro e ordenação
    keep = {k: v for k, v in urllib.parse.parse_qsl(urllib.parse.urlsplit(request.form.get("next", "")).query)
            if k in ("filtro", "ordenar")}
    return redirect(view_url(name, **keep, **extra))

@app.route("/rename_playlist", methods=["POST"])
def rename_playlist():
    name = request.form.get("nome", "").strip()
    new = request.form.get("novo", "").strip()
    path, new_path = playlist_path(name), playlist_path(new)
    if path is None or not os.path.isfile(path):
        return error_page("Playlist inexistente", "Essa playlist já não existe.", 404)
    if new_path is None:
        return error_page(*BAD_NAME)
    if new == name:
        return redirect(view_url(name))
    with QUEUE_LOCK:
        if os.path.exists(new_path):
            return error_page("Playlist existente", f"Já existe uma playlist «{new}». Escolhe outro nome.", 400)
        was_active = name == active_playlist()
        os.rename(path, new_path)
        if was_active:
            set_active(new)
    return redirect(view_url(new))

def switch_queue(c, name):
    # Põe na fila a playlist name (que passa a ser a ativa) sem cortar a rádio que
    # está a tocar: o clear do MPD parava-a. Apagam-se as outras entradas e a que
    # toca fica: se estiver na playlist nova, passa a ser essa estação (no lugar
    # dela); se não estiver, fica como rádio a experimentar ("+ Juntar"), até se
    # tocar outra. Devolve True se ficou uma a tocar
    # Sem playlist ativa enquanto a fila está a meio: se o load falhar, a próxima
    # rádio juntada não reescreve a playlist anterior só com o que lá ficou
    set_active("")
    status = c.status()
    current = c.currentsong() if status.get("state") in ("play", "pause") else {}
    keep_id, url = current.get("id"), current.get("file")
    if not keep_id:
        c.clear()
        set_preview(None)
    else:
        preview = read_preview()
        if not (preview and preview["url"] == url):
            known = station_info().get(url, {})
            preview = {"url": url, "name": known.get("name") or current.get("name"), "logo": known.get("logo")}
        others = [s["id"] for s in c.playlistinfo() if s["id"] != keep_id]
        if others:
            c.command_list_ok_begin()
            for song_id in others:
                c.deleteid(song_id)
            c.command_list_end()
        set_preview(preview)
    path = playlist_path(name)
    if read_m3u(path):
        c.load(name)
    if keep_id:
        queue = c.playlistinfo()
        twin = next((s for s in queue if s["file"] == url and s["id"] != keep_id), None)
        if twin:
            # Fica a que já toca, no lugar da cópia que veio com a playlist
            pos = int(twin["pos"])
            c.deleteid(twin["id"])
            c.moveid(keep_id, pos - 1)
            set_preview(None)
        elif len(queue) > 1:
            c.moveid(keep_id, len(queue) - 1)
    set_active(name)
    return bool(keep_id)

@app.route("/load_playlist", methods=["POST"])
def load_playlist():
    name = request.form.get("playlist_name", "").strip()
    path = playlist_path(name)
    if path is None or not os.path.isfile(path):
        return error_page("Playlist inexistente", "Essa playlist já não existe.", 404)
    with QUEUE_LOCK, mpd_client() as c:
        # A rádio que está a tocar continua; parado, começa pela primeira (uma
        # playlist vazia não tem posição 0 para tocar)
        if not switch_queue(c, name) and c.status().get("playlistlength", "0") != "0":
            c.play(0)
    return redirect(view_url(name))

@app.route("/create_playlist", methods=["POST"])
def create_playlist():
    name = request.form.get("playlist_name", "").strip()
    path = playlist_path(name)
    if path is None:
        return error_page(*BAD_NAME)
    with QUEUE_LOCK:
        if os.path.exists(path):
            return error_page("Playlist existente", f"Já existe uma playlist «{name}». Escolhe outro nome.", 400)
        os.makedirs(PLAYLIST_DIR, exist_ok=True)
        with mpd_client() as c:
            if request.form.get("from") == "atuais":
                write_m3u(path, queue_entries(c))
            else:
                # A rádio que está a tocar continua, como a experimentar
                write_m3u(path, [])
                switch_queue(c, name)
        set_active(name)
    return redirect(view_url(name))

@app.route("/delete_playlist", methods=["POST"])
def delete_playlist():
    name = request.form.get("playlist_name", "").strip()
    path = playlist_path(name)
    if path is None:
        return error_page(*BAD_NAME)
    with QUEUE_LOCK:
        was_active = name == active_playlist()
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        if was_active:
            set_active("")
    return redirect("/playlists")

# Configurações (.env). Lidas como o env_value do start.sh: KEY=valor, a última
# ocorrência ganha e tiram-se as aspas das pontas
ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
NTFY_TOPIC_RE = re.compile(r"^(https?://[^\s\"'<>]+|[A-Za-z0-9_-]{1,64})$")
NTFY_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
NTFY_MIN_TOPIC = 16
TUNNEL_OFF = ("0", "false", "off", "no", "nao", "não")

def read_env():
    values = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        m = ENV_LINE_RE.match(line.replace("\r", ""))
        if m:
            value = m.group(2)
            if value[:1] in "\"'":
                value = value[1:]
            if value[-1:] in "\"'":
                value = value[:-1]
            values[m.group(1)] = value
    return values

def write_env(updates):
    # Muda só as chaves pedidas e mantém os comentários; sem .env, parte do .env.example
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        try:
            with open(ENV_EXAMPLE, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            lines = []
    out, done = [], set()
    for line in lines:
        m = ENV_LINE_RE.match(line.replace("\r", ""))
        key = m.group(1) if m else None
        if key in updates:
            if key not in done:
                out.append(f"{key}={updates[key]}")
                done.add(key)
            continue
        out.append(line.replace("\r", ""))
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    tmp = ENV_FILE + ".tmp"
    # O .env tem o token do ntfy: só o Termux o pode ler
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, ENV_FILE)

def tunnel_enabled(values):
    return values.get("TUNNEL_ENABLED", "1").strip().lower() not in TUNNEL_OFF

def weak_ntfy_topic(topic, token):
    # A mesma regra do start.sh: no ntfy.sh público, sem token, quem adivinhar o tópico recebe o link
    if not topic or token:
        return False
    if topic.startswith(("http://", "https://")):
        if urllib.parse.urlsplit(topic).hostname != "ntfy.sh":
            return False
    return len(topic.rstrip("/").rsplit("/", 1)[-1]) < NTFY_MIN_TOPIC

def config_context():
    values = read_env()
    return {"tunnel": tunnel_enabled(values), "topic": values.get("NTFY_TOPIC_URL", ""),
            "has_token": bool(values.get("NTFY_TOKEN")), "suggestion": "radio-" + secrets.token_hex(8),
            "saved": request.args.get("config") == "guardada", "version": app_version(),
            "up_to_date": request.args.get("atualizacao") == "nada"}

_version = None

def app_version():
    # Só muda com um reinício (o update.sh reinicia depois de atualizar), por isso lê-se uma vez
    global _version
    if _version is None:
        try:
            _version = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)), "log", "-1",
                                       "--format=%h, %cd", "--date=format:%d/%m/%Y"], capture_output=True,
                                      text=True, timeout=10, stdin=subprocess.DEVNULL).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            _version = ""
    return _version

@app.route("/entrar")
def enter():
    # Dá ao browser do telemóvel a chave das Configurações; o link vem do Termux
    # (~/config.sh ou o start.sh sem túnel), que é o único que lê a chave
    if not (is_local() and same_key(request.args.get("chave", ""), config_key())):
        return error_page("Chave inválida", "Abre as configurações a partir do Termux, com ~/config.sh.", 403)
    response = redirect("/#config" if request.args.get("ir") == "config" else "/")
    # Lax e não Strict: o link chega de outra app (termux-open-url), e o browser trata
    # essa navegação como vinda de outro site; com Strict o cookie não ia no
    # redirecionamento a seguir e a página abria sem as Configurações. Os POST de
    # outros sites já são recusados pelo check_origin
    response.set_cookie(CONFIG_COOKIE, config_key(), max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
    return response

def restart_all(script=START_SCRIPT):
    # Volta a correr o start.sh (ou o update.sh, que o corre no fim) numa sessão à
    # parte: ele termina este servidor, e assim não morre com ele. Os 2 segundos
    # deixam a resposta chegar ao browser
    log = open(RESTART_LOG, "ab")
    subprocess.Popen(["bash", "-c", 'sleep 2; exec bash "$0"', script], stdin=subprocess.DEVNULL,
                     stdout=log, stderr=log, start_new_session=True, close_fds=True,
                     env={**os.environ, "VEE_RADIO_NO_OPEN": "1"})
    log.close()

@app.route("/config", methods=["POST"])
def config():
    if not can_configure():
        return error_page("Sem acesso", "As configurações só se mudam no próprio telemóvel: abre-as no Termux com ~/config.sh.", 403)
    topic = request.form.get("ntfy_topic", "").strip()
    new_token = request.form.get("ntfy_token", "").strip()
    old_token = read_env().get("NTFY_TOKEN", "")
    token = "" if request.form.get("clear_token") else new_token or old_token
    if topic and not NTFY_TOPIC_RE.match(topic):
        return error_page("Tópico inválido", "O tópico do ntfy é um nome só com letras, números, - e _ (até 64), "
                          "ou o endereço completo de um servidor (https://...).", 400)
    if new_token and not NTFY_TOKEN_RE.match(new_token):
        return error_page("Token inválido", "O token do ntfy só tem letras, números, ponto, - e _.", 400)
    if weak_ntfy_topic(topic, token):
        return error_page("Tópico fácil de adivinhar", f"No ntfy.sh, sem token, o tópico tem de ter pelo menos "
                          f"{NTFY_MIN_TOPIC} caracteres: quem o souber recebe o link e controla a rádio.", 400)
    write_env({"TUNNEL_ENABLED": "1" if request.form.get("tunnel") else "0",
               "NTFY_TOPIC_URL": topic, "NTFY_TOKEN": token})
    if not request.form.get("aplicar"):
        return redirect("/?config=guardada#config")
    restart_all()
    if request.form.get("tunnel"):
        link = ("O link público muda: o novo chega pelo ntfy." if topic
                else "O link público muda: abre-se a página para o partilhar.")
    else:
        link = "Sem túnel, o comando fica só neste telemóvel e na rede local."
    response = Response(render_template("error.html", title="A reiniciar…",
                                        message=f"A música para durante alguns segundos. {link} Esta página volta sozinha."), 202)
    # Sem JavaScript o browser volta à página sozinho; com ele, o app.js faz o mesmo
    response.headers["Refresh"] = "12; url=/"
    return response

@app.route("/atualizar", methods=["POST"])
def update():
    if not can_configure():
        return error_page("Sem acesso", "Só se atualiza no próprio telemóvel: abre as configurações no Termux com ~/config.sh.", 403)
    # Primeiro só se vê se há novidades, para não parar a música à toa
    try:
        check = subprocess.run(["bash", UPDATE_SCRIPT, "--check"], capture_output=True, text=True,
                               timeout=60, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return error_page("Sem resposta", "O GitHub não respondeu a tempo. Tenta outra vez daqui a pouco.", 504)
    if check.returncode == 3:
        return redirect("/?atualizacao=nada#config")
    if check.returncode != 0:
        reason = check.stdout.strip().splitlines()[-1:] or ["O update.sh falhou; vê o ~/restart.log."]
        return error_page("Não foi possível atualizar", reason[0], 502)
    # Se o install.sh mudou, o update.sh corre-o (atualiza os pacotes) antes de reiniciar
    install = "instalar" in check.stdout.split()
    restart_all(UPDATE_SCRIPT)
    wait = "alguns minutos, porque também se atualizam as dependências" if install else "alguns segundos"
    response = Response(render_template("error.html", title="A atualizar…",
                                        message=f"A música para durante {wait}. Esta página volta sozinha."), 202)
    response.headers["Refresh"] = f"{240 if install else 20}; url=/"
    return response

if __name__ == "__main__":
    ensure_config_key()
    threading.Thread(target=watch_stream, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
