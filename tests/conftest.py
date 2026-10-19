# Os caminhos do radio_web.py (playlists, ~/.config/vee-radio, caches) são
# calculados quando o módulo é importado, a partir da pasta pessoal: aponta-se o
# HOME para uma pasta temporária antes de o importar, para os testes nunca
# tocarem nos ficheiros verdadeiros
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

import pytest

HOME = tempfile.mkdtemp(prefix="vee-radio-testes-")
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import radio_web  # noqa: E402
from mpd import CommandError  # noqa: E402


@pytest.fixture(autouse=True)
def clean_home(monkeypatch, tmp_path):
    # Cada teste começa sem playlists nem estado, e com um .env só seu
    for sub in (".config", ".cache"):
        shutil.rmtree(os.path.join(HOME, sub), ignore_errors=True)
    os.makedirs(radio_web.PLAYLIST_DIR)
    monkeypatch.setattr(radio_web, "ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setattr(radio_web, "ENV_EXAMPLE", str(tmp_path / ".env.example"))
    monkeypatch.setattr(radio_web, "_watch", {})
    yield


class FakeMPD:
    """A fila do MPD em memória, com os comandos que as rotas usam."""

    def __init__(self, urls=(), dead=()):
        self.queue = []
        self.next_id = 1
        self.state = "stop"
        self.current = None
        self.volume = 50
        self.dead = set(dead)
        for url in urls:
            self.addid(url)

    def _renumber(self):
        for i, s in enumerate(self.queue):
            s["pos"] = str(i)

    def playlistinfo(self):
        return [dict(s) for s in self.queue]

    def playlistid(self, song_id):
        return [dict(s) for s in self.queue if s["id"] == str(song_id)]

    def addid(self, url, pos=None):
        song = {"file": url, "id": str(self.next_id)}
        self.next_id += 1
        self.queue.insert(len(self.queue) if pos is None else int(pos), song)
        self._renumber()
        return song["id"]

    def add(self, url):
        self.addid(url)

    def deleteid(self, song_id):
        before = len(self.queue)
        self.queue = [s for s in self.queue if s["id"] != str(song_id)]
        if len(self.queue) == before:
            raise CommandError("No such song")
        self._renumber()

    def playid(self, song_id):
        song = next((s for s in self.queue if s["id"] == str(song_id)), None)
        if song is None:
            raise CommandError("No such song")
        if song["file"] in self.dead:
            raise CommandError("Failed to decode " + song["file"])
        self.state, self.current = "play", song["id"]

    def play(self, *args):
        if self.queue:
            self.playid(self.queue[int(args[0]) if args else 0]["id"])

    def stop(self):
        self.state = "stop"

    def status(self):
        st = {"state": self.state, "playlistlength": str(len(self.queue)), "volume": str(self.volume)}
        if self.current:
            st["songid"] = self.current
        return st

    def currentsong(self):
        return next((dict(s) for s in self.queue if s["id"] == self.current), {})

    def setvol(self, value):
        self.volume = int(value)

    def clearerror(self):
        pass

    def clear(self):
        self.queue, self.current, self.state = [], None, "stop"

    def load(self, name):
        for url, _, _ in radio_web.read_m3u(radio_web.playlist_path(name)):
            self.addid(url)

    def moveid(self, song_id, pos):
        song = next(s for s in self.queue if s["id"] == str(song_id))
        self.queue.remove(song)
        self.queue.insert(int(pos), song)
        self._renumber()

    def listplaylists(self):
        return [{"playlist": f[:-len(".m3u")]} for f in sorted(os.listdir(radio_web.PLAYLIST_DIR)) if f.endswith(".m3u")]

    def command_list_ok_begin(self):
        pass

    def command_list_end(self):
        pass


@pytest.fixture
def mpd(monkeypatch):
    fake = FakeMPD()

    @contextmanager
    def client(*args):
        yield fake

    monkeypatch.setattr(radio_web, "mpd_client", client)
    # Os logótipos e as versões vão à internet em segundo plano: aqui não
    monkeypatch.setattr(radio_web, "warm_logos", lambda *a, **k: None)
    monkeypatch.setattr(radio_web, "public_streams", lambda urls: set(urls))
    return fake


@pytest.fixture
def client():
    return radio_web.app.test_client()


def make_playlist(name, entries, active=False):
    radio_web.write_m3u(radio_web.playlist_path(name), entries)
    if active:
        with open(radio_web.ACTIVE_FILE, "w", encoding="utf-8") as f:
            f.write(name)
