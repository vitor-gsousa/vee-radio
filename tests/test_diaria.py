# Playlist diária: uma rádio por dia, a seguinte à meia-noite
import radio_web as r
from conftest import make_playlist

TRES = [("http://a/1", "Um", None), ("http://a/2", "Dois", None), ("http://a/3", "Três", None)]


def diaria_a_tocar(mpd, monkeypatch, dia="2026-10-19"):
    make_playlist("Dia", TRES, active=True)
    r.write_m3u(r.playlist_path("Dia"), TRES, daily=True)
    for u, _, _ in TRES:
        mpd.addid(u)
    monkeypatch.setattr(r, "today", lambda: dia)
    monkeypatch.setattr(r, "_daily_seen", None)


def a_tocar(mpd):
    return mpd.currentsong().get("file") if mpd.state == "play" else None


def test_marca_fica_ao_reescrever(mpd, client):
    make_playlist("Dia", TRES)
    client.post("/playlist_diaria", data={"nome": "Dia", "diaria": "1"})
    assert "Dia" in r.daily_playlists()
    # Juntar ou remover reescreve o ficheiro sem perder a marca
    r.write_m3u(r.playlist_path("Dia"), TRES[:2])
    assert r.m3u_daily(r.playlist_path("Dia"))
    assert r.read_m3u(r.playlist_path("Dia")) == TRES[:2]
    client.post("/playlist_diaria", data={"nome": "Dia", "diaria": "0"})
    assert "Dia" not in r.daily_playlists()


def test_muda_para_a_seguinte_a_meia_noite(mpd, client, monkeypatch):
    diaria_a_tocar(mpd, monkeypatch)
    mpd.playid(mpd.queue[1]["id"])
    r.daily_step(mpd)
    assert r.read_daily()["Dia"] == {"day": "2026-10-19", "url": "http://a/2"}
    r.daily_step(mpd)
    assert a_tocar(mpd) == "http://a/2"
    monkeypatch.setattr(r, "today", lambda: "2026-10-20")
    r.daily_step(mpd)
    assert a_tocar(mpd) == "http://a/3"
    assert r.read_daily()["Dia"]["url"] == "http://a/3"
    # No fim da lista dá a volta
    monkeypatch.setattr(r, "today", lambda: "2026-10-21")
    r.daily_step(mpd)
    assert a_tocar(mpd) == "http://a/1"


def test_escolher_outra_durante_o_dia(mpd, client, monkeypatch):
    diaria_a_tocar(mpd, monkeypatch)
    mpd.playid(mpd.queue[0]["id"])
    r.daily_step(mpd)
    client.post(f"/play_id/{mpd.queue[2]['id']}")
    r.daily_step(mpd)
    assert r.read_daily()["Dia"]["url"] == "http://a/3"
    monkeypatch.setattr(r, "today", lambda: "2026-10-20")
    r.daily_step(mpd)
    assert a_tocar(mpd) == "http://a/1"


def test_parada_a_meia_noite_o_play_toca_a_do_dia(mpd, client, monkeypatch):
    diaria_a_tocar(mpd, monkeypatch)
    mpd.playid(mpd.queue[0]["id"])
    r.daily_step(mpd)
    mpd.stop()
    monkeypatch.setattr(r, "today", lambda: "2026-10-20")
    r.daily_step(mpd)
    assert mpd.state == "stop"
    client.post("/play")
    assert a_tocar(mpd) == "http://a/2"
    # Já com rádio do dia, o ▶ volta a ser o de sempre
    mpd.stop()
    client.post("/play")
    assert a_tocar(mpd) == "http://a/2"


def test_passar_para_a_diaria_noutro_dia_nao_corta_a_musica(mpd, client, monkeypatch):
    diaria_a_tocar(mpd, monkeypatch)
    r.write_daily({"Dia": {"day": "2026-10-10", "url": "http://a/1"}})
    monkeypatch.setattr(r, "_daily_seen", ("Outra", "2026-10-19"))
    mpd.playid(mpd.queue[2]["id"])
    r.daily_step(mpd)
    assert a_tocar(mpd) == "http://a/3"
    assert r.read_daily()["Dia"] == {"day": "2026-10-19", "url": "http://a/3"}


def test_mudar_o_nome_leva_a_radio_do_dia(mpd, client):
    make_playlist("Dia", TRES)
    r.write_daily({"Dia": {"day": "2026-10-19", "url": "http://a/2"}})
    client.post("/rename_playlist", data={"nome": "Dia", "novo": "Diária"})
    assert r.read_daily() == {"Diária": {"day": "2026-10-19", "url": "http://a/2"}}
    client.post("/delete_playlist", data={"playlist_name": "Diária"})
    assert r.read_daily() == {}


def test_criar_diaria(mpd, client):
    client.post("/create_playlist", data={"playlist_name": "Nova", "from": "vazia", "diaria": "1"})
    assert "Nova" in r.daily_playlists()
    page = client.get("/playlist?nome=Nova").get_data(as_text=True)
    assert "Playlist diária" in page


def test_pagina_diz_hoje_e_amanha(mpd, client, monkeypatch):
    diaria_a_tocar(mpd, monkeypatch)
    r.write_daily({"Dia": {"day": "2026-10-19", "url": "http://a/3"}})
    page = client.get("/playlist?nome=Dia").get_data(as_text=True)
    assert "Hoje: <strong>Três</strong>" in page
    assert "passa para <strong>Um</strong>" in page


def test_pagina_segue_a_estacao_a_tocar(mpd, client, monkeypatch):
    # Antes de o vigia gravar a escolha, a linha já diz a que está a tocar
    diaria_a_tocar(mpd, monkeypatch)
    r.write_daily({"Dia": {"day": "2026-10-19", "url": "http://a/1"}})
    mpd.playid(mpd.queue[1]["id"])
    page = client.get("/playlist?nome=Dia").get_data(as_text=True)
    assert "Hoje: <strong>Dois</strong>" in page
    assert f'data-now="{mpd.queue[1]["id"]}"' in page
