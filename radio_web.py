import html
import json
import os
import re
import urllib.parse
import urllib.request
from contextlib import contextmanager

from flask import Flask, render_template_string, request, redirect
from mpd import MPDClient, MPDError

app = Flask(__name__)

# Tem de coincidir com o playlist_directory do mpd.conf
PLAYLIST_DIR = os.path.expanduser("~/.config/mpd/playlists")
# Nomes dados a streams adicionados pela web, ainda que não estejam em nenhuma playlist
NAMES_FILE = os.path.expanduser("~/.config/mpd/nomes.m3u")
# Escrito pelo start.sh com o link do Cloudflare Tunnel
TUNNEL_URL_FILE = os.path.expanduser("~/tunnel-url.txt")

HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Comando Rádio & Playlists</title>
    <style>
        body { font-family: sans-serif; text-align: center; padding: 20px; background: #1e1e2e; color: #cdd6f4; max-width: 500px; margin: 0 auto; }
        h2, h3 { color: #89b4fa; }
        button { display: block; width: 100%; margin: 8px 0; padding: 12px; font-size: 16px; border-radius: 8px; border: none; background: #313244; color: #cdd6f4; cursor: pointer; }
        button:hover { background: #45475a; }
        .stop { background: #f38ba8; color: #11111b; font-weight: bold; }
        .btn-green { background: #a6e3a1; color: #11111b; font-weight: bold; }
        .vol { background: #fab387; color: #11111b; width: 48%; display: inline-block; margin: 1%; font-weight: bold; }
        input[type="text"] { width: 100%; padding: 10px; margin: 6px 0; box-sizing: border-box; border-radius: 6px; border: 1px solid #45475a; background: #313244; color: #fff; }
        .card { background: #181825; padding: 15px; border-radius: 10px; margin-bottom: 20px; text-align: left; }
        .song-item { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }
        .song-btn { width: 80%; text-align: left; margin: 0; }
        .del-btn { width: 18%; background: #f38ba8; color: #11111b; margin: 0; font-size: 12px; }
        .result-btn { text-align: left; margin: 6px 0 0 0; }
        .meta { font-size: 12px; color: #a6adc8; margin: 2px 0 8px 4px; }
    </style>
</head>
<body>
    <h2>Comando de Áudio</h2>
    
    <div class="card">
        <h3>A Tocar Agora</h3>
        <p><strong>Estação:</strong> {{ current_station or 'Parado' }}</p>
        {% if current_title %}<p><strong>Faixa:</strong> {{ current_title }}</p>{% endif %}
        <p><strong>Estado:</strong> {{ status.state }} | <strong>Volume:</strong> {{ status.volume }}%</p>
        <form action="/stop" method="post"><button class="stop">Parar</button></form>
        <div>
            <form action="/voldown" method="post" style="display:inline;"><button class="vol">- Vol</button></form>
            <form action="/volup" method="post" style="display:inline;"><button class="vol">+ Vol</button></form>
        </div>
    </div>

    <div class="card">
        <h3>Fila Atual (Estações)</h3>
        {% if queue %}
            {% for song in queue %}
            <div class="song-item">
                <form action="/play_pos/{{ song.pos }}" method="post" style="width: 80%;">
                    <button class="song-btn">{{ names.get(song.file) or song.name or song.title or song.file }}</button>
                </form>
                <form action="/remove/{{ song.pos }}" method="post" style="width: 18%;">
                    <button class="del-btn">X</button>
                </form>
            </div>
            {% endfor %}
            <form action="/clear" method="post"><button class="stop" style="margin-top:10px; padding: 8px;">Limpar Fila Atual</button></form>
        {% else %}
            <p>Fila vazia.</p>
        {% endif %}
    </div>

    <div class="card" id="pesquisa">
        <h3>Procurar Rádio</h3>
        <form action="/search#pesquisa" method="get">
            <input type="text" name="q" value="{{ query }}" placeholder="Nome da rádio (ex: Comercial, jazz)" required>
            <label><input type="checkbox" name="pt" value="1" {% if only_pt %}checked{% endif %}> Só rádios portuguesas</label>
            <button type="submit">Procurar</button>
        </form>
        {% if results is not none %}
            {% for r in results %}
                <form action="/add_stream" method="post">
                    <input type="hidden" name="url" value="{{ r.url_resolved }}">
                    <input type="hidden" name="name" value="{{ r.name }}">
                    <input type="hidden" name="next" value="{{ request.full_path }}#pesquisa">
                    <button class="result-btn">+ {{ r.name }}</button>
                </form>
                <div class="meta">{{ r.countrycode or '?' }} · {{ r.codec or '?' }}{% if r.bitrate %} · {{ r.bitrate }} kbps{% endif %}{% if r.tags %} · {{ r.tags[:60] }}{% endif %}</div>
            {% else %}
                <p>Nenhuma rádio encontrada.</p>
            {% endfor %}
        {% endif %}
    </div>

    <div class="card">
        <h3>Adicionar Novo Stream</h3>
        <form action="/add_stream" method="post">
            <input type="text" name="url" placeholder="URL do stream (ex: http://...mp3)" required>
            <input type="text" name="name" placeholder="Nome da estação (opcional, ex: Antena 1)">
            <button type="submit" class="btn-green">+ Adicionar à Fila</button>
        </form>
    </div>

    <div class="card">
        <h3>Playlists Guardadas</h3>
        {% for pl in playlists %}
            <div style="display: flex; gap: 5px; margin-bottom: 5px;">
                <form action="/load_playlist/{{ pl }}" method="post" style="flex: 3;">
                    <button style="text-align: left;">📁 {{ pl }}</button>
                </form>
            </div>
        {% else %}
            <p>Sem playlists guardadas.</p>
        {% endfor %}

        <h4 style="margin-top: 15px;">Guardar Fila Atual como Playlist:</h4>
        <form action="/save_playlist" method="post">
            <input type="text" name="playlist_name" placeholder="Nome da playlist (ex: Jazz, Noticias)" required>
            <button type="submit" class="btn-green">Guardar Playlist</button>
        </form>
    </div>

    {% if public_url %}
    <p class="meta" style="text-align: center;">Link público: <a href="{{ public_url }}" style="color: #89b4fa;">{{ public_url }}</a></p>
    {% endif %}
</body>
</html>
"""

ERROR_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Erro</title>
    <style>
        body { font-family: sans-serif; text-align: center; padding: 20px; background: #1e1e2e; color: #cdd6f4; max-width: 500px; margin: 0 auto; }
        h2 { color: #f38ba8; }
        a { color: #89b4fa; }
    </style>
</head>
<body>
    <h2>{{ title }}</h2>
    <p>{{ message }}</p>
    <p><a href="/">Voltar</a></p>
</body>
</html>
"""

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
# stream (ICY) e o nome original perde-se.
def read_m3u(path):
    entries = []
    name = None
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return entries
    with f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                name = line.split(",", 1)[1].strip() if "," in line else None
            elif line and not line.startswith("#"):
                entries.append((line, name))
                name = None
    return entries

def write_m3u(path, entries):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for url, name in entries:
            if name:
                f.write(f"#EXTINF:-1,{name}\n")
            f.write(url + "\n")

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

def station_names():
    # URL -> nome, juntando todas as playlists guardadas e o ficheiro de nomes
    paths = []
    if os.path.isdir(PLAYLIST_DIR):
        paths = [os.path.join(PLAYLIST_DIR, f) for f in sorted(os.listdir(PLAYLIST_DIR)) if f.endswith(".m3u")]
    names = {}
    for path in paths + [NAMES_FILE]:
        for url, name in read_m3u(path):
            if name:
                names[url] = name
    return names

def error_page(title, message, code):
    return render_template_string(ERROR_HTML, title=title, message=message), code

@app.errorhandler(OSError)
def mpd_unreachable(e):
    return render_template_string(ERROR_HTML, title="MPD indisponível",
                                  message="Não foi possível ligar ao MPD. Confirma que está a correr (~/start.sh)."), 503

@app.errorhandler(MPDError)
def mpd_failed(e):
    return render_template_string(ERROR_HTML, title="Erro do MPD", message=str(e)), 500

def render_index(results=None, query="", only_pt=False):
    with mpd_client() as c:
        status = c.status()
        current = c.currentsong()
        queue = c.playlistinfo()
        stored_playlists = [p['playlist'] for p in c.listplaylists()]

    names = station_names()
    current_station = names.get(current.get('file')) or current.get('name') or current.get('file', '')
    current_title = clean_title(current.get('title', ''))
    try:
        with open(TUNNEL_URL_FILE, encoding="utf-8") as f:
            public_url = f.read().strip()
    except FileNotFoundError:
        public_url = ""
    return render_template_string(HTML, status=status, current_station=current_station, current_title=current_title,
                                  queue=queue, playlists=stored_playlists, names=names,
                                  results=results, query=query, only_pt=only_pt, public_url=public_url)

@app.route("/")
def index():
    return render_index()

def search_stations(query, only_pt):
    # Diretório público de rádios (radio-browser.info); o "all" encaminha para um servidor ativo
    params = {"name": query, "limit": 25, "hidebroken": "true", "order": "clickcount", "reverse": "true"}
    if only_pt:
        params["countrycode"] = "PT"
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
            results.append(s)
    return results

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    only_pt = request.args.get("pt") == "1"
    if not query:
        return redirect("/")
    try:
        results = search_stations(query, only_pt)
    except (OSError, ValueError):
        # Apanhado aqui para não cair no handler de OSError, que culpa o MPD
        return error_page("Pesquisa indisponível", "Não foi possível contactar o radio-browser.info. Tenta outra vez.", 502)
    return render_index(results, query, only_pt)

@app.route("/play_pos/<int:pos>", methods=["POST"])
def play_pos(pos):
    with mpd_client() as c:
        c.play(pos)
    return redirect("/")

@app.route("/stop", methods=["POST"])
def stop():
    with mpd_client() as c:
        c.stop()
    return redirect("/")

@app.route("/volup", methods=["POST"])
def volup():
    with mpd_client() as c:
        c.volume(+5)
    return redirect("/")

@app.route("/voldown", methods=["POST"])
def voldown():
    with mpd_client() as c:
        c.volume(-5)
    return redirect("/")

@app.route("/add_stream", methods=["POST"])
def add_stream():
    url = request.form.get("url", "").strip()
    # Junta espaços e quebras de linha para o nome não partir o formato m3u
    name = " ".join(request.form.get("name", "").split())
    if url:
        with mpd_client() as c:
            c.add(url)
        if name:
            with open(NAMES_FILE, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"#EXTINF:-1,{name}\n{url}\n")
    # Vindo da pesquisa, volta aos resultados; só aceita caminhos locais
    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("/")

@app.route("/remove/<int:pos>", methods=["POST"])
def remove(pos):
    with mpd_client() as c:
        c.delete(pos)
    return redirect("/")

@app.route("/clear", methods=["POST"])
def clear():
    with mpd_client() as c:
        c.clear()
    return redirect("/")

@app.route("/load_playlist/<name>", methods=["POST"])
def load_playlist(name):
    with mpd_client() as c:
        c.clear()
        c.load(name)
        c.play(0)
    return redirect("/")

@app.route("/save_playlist", methods=["POST"])
def save_playlist():
    name = request.form.get("playlist_name", "").strip()
    if name:
        if "/" in name or "\\" in name or name.startswith("."):
            return error_page("Nome inválido", "O nome da playlist não pode ter / nem \\ nem começar por ponto.", 400)
        with mpd_client() as c:
            queue = c.playlistinfo()
        # O save do MPD só escreve os URLs, por isso a playlist é escrita aqui para manter os nomes
        names = station_names()
        os.makedirs(PLAYLIST_DIR, exist_ok=True)
        write_m3u(os.path.join(PLAYLIST_DIR, name + ".m3u"),
                  [(s["file"], names.get(s["file"]) or s.get("name")) for s in queue])
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
