"""Génération nginx : rendu, validations anti-injection, et `nginx -t` réel si disponible."""

import re
import shutil
import subprocess

import pytest

from icc_ai.proxy import CLOUDFLARE_NETWORKS
from icc_ai.proxy_config import main, render_cloudflare_ips, render_nginx

NAME = "code.exemple.fr"
CERT = "/etc/letsencrypt/live/code.exemple.fr/fullchain.pem"
KEY = "/etc/letsencrypt/live/code.exemple.fr/privkey.pem"


def render(**overrides):
    values = {"mode": "nginx", "server_name": NAME}
    values.update(overrides)
    return render_nginx(**values)


def listens(text):
    return [line.strip() for line in text.splitlines() if line.strip().startswith("listen")]


# --------------------------------------------------------------------------
# Validations (injection d'abord)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "", " ", "a b", "a;b", "a.com; return 301 http://evil", "a.com\nlisten 81;", "a.com\n", "a.com\r\n",
    "-a.com", "a-.com", "a..com", "a/b", "a.com{", "a.com}", "*.exemple.fr", "exémple.fr", "a.com#", "a$b.com",
    "a.com ", "x" * 64 + ".com", "\na.com", "a\x00b.com",
])
def test_nom_de_serveur_invalide(name):
    with pytest.raises(ValueError, match="Nom de serveur"):
        render(server_name=name)


@pytest.mark.parametrize("name", ["_", "localhost", "code.exemple.fr", "A-1.b2.example", "xn--exmple-cua.fr"])
def test_nom_de_serveur_valide(name):
    assert f"server_name {name};" in render(server_name=name)


@pytest.mark.parametrize("cert,key", [
    ("/etc/x.pem\n", KEY), (CERT, "/etc/y.pem\n"), ("cert.pem", KEY), (CERT, "key.pem"),
    ("/etc/a b.pem", KEY), ("/etc/x;y.pem", KEY), ("/etc/x\nssl_verify_client off;.pem", KEY),
    ("/etc/$x.pem", KEY), ("/etc/x{.pem", KEY), ("/etc/../../etc/passwd", KEY), (CERT, "/etc/../shadow"),
    ("/", KEY), ("\x00", KEY),
])
def test_chemin_tls_invalide(cert, key):
    with pytest.raises(ValueError):
        render(tls_cert=cert, tls_key=key)


def test_certificat_et_cle_vont_ensemble():
    with pytest.raises(ValueError, match="ET la clé"):
        render(tls_cert=CERT)
    with pytest.raises(ValueError, match="ET la clé"):
        render(tls_key=KEY)


@pytest.mark.parametrize("kwargs", [
    {"mode": "apache"}, {"mode": ""}, {"upstream_port": 0}, {"upstream_port": 65536}, {"upstream_port": -1},
    {"max_upload_mb": 0}, {"max_upload_mb": 501},
    {"networks": ("999.0.0.0/8",)}, {"networks": ("1.2.3.0/24; evil",)}, {"networks": ("1.2.3.0/24\nx",)},
])
def test_parametres_invalides(kwargs):
    with pytest.raises(ValueError):
        render(**kwargs)


# --------------------------------------------------------------------------
# Rendu
# --------------------------------------------------------------------------

def test_rendu_nginx_sans_tls():
    text = render()
    assert listens(text) == ["listen 80;", "listen [::]:80;"]
    assert "set_real_ip_from" not in text and "return 444" not in text and "geo " not in text
    assert "map $scheme $icc_forwarded_proto" in text and "ssl_certificate" not in text
    assert "server 127.0.0.1:8000;" in text and "client_max_body_size 60m;" in text
    assert "proxy_pass http://icc_backend;" in text and "location /ws/" in text and "location /api/" in text
    assert "server_tokens off;" in text and "limit_req_status 429;" in text
    assert "CF-Connecting-IP" not in text


