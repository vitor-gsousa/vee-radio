import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager

from flask import Flask, Response, jsonify, render_template, request, redirect
from mpd import MPDClient, MPDError

app = Flask(__name__)

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
# Escrito pelo start.sh com o link do Cloudflare Tunnel
TUNNEL_URL_FILE = os.path.expanduser("~/tunnel-url.txt")
# Logótipos descarregados, um ficheiro por stream
LOGO_DIR = os.path.expanduser("~/.cache/vee-radio/logos")
# Só URLs simples: o logótipo vai entre aspas no #EXTINF e num url() de CSS
LOGO_URL_RE = re.compile(r"^https?://[^\s\"',()\\]+$")
LOGO_MAX_BYTES = 512 * 1024
# Depois de uma falha, só volta a tentar descarregar o logótipo passado um dia
LOGO_RETRY_SECONDS = 24 * 3600
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
ORDERS = [("clickcount", "mais ouvidas"), ("votes", "mais votadas"), ("clicktrend", "em alta"), ("random", "aleatórias")]
ORDER_LABELS = dict(ORDERS)
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
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for url, name, logo in entries:
            if name or logo:
                f.write(extinf(name, logo))
            f.write(url + "\n")

def playlist_path(name):
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    return os.path.join(PLAYLIST_DIR, name + ".m3u")

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
    if b"<svg" in data[:1024]:
        return "image/svg+xml"
    return None

