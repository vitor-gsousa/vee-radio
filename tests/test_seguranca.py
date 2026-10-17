# Origem dos pedidos, cabeçalhos, Configurações e o .env
import os
import shutil
import subprocess

import pytest

import radio_web as r

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_host_desconhecido_e_recusado(client):
    assert client.get("/static/style.css", headers={"Host": "atacante.com"}).status_code == 400
    assert client.get("/static/style.css", headers={"Host": "abc.trycloudflare.com"}).status_code == 200
    assert client.get("/static/style.css", headers={"Host": "192.168.1.20:8080"}).status_code == 200


def test_post_de_outro_site_e_recusado(mpd, client):
    assert client.post("/volume", data={"v": "10"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/volume", data={"v": "10"}, headers={"Origin": "https://atacante.com"}).status_code == 403
    assert client.post("/volume", data={"v": "10"}, headers={"Sec-Fetch-Site": "same-origin"}).status_code != 403


def test_cabecalhos_de_seguranca(client):
    resp = client.get("/static/style.css")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Referrer-Policy"] == "no-referrer"


def test_configuracoes_so_no_proprio_telemovel(mpd, client):
    # Pelo túnel (com Cf-Connecting-IP) nunca, mesmo vindo de 127.0.0.1
    resp = client.get("/configuracoes", headers={"Cf-Connecting-IP": "1.2.3.4"})
    assert resp.status_code == 403
    assert client.post("/config", data={"secao": "acesso", "ntfy_topic": "x"},
                       headers={"Cf-Connecting-IP": "1.2.3.4"}).status_code == 403


@pytest.mark.parametrize("topic, token, weak", [
    ("curto", "", True),
    ("um-topico-bem-comprido-123", "", False),
    ("curto", "tk_123", False),
    ("https://ntfy.sh/curto", "", True),
    ("https://ntfy.exemplo.pt/curto", "", False),
    ("", "", False),
])
def test_weak_ntfy_topic(topic, token, weak):
    assert r.weak_ntfy_topic(topic, token) is weak


def test_write_env_mantem_comentarios_e_outras_chaves():
    with open(r.ENV_FILE, "w", encoding="utf-8") as f:
        f.write("# comentário\nNTFY_TOPIC_URL=antigo\nOUTRA=1\nNTFY_TOPIC_URL=repetido\n")
    r.write_env({"NTFY_TOPIC_URL": "novo", "TUNNEL_ENABLED": "0"})
    text = open(r.ENV_FILE, encoding="utf-8").read()
    assert text == "# comentário\nNTFY_TOPIC_URL=novo\nOUTRA=1\nTUNNEL_ENABLED=0\n"
    assert r.read_env() == {"NTFY_TOPIC_URL": "novo", "OUTRA": "1", "TUNNEL_ENABLED": "0"}


ENV_CASES = 'APP_NAME="VEE Rádio"\nNTFY_TOPIC_URL = topico-a\r\nNTFY_TOPIC_URL=topico-b  \nTEAMS_WEBHOOK_URL=\'https://x.pt/a?sig=1\'\n  TUNNEL_ENABLED=0\n'


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("sed"), reason="precisa de bash e sed")
def test_env_lido_igual_no_start_sh_e_no_python(tmp_path):
    # O start.sh (env_value, com sed) e o radio_web.py (read_env) têm de ler o
    # .env da mesma maneira, senão as Configurações mostram uma coisa e o arranque usa outra
    (tmp_path / ".env").write_bytes(ENV_CASES.encode("utf-8"))
    func = open(os.path.join(REPO, "start.sh"), encoding="utf-8").read()
    func = func[func.index("env_value() {"):]
    func = func[:func.index("\n}\n") + 3]
    keys = ["APP_NAME", "NTFY_TOPIC_URL", "TEAMS_WEBHOOK_URL", "TUNNEL_ENABLED"]
    script = f'DIR="$1"\n{func}\nfor k in {" ".join(keys)}; do printf "%s=%s\\n" "$k" "$(env_value "$k")"; done\n'
    out = subprocess.run(["bash", "-c", script, "bash", str(tmp_path)], stdin=subprocess.DEVNULL, capture_output=True, check=True).stdout
    from_bash = dict(line.split("=", 1) for line in out.decode("utf-8").splitlines())
    # O conftest já aponta o ENV_FILE para tmp_path/.env
    from_python = r.read_env()
    assert from_bash == {k: from_python.get(k, "") for k in keys}