def test_rendu_nginx_avec_tls():
    text = render(tls_cert=CERT, tls_key=KEY)
    assert listens(text) == ["listen 80;", "listen [::]:80;", "listen 443 ssl http2;", "listen [::]:443 ssl http2;"]
    assert f"ssl_certificate {CERT};" in text and f"ssl_certificate_key {KEY};" in text
    assert "return 301 https://$host$request_uri;" in text and "ssl_protocols TLSv1.2 TLSv1.3;" in text
    assert "/.well-known/acme-challenge/" in text


def test_rendu_cloudflare_verrouille_l_origine_par_defaut():
    text = render(mode="cloudflare")
    for network in CLOUDFLARE_NETWORKS:
        assert f"set_real_ip_from {network};" in text and f"    {network} 1;" in text
    assert "real_ip_header CF-Connecting-IP;" in text and "real_ip_recursive on;" in text
    assert "geo $realip_remote_addr $icc_from_cloudflare" in text
    assert "if ($icc_from_cloudflare = 0) { return 444; }" in text
    assert "map $http_cf_visitor $icc_forwarded_proto" in text
    assert "proxy_set_header CF-Connecting-IP $remote_addr;" in text
    # Le test de verrouillage porte sur l'adresse TCP réelle, jamais sur l'IP du visiteur.
    assert "$realip_remote_addr" in text and "127.0.0.1 1;" in text and "::1 1;" in text


def test_rendu_cloudflare_sans_verrouillage_et_nginx_verrouille():
    open_text = render(mode="cloudflare", lock_to_cloudflare=False)
    assert "return 444" not in open_text and "geo " not in open_text and "set_real_ip_from" in open_text
    locked = render(mode="nginx", lock_to_cloudflare=True)
    assert "return 444" in locked and "set_real_ip_from" not in locked


def test_ipv6_desactive_retire_les_ecoutes_inet6():
    for kwargs in ({}, {"tls_cert": CERT, "tls_key": KEY}, {"mode": "cloudflare", "tls_cert": CERT, "tls_key": KEY}):
        text = render(ipv6=False, **kwargs)
        assert "[::]" not in text and listens(text)
        assert "[::]" in render(ipv6=True, **kwargs)


def test_parametres_personnalises():
    text = render(upstream_port=9100, max_upload_mb=120, mode="cloudflare", networks=("192.0.2.0/24",))
    assert "server 127.0.0.1:9100;" in text and "client_max_body_size 120m;" in text
    assert "set_real_ip_from 192.0.2.0/24;" in text and "173.245.48.0/20" not in text


def test_aucune_valeur_utilisateur_non_validee_dans_le_rendu():
    text = render(server_name="a.example", mode="cloudflare", tls_cert=CERT, tls_key=KEY)
    assert "\r" not in text and "\x00" not in text
    assert text.count("server_name") == 2 and text.endswith("}\n")


def test_fichier_des_plages_cloudflare():
    text = render_cloudflare_ips(("1.2.3.0/24", "2001:db8::/32"))
    assert text == "1.2.3.0/24\n2001:db8::/32\n"
    assert render_cloudflare_ips().splitlines() == list(CLOUDFLARE_NETWORKS)


# --------------------------------------------------------------------------
# Ligne de commande
# --------------------------------------------------------------------------

def test_cli_ecrit_la_configuration(capsys):
    assert main(["--mode", "cloudflare", "--server-name", NAME, "--no-ipv6", "--no-lock", "--port", "9000"]) == 0
    out = capsys.readouterr().out
    assert "server_name code.exemple.fr;" in out and "[::]" not in out and "return 444" not in out
    assert "127.0.0.1:9000" in out


@pytest.mark.parametrize("argv", [
    ["--mode", "nginx", "--server-name", "a;b"],
    ["--mode", "nginx", "--server-name", NAME, "--tls-cert", CERT],
    ["--mode", "nginx", "--server-name", NAME, "--tls-cert", "x", "--tls-key", "y"],
    ["--mode", "nginx", "--server-name", NAME, "--port", "70000"],
    ["--mode", "nginx", "--server-name", NAME, "--networks-file", "/fichier/inexistant"],
])
def test_cli_erreurs_code_2_sans_sortie(argv, capsys):
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("Erreur")


