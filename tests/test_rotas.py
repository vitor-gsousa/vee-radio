# Rotas com um MPD falso (conftest.FakeMPD): o que fica na fila e nos .m3u
import time

import radio_web as r
from conftest import make_playlist

TRES = [("http://a/1", "Um", None), ("http://a/2", "Dois", "https://logo.pt/2.png"), ("http://a/3", "Três", None)]


def urls(name):
    return [u for u, _, _ in r.read_m3u(r.playlist_path(name))]


def test_ouvir_uma_radio_morta_nao_fica_na_playlist(mpd, client):
    # O bug do /ouvir: o playid falhava antes de a rádio ficar marcada como
    # experiência, e a sincronização seguinte gravava-a na playlist
    make_playlist("Rock", TRES, active=True)
    for u, _, _ in TRES:
        mpd.addid(u)
    mpd.dead.add("http://morta/x")
    resp = client.post("/ouvir", data={"url": "http://morta/x", "name": "Morta"})
    assert resp.status_code in (200, 302)
    assert r.read_preview()["url"] == "http://morta/x"
    with r.mpd_client() as c:
        r.sync_active(c)
    assert urls("Rock") == ["http://a/1", "http://a/2", "http://a/3"]


def test_remover_e_anular_na_playlist_a_tocar(mpd, client):
    make_playlist("Rock", TRES, active=True)
    for u, _, _ in TRES:
        mpd.addid(u)
    song_id = mpd.queue[1]["id"]
    client.post(f"/remove/{song_id}")
    assert urls("Rock") == ["http://a/1", "http://a/3"]
    client.post("/anular")
    assert [s["file"] for s in mpd.queue] == ["http://a/1", "http://a/2", "http://a/3"]
    assert r.read_m3u(r.playlist_path("Rock"))[1] == TRES[1]


def test_remover_varias_e_anular_noutra_playlist(mpd, client):
    make_playlist("Jazz", TRES + [("http://a/4", "Quatro", None)])
    client.post("/playlist_edit", data={"nome": "Jazz", "acao": "remover", "url": ["http://a/2", "http://a/4"]})
    assert urls("Jazz") == ["http://a/1", "http://a/3"]
    with r.app.test_request_context():
        assert r.removed_context({"name": "Jazz"})["count"] == 2
        assert r.removed_context({"name": "Outra"}) is None
    client.post("/anular")
    assert urls("Jazz") == ["http://a/1", "http://a/2", "http://a/3", "http://a/4"]
    # A fila do MPD (outra playlist) não mexeu
    assert mpd.queue == []


def test_anular_expira(mpd, client):
    make_playlist("Jazz", TRES)
    r.note_removed("Jazz", [("http://a/9", "Nove", None, 0)])
    data = r.read_removed()
    assert data and data["stations"][0]["url"] == "http://a/9"
    r.write_state(r.REMOVED_FILE, {**data, "t": int(time.time()) - r.UNDO_SECONDS - 1})
    assert r.read_removed() is None


def test_anular_le_o_formato_antigo(mpd):
    r.write_state(r.REMOVED_FILE, {"url": "http://a/2", "name": "Dois", "logo": None, "pos": 1,
                                   "playlist": "Rock", "t": int(time.time())})
    assert r.read_removed()["stations"][0]["url"] == "http://a/2"


def test_volume(mpd, client):
    client.post("/volume", data={"v": "35"})
    assert mpd.volume == 35
    client.post("/volume", data={"v": "250"})
    assert mpd.volume == 100
    assert client.post("/volume", data={"v": "alto"}).status_code == 400


def test_add_stream_recusa_rede_local(mpd, client, monkeypatch):
    monkeypatch.setattr(r, "public_streams", lambda u: set())
    resp = client.post("/add_stream", data={"url": "http://192.168.1.1/", "name": "Router"},
                       headers={"Cf-Connecting-IP": "1.2.3.4"})
    assert resp.status_code == 400
    assert mpd.queue == []
