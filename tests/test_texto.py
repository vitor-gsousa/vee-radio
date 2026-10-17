# Funções puras: nomes, URLs, agrupar versões, ficheiros .m3u
import pytest

import radio_web as r
from conftest import make_playlist


@pytest.mark.parametrize("text, plain", [
    ("Rádio Comercial", "radio comercial"),
    ("ANTENA 1", "antena 1"),
    ("Smooth FM – Açores", "smooth fm – acores"),
    ("Ö1 Österreich", "o1 osterreich"),
])
def test_plain_text(text, plain):
    assert r.plain_text(text) == plain


def test_group_key_ignora_marcas_de_qualidade_e_separa_paises():
    assert r.group_key("Rádio Comercial 128k", "pt") == r.group_key("Radio Comercial (AAC)", "PT")
    assert r.group_key("Radio 1", "PT") != r.group_key("Radio 1", "NL")


def test_quality_prefere_aac_a_mp3_com_o_mesmo_som():
    aac64 = {"codec": "AAC", "bitrate": 64}
    mp3_96 = {"codec": "MP3", "bitrate": 96}
    assert r.quality(aac64) > r.quality(mp3_96)
    # Bitrate desconhecido conta como 64
    assert r.quality({"codec": "MP3", "bitrate": 0}) == r.quality({"codec": "MP3", "bitrate": 64})


def station(name, url, codec="MP3", bitrate=128, country="PT", clicks=0):
    return {"name": name, "url_resolved": url, "codec": codec, "bitrate": bitrate,
            "countrycode": country, "clickcount": clicks, "favicon": "", "stationuuid": "", "tags": ""}


def test_group_versions_junta_versoes_e_fica_com_a_melhor():
    found = r.clean_stations([
        station("Rádio X", "http://x/mp3", "MP3", 128, clicks=10),
        station("Rádio X AAC", "http://x/aac", "AAC", 96),
        station("Rádio X", "http://x/mp3"),  # repetida
        station("Rádio Y", "http://y/hls.m3u8"),  # HLS fica de fora
        station("Rádio X", "http://x/nl", country="NL"),
    ])
    groups = r.group_versions(found)
    assert [g["name"] for g in groups] == ["Rádio X", "Rádio X"]
    pt = groups[0]
    assert pt["url_resolved"] == "http://x/aac"
    assert [v["url"] for v in pt["variants"]] == ["http://x/aac", "http://x/mp3"]


@pytest.mark.parametrize("url, ok", [
    ("http://radio.pt/stream", True),
    ("HTTPS://radio.pt/a.mp3", True),
    ("ftp://radio.pt/x", False),
    ("smb://nas/musica", False),
    ("Music/ficheiro.mp3", False),
    ("http://radio.pt/x\nkill", False),
    ("http://radio.pt/" + "a" * 3000, False),
    ("", False),
])
def test_safe_url(url, ok):
    assert r.safe_url(url) is ok


@pytest.mark.parametrize("name, ok", [
    ("Rock", True),
    ("Rádio da manhã", True),
    ("../fora", False),
    ("a/b", False),
    ("a\\b", False),
    (".escondida", False),
    ("linha\nkill", False),
    ("é" * 150, False),  # 300 bytes em UTF-8
])
def test_playlist_path(name, ok):
    assert (r.playlist_path(name) is not None) is ok


@pytest.mark.parametrize("host, public", [
    ("8.8.8.8", True),
    ("192.168.1.1", False),
    ("10.0.0.5", False),
    ("127.0.0.1", False),
    ("::1", False),
    ("100.64.0.1", False),  # CGNAT
    ("169.254.1.1", False),
    ("localhost", False),
    ("router.local", False),
    ("nas.lan", False),
    ("", False),
])
def test_public_host(host, public):
    assert r.public_host(host) is public


def test_m3u_guarda_nomes_logotipos_e_virgulas():
    entries = [
        ("http://a/1", "Rádio, com vírgula", "https://logo.pt/a.png"),
        ("http://a/2", None, None),
        ("http://a/3", "Só nome", None),
    ]
    make_playlist("Teste", entries)
    assert r.read_m3u(r.playlist_path("Teste")) == entries


def test_flag():
    assert r.flag("pt") == "🇵🇹"
    assert r.flag("") == "" and r.flag("PRT") == ""
