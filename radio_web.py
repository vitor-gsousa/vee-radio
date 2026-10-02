import html
import json
import os
import re
import urllib.parse
import urllib.request
from contextlib import contextmanager

from flask import Flask, render_template, request, redirect
from mpd import MPDClient, MPDError

app = Flask(__name__)

# Tem de coincidir com o playlist_directory do mpd.conf
PLAYLIST_DIR = os.path.expanduser("~/.config/mpd/playlists")
# Nomes dados a streams adicionados pela web, ainda que não estejam em nenhuma playlist
NAMES_FILE = os.path.expanduser("~/.config/mpd/nomes.m3u")
# Playlist que está carregada na fila; o start.sh escreve "radios" quando a carrega
ACTIVE_FILE = os.path.expanduser("~/.config/mpd/playlist-ativa.txt")
# Escrito pelo start.sh com o link do Cloudflare Tunnel
TUNNEL_URL_FILE = os.path.expanduser("~/tunnel-url.txt")

STREAM_FAILED_HINT = 'O endereço pode ter mudado. Procura a rádio outra vez em "Juntar rádio" e remove a antiga.'

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
    names = station_names()
    return [(s["file"], names.get(s["file"]) or s.get("name")) for s in c.playlistinfo()]

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

def render_index(results=None, query="", only_pt=False):
    with mpd_client() as c:
        status = c.status()
        current = c.currentsong()
        queue = c.playlistinfo()
        stored_playlists = sorted(p['playlist'] for p in c.listplaylists())

    names = station_names()
    stations = [{"pos": s["pos"], "name": names.get(s["file"]) or s.get("name") or s.get("title") or s["file"],
                 "current": status.get("state") != "stop" and s.get("id") == current.get("id")} for s in queue]
    playlists = [{"name": p, "count": len(read_m3u(playlist_path(p) or ""))} for p in stored_playlists]
    current_station = names.get(current.get('file')) or current.get('name') or current.get('file', '')
    current_title = clean_title(current.get('title', ''))
    try:
        with open(TUNNEL_URL_FILE, encoding="utf-8") as f:
            public_url = f.read().strip()
    except FileNotFoundError:
        public_url = ""
    return render_template("index.html", status=status, state=STATES.get(status.get("state"), status.get("state")),
                                  current_station=current_station, current_title=current_title,
                                  stations=stations, queue_urls={s["file"] for s in queue},
                                  playlists=playlists, active=active_playlist(),
                                  results=results, query=query, only_pt=only_pt, public_url=public_url,
                                  hint=STREAM_FAILED_HINT)

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
        return redirect("/#juntar")
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

@app.route("/play", methods=["POST"])
def play():
    with mpd_client() as c:
        c.play()
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
        # O nome vai para o ficheiro antes de sincronizar, para a playlist ficar com ele
        if name:
            with open(NAMES_FILE, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"#EXTINF:-1,{name}\n{url}\n")
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