def fetch(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": "vee-radio/1.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read(limit + 1)

def lookup_logo(stream_url):
    # Só procura pelo URL exato do stream: pelo nome aparecem rádios de outros países
    url = "https://all.api.radio-browser.info/json/stations/byurl?" + urllib.parse.urlencode({"url": stream_url})
    for s in json.loads(fetch(url, 1024 * 1024)):
        if LOGO_URL_RE.match(s.get("favicon") or ""):
            return s["favicon"]
    return None

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
    return {"status": status, "state": STATES.get(status.get("state"), status.get("state")),
            "current_station": display_name(current) if current.get("file") else "",
            "current_title": clean_title(current.get("title", "")),
            "current_key": logo_key(current["file"]) if current.get("file") else "",
            "current_pos": current.get("pos") if playing else None,
            "volume": volume, "has_stations": status.get("playlistlength", "0") != "0"}

def render_index(results=None, query="", filters=None, theme=None):
    with mpd_client() as c:
        status = c.status()
        current = c.currentsong()
        queue = c.playlistinfo()
        stored_playlists = sorted(p['playlist'] for p in c.listplaylists())

    info = station_info()
    display_name = display_namer(info)
    player = player_context(status, current, info)
    stations = [{"pos": s["pos"], "name": display_name(s), "key": logo_key(s["file"]),
                 "current": s["pos"] == player["current_pos"]} for s in queue]
    playlists = [{"name": p, "count": len(read_m3u(playlist_path(p) or ""))} for p in stored_playlists]
    try:
        with open(TUNNEL_URL_FILE, encoding="utf-8") as f:
            public_url = f.read().strip()
    except FileNotFoundError:
        public_url = ""
    return render_template("index.html", **player,
                                  stations=stations, queue_urls={s["file"] for s in queue},
                                  playlists=playlists, active=active_playlist(),
                                  results=results, query=query, filters=filters or search_filters(), public_url=public_url,
                                  themes=THEMES, countries=COUNTRIES, orders=ORDERS, languages=LANGUAGES,
                                  bitrates=BITRATES, theme=theme,
                                  hint=STREAM_FAILED_HINT)

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
def estado():
    # Estado do reprodutor para o script: a barra já feita em HTML (o mesmo
    # template da página) e o que é preciso para saber se a página mudou
    with mpd_client() as c:
        status = c.status()
        current = c.currentsong()
    player = player_context(status, current, station_info())
    return jsonify(player=render_template("player.html", **player), pos=player["current_pos"],
                   station=player["current_station"], playing=status.get("state") == "play",
                   queue=status.get("playlist", ""), error=status.get("error", ""))

def done():
    # Os botões do reprodutor devolvem o estado ao script, em vez da página inteira
    return estado() if from_script() else redirect("/")

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
    try:
        logo_url = info.get(stream_url, {}).get("logo") or lookup_logo(stream_url)
        data = fetch(logo_url, LOGO_MAX_BYTES) if logo_url else b""
    except (OSError, ValueError):
        data = b""
    if len(data) > LOGO_MAX_BYTES or not image_type(data):
        open(failed, "w").close()
        return initials_response(name)
    with open(path, "wb") as f:
        f.write(data)
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
    params = {"limit": limit * 2, "hidebroken": "true", "order": filters["ordem"], "reverse": "true", **criteria}
    if filters["pais"]:
        params["countrycode"] = filters["pais"]
    if filters["lingua"]:
        params["language"] = filters["lingua"]
    if filters["kbps"]:
        params["bitrateMin"] = filters["kbps"]
    url = "https://all.api.radio-browser.info/json/stations/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "vee-radio/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        stations = json.load(r)
    # A mesma rádio aparece muitas vezes repetida com o mesmo URL. Os streams HLS
    # (.m3u8) ficam de fora porque o MPD nem sempre os consegue tocar; quase todas
    # as rádios têm também um stream normal.
    seen, results = set(), []
    for s in stations:
        if s.get("hls") or ".m3u8" in s.get("url_resolved", ""):
            continue
        if s.get("url_resolved") and s["url_resolved"] not in seen:
            seen.add(s["url_resolved"])
            if not LOGO_URL_RE.match(s.get("favicon") or ""):
                s["favicon"] = ""
            s["name"] = " ".join(s.get("name", "").split())
            results.append(s)
    return results[:limit]

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
        return error_page("Nome inválido", "O nome da playlist não pode ter / nem \\ nem começar por ponto.", 400)
    logos = [l if LOGO_URL_RE.match(l) else None for l in request.form.getlist("logo")]
    picked = list(zip(request.form.getlist("url"), request.form.getlist("name"), logos))
    existed = os.path.isfile(path)
    entries = read_m3u(path)
    known = {u for u, _, _ in entries}
    new = [(u, " ".join(n.split()) or None, l) for u, n, l in picked if u and u not in known]
    with mpd_client() as c:
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
        # Playlist nova: passa a ser a da página principal, como em "Nova playlist"
        c.clear()
        c.load(target)
        if new:
            c.play(0)
    set_active(target)
    return redirect("/")

@app.route("/play_pos/<int:pos>", methods=["POST"])
def play_pos(pos):
    with mpd_client() as c:
        c.play(pos)
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
    # Junta espaços e quebras de linha para o nome não partir o formato m3u
    name = " ".join(request.form.get("name", "").split())
    logo = request.form.get("logo", "").strip()
    if not LOGO_URL_RE.match(logo):
        logo = ""
    if url:
        # O nome vai para o ficheiro antes de sincronizar, para a playlist ficar com ele
        if name or logo:
            with open(NAMES_FILE, "a", encoding="utf-8", newline="\n") as f:
                f.write(extinf(name, logo) + url + "\n")
        if logo:
            # Pode ter falhado antes, sem logótipo; agora há um para tentar
            try:
                os.remove(os.path.join(LOGO_DIR, logo_key(url) + ".falhou"))
            except FileNotFoundError:
                pass
        with mpd_client() as c:
            c.add(url)
            sync_active(c)
    # Vindo da pesquisa, volta aos resultados; só aceita caminhos locais
    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("/")

@app.route("/remove/<int:pos>", methods=["POST"])
def remove(pos):
    with mpd_client() as c:
        c.delete(pos)
        sync_active(c)
    return redirect("/")

@app.route("/load_playlist", methods=["POST"])
def load_playlist():
    name = request.form.get("playlist_name", "").strip()
    path = playlist_path(name)
    if path is None or not os.path.isfile(path):
        return error_page("Playlist inexistente", "Essa playlist já não existe.", 404)
    with mpd_client() as c:
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
        return error_page("Nome inválido", "O nome da playlist não pode ter / nem \\ nem começar por ponto.", 400)
    if os.path.exists(path):
        return error_page("Playlist existente", f"Já existe uma playlist «{name}». Escolhe outro nome.", 400)
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    with mpd_client() as c:
        if request.form.get("from") == "atuais":
            write_m3u(path, queue_entries(c))
        else:
            write_m3u(path, [])
            c.clear()
    set_active(name)
    return redirect("/")

@app.route("/delete_playlist", methods=["POST"])
def delete_playlist():
    name = request.form.get("playlist_name", "").strip()
    path = playlist_path(name)
    if path is None:
        return error_page("Nome inválido", "O nome da playlist não pode ter / nem \\ nem começar por ponto.", 400)
    was_active = name == active_playlist()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    if was_active:
        set_active("")
    return redirect("/#playlists")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