def test_cli_fichier_de_plages(tmp_path, capsys):
    path = tmp_path / "ips.txt"
    path.write_text("# commentaire\n192.0.2.0/24\n\n198.51.100.0/24\n")
    assert main(["--mode", "cloudflare", "--server-name", NAME, "--networks-file", str(path)]) == 0
    out = capsys.readouterr().out
    assert "set_real_ip_from 192.0.2.0/24;" in out and "set_real_ip_from 198.51.100.0/24;" in out
    assert "173.245.48.0/20" not in out
    path.write_text("999.9.9.9/8\n")
    assert main(["--mode", "cloudflare", "--server-name", NAME, "--networks-file", str(path)]) == 2


def test_cli_mode_invalide():
    with pytest.raises(SystemExit):
        main(["--mode", "apache", "--server-name", NAME])


# --------------------------------------------------------------------------
# nginx -t réel
# --------------------------------------------------------------------------

NGINX = shutil.which("nginx")
needs_nginx = pytest.mark.skipif(NGINX is None, reason="nginx absent")


def nginx_test(tmp_path, site: str):
    """Teste `site` dans un nginx.conf principal temporaire (aucun processus lancé)."""
    for sub in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
        (tmp_path / sub).mkdir(exist_ok=True)
    (tmp_path / "site.conf").write_text(site, encoding="utf-8")
    main_conf = tmp_path / "nginx.conf"
    main_conf.write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/error.log;\nevents {{}}\n"
        f"http {{\n    access_log off;\n    client_body_temp_path {tmp_path}/body;\n"
        f"    proxy_temp_path {tmp_path}/proxy;\n    fastcgi_temp_path {tmp_path}/fastcgi;\n"
        f"    uwsgi_temp_path {tmp_path}/uwsgi;\n    scgi_temp_path {tmp_path}/scgi;\n"
        f"    include {tmp_path}/site.conf;\n}}\n", encoding="utf-8")
    return subprocess.run([NGINX, "-t", "-p", str(tmp_path), "-c", str(main_conf)],
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def certificate(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("openssl absent")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(cert), "-days", "1", "-subj", "/CN=code.exemple.fr"],
                   check=True, capture_output=True, timeout=60)
    return str(cert), str(key)


@needs_nginx
def test_nginx_refuse_une_configuration_cassee(tmp_path):
    """Témoin : le banc d'essai détecte bien une config invalide."""
    result = nginx_test(tmp_path, "server { listen 80; nimporte_quoi on; }\n")
    assert result.returncode != 0 and "unknown directive" in result.stderr


@needs_nginx
@pytest.mark.parametrize("mode,lock", [("nginx", None), ("cloudflare", None), ("cloudflare", False), ("nginx", True)])
def test_nginx_t_sans_tls(tmp_path, mode, lock):
    result = nginx_test(tmp_path, render(mode=mode, lock_to_cloudflare=lock, ipv6=False))
    assert result.returncode == 0, result.stderr
    assert "syntax is ok" in result.stderr and "test is successful" in result.stderr


@needs_nginx
@pytest.mark.parametrize("mode", ["nginx", "cloudflare"])
def test_nginx_t_avec_tls(tmp_path, certificate, mode):
    cert, key = certificate
    result = nginx_test(tmp_path, render(mode=mode, ipv6=False, tls_cert=cert, tls_key=key, upstream_port=9001,
                                         max_upload_mb=100))
    assert result.returncode == 0, result.stderr
    assert "test is successful" in result.stderr


@needs_nginx
def test_nginx_t_plages_personnalisees_et_nom_joker(tmp_path):
    text = render(mode="cloudflare", server_name="_", networks=("192.0.2.0/24", "2001:db8::/32"), ipv6=False)
    result = nginx_test(tmp_path, text)
    assert result.returncode == 0, result.stderr


@needs_nginx
def test_nginx_t_detecte_un_certificat_manquant(tmp_path):
    text = render(ipv6=False, tls_cert="/inexistant/cert.pem", tls_key="/inexistant/key.pem")
    result = nginx_test(tmp_path, text)
    assert result.returncode != 0 and re.search(r"cannot load certificate|No such file", result.stderr)
