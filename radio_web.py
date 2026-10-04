import hashlib
import hmac
import html
import http.client
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

from flask import Flask, Request, Response, jsonify, render_template, request, redirect
from mpd import CommandError, MPDClient, MPDError

class BigFormRequest(Request):
    # Com "Todas", o "Juntar todas" de um tema manda centenas de rádios, com 3
    # campos cada; o limite do Werkzeug é de 1000 campos por formulário
    max_form_parts = 5000

app = Flask(__name__)
app.request_class = BigFormRequest
# "Juntar todas" com ALL_LIMIT rádios fica bem abaixo disto; acima é abuso
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
# Ordenações da API (campo order, do maior para o menor); "random" serve para descobrir rádios novas
# "name" mostra todas, por ordem alfabética, até ALL_LIMIT
ORDERS = [("clickcount", "mais ouvidas"), ("votes", "mais votadas"), ("clicktrend", "tendências"), ("random", "aleatórias"),
          ("name", "todas, de A a Z")]
ORDER_LABELS = dict(ORDERS)
ALL_LIMIT = 500
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
# Quantas rádios de um tema se mostram e se juntam de uma vez (as mais ouvidas)
THEME_LIMIT = 15

STREAM_FAILED_HINT ='O endereço pode ter mudado. Procura a rádio outra vez em "Juntar rádio" e remove a antiga.'

STATES = {"play": "A tocar", "pause": "Em pausa", "stop": "Parado"}

@contextmanager
def mpd_client():
    # Abre uma ligação ao MPD e garante que é fechada mesmo que um comando falhe
    client = MPDClient()
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

def station_info():
    # URL -> {"name", "logo"}, juntando as playlists guardadas e o ficheiro de
    # nomes; cada ficheiro sobrepõe-se aos anteriores
    paths = []
    if os.path.isdir(PLAYLIST_DIR):
        paths += [os.path.join(PLAYLIST_DIR, f) for f in sorted(os.listdir(PLAYLIST_DIR)) if f.endswith(".m3u")]
    info = {}
    for path in paths + [NAMES_FILE]:
        for url, name, logo in read_m3u(path):
            entry = info.setdefault(url, {"name": None, "logo": None})
            if name:
                entry["name"] = name
            if logo:
                entry["logo"] = logo
    return info

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

def load_stats():
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}

def save_stats(stats):
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, f)
    os.replace(tmp, STATS_FILE)

def merge_stats(updates):
    # Grava entradas novas mantendo o momento do último voto
    with STATS_LOCK:
        stats = load_stats()
        for url, entry in updates.items():
            voted = stats.get(url, {}).get("voted_at")
            stats[url] = {**entry, "voted_at": voted} if voted and "voted_at" not in entry else entry
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

def queue_entries(c):
    # O save do MPD só escreve os URLs, por isso as playlists são escritas aqui para manter os nomes
    info = station_info()
    entries = []
    for s in c.playlistinfo():
        known = info.get(s["file"], {})
        entries.append((s["file"], known.get("name") or s.get("name"), known.get("logo")))
    return entries

def sync_active(c):
    name = active_playlist()
    if name:
        write_m3u(playlist_path(name), queue_entries(c))

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
    return {"status": status, "state": STATES.get(status.get("state"), status.get("state")),
            "details": details, "kbps": kbps,
            "voted": time.time() - (details.get("voted_at") or 0) < VOTE_COOLDOWN,
            "current_station": display_name(current) if current.get("file") else "",
            "current_title": clean_title(current.get("title", "")),
            "current_key": logo_key(current["file"]) if current.get("file") else "",
            "current_pos": current.get("pos") if playing else None,
            "volume": volume, "has_stations": status.get("playlistlength", "0") != "0"}

def queue_sig(queue):
    # Muda só quando se juntam, removem ou trocam estações. A versão da fila do
    # MPD (status.playlist) também muda quando o stream a tocar muda de música,
    # e o script recarregava a página a cada música
    return hashlib.sha1(",".join(s["id"] for s in queue).encode()).hexdigest()[:12]

def render_index(results=None, query="", filters=None, theme=None):
    with mpd_client() as c:
        status = c.status()
        current = c.currentsong()
        queue = c.playlistinfo()
        stored_playlists = sorted(p['playlist'] for p in c.listplaylists())

    info = station_info()
    display_name = display_namer(info)
    player = player_context(status, current, info)
    # Filtro e ordenação da lista: as estações escondidas vão na página com hidden,
    # para o script as poder mostrar enquanto se escreve, sem pedir outra vez
    list_filter = request.args.get("filtro", "").strip()
    list_order = request.args.get("ordenar", "")
    if list_order not in dict(LIST_ORDERS):
        list_order = ""
    # Para onde voltam os cartões depois de tocar ou remover: a lista com o mesmo
    # filtro, e nunca /search ou /tema, que voltariam a pedir tudo à API
    list_params = {k: v for k, v in (("filtro", list_filter), ("ordenar", list_order)) if v}
    list_url = "/" + ("?" + urllib.parse.urlencode(list_params) if list_params else "")
    wanted = plain_text(list_filter)
    stations = [{"pos": s["pos"], "id": s["id"], "file": s["file"], "name": display_name(s), "key": logo_key(s["file"]),
                 "current": s["pos"] == player["current_pos"]} for s in queue]
    for s in stations:
        s["hidden"] = wanted not in plain_text(s["name"])
    if list_order in POPULAR_ORDERS:
        stats = station_stats([s["file"] for s in stations])
        for s in stations:
            s["stat"] = stats.get(s["file"], {}).get(list_order, 0)
        # sort estável: em caso de empate fica a ordem da playlist
        stations.sort(key=lambda s: -s["stat"])
    elif list_order in ("name", "-name"):
        stations.sort(key=lambda s: sort_key(s["name"]), reverse=list_order == "-name")
    playlists = [{"name": p, "count": len(read_m3u(playlist_path(p) or ""))} for p in stored_playlists]
    return render_template("index.html", **player,
                                  stations=stations, queue_urls={s["file"] for s in queue}, queue_sig=queue_sig(queue),
                                  list_filter=list_filter, list_order=list_order, list_url=list_url,
                                  list_orders=LIST_ORDERS,
                                  playlists=playlists, active=active_playlist(),
                                  results=results, query=query, filters=filters or search_filters(),
                                  themes=THEMES, countries=COUNTRIES, orders=ORDERS, languages=LANGUAGES,
                                  bitrates=BITRATES, theme=theme,
                                  hint=STREAM_FAILED_HINT, config=config_context() if can_configure() else None)

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

@app.after_request
def redirect_for_script(response):
    # O fetch segue os redirects sozinho e perde o #janela do destino; para o
    # script, o redirect passa a um cabeçalho e é ele que pede a página nova
    if from_script() and response.status_code in (301, 302, 303):
        location = response.headers["Location"]
        response = Response(status=204)
        response.headers["X-Location"] = location
    return response

@app.route("/estado")
def estado(notice=None):
    # Estado do reprodutor para o script: a barra já feita em HTML (o mesmo
    # template da página) e o que é preciso para saber se a página mudou.
    # O notice é uma mensagem curta para o script mostrar (por exemplo, depois de votar)
    with mpd_client() as c:
        status = c.status()
        current = c.currentsong()
        queue = c.playlistinfo()
    player = player_context(status, current, station_info())
    return jsonify(player=render_template("player.html", **player), pos=player["current_pos"],
                   station=player["current_station"], playing=status.get("state") == "play",
                   queue=queue_sig(queue), error=status.get("error", ""), notice=notice)

def back():
    # Volta à página de onde veio o formulário (resultados da pesquisa, lista
    # filtrada); só aceita caminhos locais. Os browsers tratam "\" como "/", por
    # isso "/\site.com" seria um link para outro site
    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url \
            and not CONTROL_RE.search(next_url):
        return redirect(next_url)
    return redirect("/")

def done():
    # Os botões do reprodutor devolvem o estado ao script, em vez da página inteira
    return estado() if from_script() else back()

@app.route("/")
def index():
    return render_index()

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
            return image_response(f.read())
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
    try:
        logo_url = info.get(stream_url, {}).get("logo") or lookup_logo(stream_url)
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
    # Ficheiro à parte e troca de uma vez: outro pedido ao mesmo tempo nunca serve
    # (e deixa o browser guardar por um dia) uma imagem a meio
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return image_response(data)

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

def find_stations(filters, limit, **criteria):
    # Diretório público de rádios (radio-browser.info); o "all" encaminha para um servidor ativo.
    # Pede mais do que o limite porque os repetidos e os HLS ficam de fora.
    if filters["ordem"] == "name":
        limit = ALL_LIMIT
    params = {"limit": limit * 2, "hidebroken": "true", "order": filters["ordem"],
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
    # A mesma rádio aparece muitas vezes repetida com o mesmo URL. Os streams HLS
    # (.m3u8) ficam de fora porque o MPD nem sempre os consegue tocar; quase todas
    # as rádios têm também um stream normal.
    seen, results = set(), []
    if not isinstance(stations, list):
        raise ValueError("resposta inesperada do radio-browser")
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
        results.append(s)
    if filters["ordem"] == "name":
        # A API ordena os nomes tal como estão, com espaços e pontuação à frente
        # (" M80", ". Abdulbasit") e maiúsculas antes de minúsculas
        results.sort(key=lambda s: sort_key(s["name"]))
    return results[:limit]

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
        return redirect("/#juntar")
    try:
        results = find_stations(filters, 25, name=query)
    except (OSError, ValueError):
        # Apanhado aqui para não cair no handler de OSError, que culpa o MPD
        return error_page(*API_DOWN)
    return render_index(results, query, filters)

@app.route("/tema")
def theme():
    # Um tema, um país ou os dois juntos ("Rock (Portugal)"); o nome é também o da playlist nova
    tag = request.args.get("t", "")
    filters = search_filters()
    if tag and tag not in THEME_LABELS:
        return redirect("/#temas")
    if not tag and not filters["pais"]:
        return redirect("/#paises")
    criteria = {"tag": tag, "tagExact": "true"} if tag else {}
    if tag and filters["pais"]:
        label = f"{THEME_LABELS[tag]} ({COUNTRY_LABELS[filters['pais']]})"
    else:
        label = THEME_LABELS.get(tag) or COUNTRY_LABELS[filters["pais"]]
    try:
        results = find_stations(filters, THEME_LIMIT, **criteria)
    except (OSError, ValueError):
        return error_page(*API_DOWN)
    return render_index(filters=filters, theme={"tag": tag, "label": label, "results": results})

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
    with QUEUE_LOCK, mpd_client() as c:
        existed = os.path.isfile(path)
        entries = read_m3u(path)
        known = {u for u, _, _ in entries}
        new = [(u, clean_name(n) or None, l) for u, n, l in picked if safe_url(u) and u not in known]
        if target == active_playlist():
            # A playlist está a tocar: junta à fila e sincroniza. O ficheiro é escrito
            # antes para a sincronização encontrar os nomes e logótipos das novas.
            for url, _, _ in new:
                c.add(url)
            write_m3u(path, entries + new)
            sync_active(c)
            return redirect("/")
        os.makedirs(PLAYLIST_DIR, exist_ok=True)
        write_m3u(path, entries + new)
        if existed:
            return redirect("/#playlists")
        # Playlist nova: passa a ser a da página principal, como em "Nova playlist".
        # Deixa de haver playlist ativa antes de esvaziar a fila: se o load falhar,
        # a próxima rádio juntada não pode reescrever a playlist antiga com a fila vazia
        set_active("")
        c.clear()
        c.load(target)
        if new:
            c.play(0)
        set_active(target)
    return redirect("/")

# Os cartões usam o id da entrada na fila e não a posição: se a lista mudou
# noutro aparelho depois de a página ser feita, a posição já é de outra estação
@app.route("/play_id/<int:song_id>", methods=["POST"])
def play_id(song_id):
    with mpd_client() as c:
        c.playid(song_id)
    return done()

def step(c, delta):
    # Estação anterior ou seguinte, dando a volta no fim da lista; parado, começa pela primeira
    length = int(c.status().get("playlistlength", "0"))
    if not length:
        return
    pos = c.currentsong().get("pos")
    c.play((int(pos) + delta) % length if pos is not None else 0)

@app.route("/previous", methods=["POST"])
def previous():
    with mpd_client() as c:
        step(c, -1)
    return done()

@app.route("/next", methods=["POST"])
def next_station():
    with mpd_client() as c:
        step(c, +1)
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
    if url:
        remember_uuids([(url, request.form.get("uuid", ""))])
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
            with mpd_client() as c:
                # Dois toques em "+ Rádio" (ou dois aparelhos) não a juntam duas vezes
                if url not in {s["file"] for s in c.playlistinfo()}:
                    c.add(url)
                    sync_active(c)
    return back()

@app.route("/remove/<int:song_id>", methods=["POST"])
def remove(song_id):
    with QUEUE_LOCK, mpd_client() as c:
        try:
            c.deleteid(song_id)
        except CommandError:
            # Já tinha sido removida (noutro aparelho, ou com dois toques no ✕)
            pass
        sync_active(c)
    return back()

@app.route("/load_playlist", methods=["POST"])
def load_playlist():
    name = request.form.get("playlist_name", "").strip()
    path = playlist_path(name)
    if path is None or not os.path.isfile(path):
        return error_page("Playlist inexistente", "Essa playlist já não existe.", 404)
    with QUEUE_LOCK, mpd_client() as c:
        # Sem playlist ativa enquanto a fila está vazia: se o load falhar, a próxima
        # rádio juntada não reescreve a playlist anterior só com ela
        set_active("")
        c.clear()
        c.load(name)
        # Uma playlist vazia não tem posição 0 para tocar
        if c.status().get("playlistlength", "0") != "0":
            c.play(0)
        set_active(name)
    return redirect("/")

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
                write_m3u(path, [])
                set_active("")
                c.clear()
        set_active(name)
    return redirect("/")

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
    return redirect("/#playlists")

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
            "saved": request.args.get("config") == "guardada"}

@app.route("/entrar")
def enter():
    # Dá ao browser do telemóvel a chave das Configurações; o link vem do Termux
    # (~/config.sh ou o start.sh sem túnel), que é o único que lê a chave
    if not (is_local() and same_key(request.args.get("chave", ""), config_key())):
        return error_page("Chave inválida", "Abre as configurações a partir do Termux, com ~/config.sh.", 403)
    response = redirect("/#config" if request.args.get("ir") == "config" else "/")
    response.set_cookie(CONFIG_COOKIE, config_key(), max_age=365 * 24 * 3600, httponly=True, samesite="Strict")
    return response

def restart_all():
    # Volta a correr o start.sh numa sessão à parte: ele termina este servidor, e
    # assim não morre com ele. Os 2 segundos deixam a resposta chegar ao browser
    log = open(RESTART_LOG, "ab")
    subprocess.Popen(["bash", "-c", 'sleep 2; exec "$0"', START_SCRIPT], stdin=subprocess.DEVNULL,
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

if __name__ == "__main__":
    ensure_config_key()
    app.run(host="0.0.0.0", port=8080)
