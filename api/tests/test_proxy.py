"""ProxySettings et ProxyMiddleware : IP réelle, schéma, hôtes, débit, en-têtes de sécurité."""

from types import SimpleNamespace

import pytest

from conftest import run
from icc_ai import proxy as proxy_module
from icc_ai.proxy import CSP, ProxyMiddleware, ProxySettings, _parse_ip

PUBLIC = "203.0.113.9"
CF_PEER = "173.245.48.5"


def make(headers_out=None, **settings):
    """Middleware autour d'une mini-app ASGI qui mémorise la portée reçue."""
    seen = {}

    async def app(scope, receive, send):
        seen["scope"] = scope
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": list(headers_out or [])})
            await send({"type": "http.response.body", "body": b"ok"})

    return ProxyMiddleware(app, ProxySettings(**settings)), seen


async def call(middleware, *, kind="http", path="/api/x", peer=(PUBLIC, 1234), headers=None, scheme=None):
    scope = {
        "type": kind, "path": path, "client": peer, "scheme": scheme or ("ws" if kind == "websocket" else "http"),
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()],
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return scope, sent


def status(sent):
    return sent[0].get("status")


def response_headers(sent):
    return {k.decode(): v.decode() for k, v in sent[0]["headers"]}


def forwarded(settings, peer, **headers):
    return settings.client_ip(peer, {k.replace("_", "-"): v for k, v in headers.items()})


# --------------------------------------------------------------------------
# Mode off : tout en-tête forgé est ignoré
# --------------------------------------------------------------------------

FORGED = {
    "x-forwarded-for": "1.2.3.4", "x-real-ip": "1.2.3.4", "cf-connecting-ip": "1.2.3.4",
    "x-forwarded-proto": "https", "x-forwarded-host": "evil.example", "cf-visitor": '{"scheme":"https"}',
}


@pytest.mark.parametrize("peer", [PUBLIC, "127.0.0.1", "::1", CF_PEER])
def test_mode_off_ignore_les_en_tetes_forges(peer):
    middleware, seen = make(mode="off")

    async def go():
        return await call(middleware, peer=(peer, 5000), headers={**FORGED, "host": "vrai.example"})

    scope, sent = run(go())
    inner = seen["scope"]
    assert inner["client"] == (peer, 5000) and inner["scheme"] == "http"
    assert dict(inner["headers"])[b"host"] == b"vrai.example"
    assert status(sent) == 200


def test_mode_inconnu_equivaut_a_off(caplog):
    settings = ProxySettings(mode="traefik")
    assert settings.mode == "off" and not settings.enabled
    assert settings.client_ip("127.0.0.1", {"x-forwarded-for": "1.2.3.4"}) is None
    assert settings.rate_limit == 0


def test_mode_off_sans_limitation_de_debit_par_defaut():
    middleware, _ = make(mode="off")

    async def go():
        return [status((await call(middleware))[1]) for _ in range(500)]

    assert set(run(go())) == {200}


def test_mode_off_les_boucles_ne_sont_pas_de_confiance():
    settings = ProxySettings(mode="off")
    assert not settings.is_trusted("127.0.0.1") and not settings.is_trusted("::1")


# --------------------------------------------------------------------------
# Mode nginx
# --------------------------------------------------------------------------

def test_nginx_xff_lu_de_droite_a_gauche():
    s = ProxySettings(mode="nginx", trusted=["10.0.0.0/8"])
    # La valeur forgée à gauche est ignorée : le premier maillon non fiable à partir de la droite gagne.
    assert forwarded(s, "127.0.0.1", x_forwarded_for="6.6.6.6, 1.2.3.4") == "1.2.3.4"
    assert forwarded(s, "127.0.0.1", x_forwarded_for="1.2.3.4, 10.0.0.5, 127.0.0.1") == "1.2.3.4"
    assert forwarded(s, "127.0.0.1", x_forwarded_for="1.2.3.4:5555") == "1.2.3.4"
    assert forwarded(s, "::1", x_forwarded_for="2001:db8::1") == "2001:db8::1"
    assert forwarded(s, "127.0.0.1", x_forwarded_for="[2001:db8::1]:80") == "2001:db8::1"


def test_nginx_chaine_entierement_fiable_prend_le_premier():
    s = ProxySettings(mode="nginx", trusted=["10.0.0.0/8"])
    assert forwarded(s, "127.0.0.1", x_forwarded_for="10.0.0.1, 10.0.0.2") == "10.0.0.1"


def test_nginx_entrees_invalides_et_x_real_ip():
    s = ProxySettings(mode="nginx")
    assert forwarded(s, "127.0.0.1", x_forwarded_for="garbage, 1.2.3.4, <script>") == "1.2.3.4"
    assert forwarded(s, "127.0.0.1", x_forwarded_for="garbage", x_real_ip="5.6.7.8") == "5.6.7.8"
    assert forwarded(s, "127.0.0.1", x_real_ip="5.6.7.8") == "5.6.7.8"
    assert forwarded(s, "127.0.0.1") is None  # rien d'exploitable : on garde l'adresse du pair
    assert forwarded(s, "127.0.0.1", x_real_ip="n'importe quoi") is None


@pytest.mark.parametrize("peer", [PUBLIC, "8.8.8.8", "2001:db8::5", None, "", "pas-une-ip"])
def test_nginx_pair_non_fiable_ignore(peer):
    s = ProxySettings(mode="nginx")
    headers = {"x-forwarded-for": "1.2.3.4", "x-real-ip": "1.2.3.4", "x-forwarded-proto": "https",
               "x-forwarded-host": "evil.example"}
    assert s.client_ip(peer, headers) is None and s.scheme(peer, headers) is None
    assert s.forwarded_host(peer, headers) is None


def test_nginx_middleware_remplace_ip_schema_hote():
    middleware, seen = make(mode="nginx")

    async def go():
        return await call(middleware, peer=("127.0.0.1", 9), headers={
            "x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https",
            "x-forwarded-host": "code.exemple.fr", "host": "127.0.0.1:8000"})

    run(go())
    inner = seen["scope"]
    assert inner["client"][0] == "198.51.100.7" and inner["scheme"] == "https"
    hosts = [v for k, v in inner["headers"] if k == b"host"]
    assert hosts == [b"code.exemple.fr"]


def test_nginx_pair_non_fiable_via_middleware():
    middleware, seen = make(mode="nginx")

    async def go():
        return await call(middleware, peer=("8.8.8.8", 9), headers={
            "x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https", "host": "reel.example"})

    run(go())
    inner = seen["scope"]
    assert inner["client"] == ("8.8.8.8", 9) and inner["scheme"] == "http"
    assert dict(inner["headers"])[b"host"] == b"reel.example"


def test_schema_x_forwarded_proto():
    s = ProxySettings(mode="nginx")
    assert s.scheme("127.0.0.1", {"x-forwarded-proto": "HTTPS"}) == "https"
    assert s.scheme("127.0.0.1", {"x-forwarded-proto": "https, http"}) == "https"
    assert s.scheme("127.0.0.1", {"x-forwarded-proto": "javascript"}) is None
    assert s.scheme("127.0.0.1", {}) is None


def test_websocket_schema_et_ip():
    middleware, seen = make(mode="nginx")

    async def go(proto):
        await call(middleware, kind="websocket", path="/ws/x", peer=("127.0.0.1", 1), scheme="ws",
                   headers={"x-forwarded-proto": proto, "x-forwarded-for": "1.2.3.4"})
        return seen["scope"]

    assert run(go("https"))["scheme"] == "wss"
    secure = run(go("http"))
    assert secure["scheme"] == "ws" and secure["client"][0] == "1.2.3.4"


def test_lifespan_traverse_sans_traitement():
    middleware, seen = make(mode="nginx")

    async def go():
        scope = {"type": "lifespan"}
        await middleware(scope, None, None)
        return seen["scope"]

    assert run(go()) == {"type": "lifespan"}


# --------------------------------------------------------------------------
# Mode cloudflare
# --------------------------------------------------------------------------

@pytest.mark.parametrize("peer", [CF_PEER, "104.16.0.1", "2606:4700::1", "127.0.0.1", "::1"])
def test_cloudflare_cf_connecting_ip_cru_depuis_pair_fiable(peer):
    s = ProxySettings(mode="cloudflare")
    assert s.client_ip(peer, {"cf-connecting-ip": "198.51.100.7"}) == "198.51.100.7"


@pytest.mark.parametrize("peer", [PUBLIC, "8.8.8.8", "173.245.47.255", "2001:db8::1"])
def test_cloudflare_pair_hors_plages_ignore(peer):
    s = ProxySettings(mode="cloudflare")
    headers = {"cf-connecting-ip": "198.51.100.7", "x-forwarded-for": "198.51.100.8", "cf-visitor": '{"scheme":"https"}'}
    assert s.client_ip(peer, headers) is None and s.scheme(peer, headers) is None


def test_cloudflare_repli_sur_xff_si_en_tete_invalide():
    s = ProxySettings(mode="cloudflare")
    assert s.client_ip(CF_PEER, {"cf-connecting-ip": "n'importe quoi", "x-forwarded-for": "1.2.3.4"}) == "1.2.3.4"
    assert s.client_ip(CF_PEER, {"cf-connecting-ip": "n'importe quoi"}) is None


def test_cloudflare_en_tete_ignore_hors_mode_cloudflare():
    s = ProxySettings(mode="nginx")
    assert s.client_ip("127.0.0.1", {"cf-connecting-ip": "1.2.3.4"}) is None
    assert s.scheme("127.0.0.1", {"cf-visitor": '{"scheme":"https"}'}) is None


def test_cloudflare_visitor_donne_le_schema():
    s = ProxySettings(mode="cloudflare")
    assert s.scheme(CF_PEER, {"cf-visitor": '{"scheme":"https"}', "x-forwarded-proto": "http"}) == "https"
    assert s.scheme(CF_PEER, {"cf-visitor": '{"scheme":"http"}', "x-forwarded-proto": "https"}) == "http"
    assert s.scheme(CF_PEER, {"cf-visitor": "pas du json", "x-forwarded-proto": "https"}) == "https"
    assert s.scheme(CF_PEER, {"cf-visitor": '["x"]', "x-forwarded-proto": "https"}) == "https"
    assert s.scheme(CF_PEER, {"cf-visitor": '{"scheme":"gopher"}'}) is None


def test_cloudflare_plages_personnalisees_et_cidr_invalide():
    s = ProxySettings(mode="cloudflare", cloudflare_networks=["192.0.2.0/24", "zzz", "# commentaire", ""])
    assert s.is_cloudflare("192.0.2.9") and not s.is_cloudflare(CF_PEER)
    assert s.client_ip("192.0.2.9", {"cf-connecting-ip": "1.2.3.4"}) == "1.2.3.4"
    assert s.client_ip(CF_PEER, {"cf-connecting-ip": "1.2.3.4"}) is None


def test_cloudflare_via_middleware():
    middleware, seen = make(mode="cloudflare")

    async def go():
        await call(middleware, peer=(CF_PEER, 1), headers={
            "cf-connecting-ip": "198.51.100.7", "cf-visitor": '{"scheme":"https"}'})
        return seen["scope"]

    inner = run(go())
    assert inner["client"][0] == "198.51.100.7" and inner["scheme"] == "https"


# --------------------------------------------------------------------------
# X-Forwarded-Host et hôtes autorisés
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("code.exemple.fr", "code.exemple.fr"),
    ("code.exemple.fr:8443", "code.exemple.fr:8443"),
    ("a.example, b.example", "a.example"),
    ("[2001:db8::1]:8000", "[2001:db8::1]:8000"),
    ("evil.example/chemin", None),
    ("evil.example\r\nX-Injecte: 1", None),
    ("a b", None),
    ("<script>", None),
    ("user@evil.example", None),
    ("evil.example:99999999", None),
    ("", None),
])
def test_forwarded_host_valide(value, expected):
    s = ProxySettings(mode="nginx")
    assert s.forwarded_host("127.0.0.1", {"x-forwarded-host": value}) == expected


def test_hotes_autorises_400():
    middleware, seen = make(mode="off", allowed_hosts=["code.exemple.fr", "LOCALHOST"])

    async def go(host):
        seen.clear()
        _, sent = await call(middleware, headers={"host": host} if host is not None else {})
        return status(sent), bool(seen)

    assert run(go("code.exemple.fr")) == (200, True)
    assert run(go("code.exemple.fr:8000")) == (200, True)
    assert run(go("CODE.exemple.FR")) == (200, True)
    assert run(go("localhost:8000")) == (200, True)
    assert run(go("evil.example")) == (400, False)
    assert run(go("code.exemple.fr.evil.example")) == (400, False)
    assert run(go(None)) == (400, False)
    assert run(go("")) == (400, False)


def test_hotes_autorises_reponse_400_sans_appeler_l_app():
    middleware, seen = make(mode="off", allowed_hosts=["ok.example"])

    async def go():
        return await call(middleware, headers={"host": "mal.example"})

    _, sent = run(go())
    assert status(sent) == 400 and sent[1]["body"] == b"Hote non autorise." and not seen
    assert response_headers(sent)["content-type"].startswith("text/plain")


def test_hote_public_toujours_accepte():
    middleware, _ = make(mode="off", allowed_hosts=["autre.example"], public_host="Public.Example")

    async def go(host):
        return status((await call(middleware, headers={"host": host}))[1])

    assert run(go("public.example")) == 200 and run(go("autre.example")) == 200 and run(go("x.example")) == 400


def test_hote_ipv6_autorise():
    middleware, _ = make(mode="off", allowed_hosts=["[::1]"])

    async def go(host):
        return status((await call(middleware, headers={"host": host}))[1])

    assert run(go("[::1]:8000")) == 200 and run(go("[::1]")) == 200 and run(go("[::2]:8000")) == 400


def test_x_forwarded_host_verifie_contre_les_hotes_autorises():
    middleware, seen = make(mode="nginx", allowed_hosts=["code.exemple.fr"])

    async def go(peer, forwarded_host, host="127.0.0.1:8000"):
        seen.clear()
        _, sent = await call(middleware, peer=(peer, 1), headers={"host": host, "x-forwarded-host": forwarded_host})
        return status(sent)

    assert run(go("127.0.0.1", "evil.example", host="code.exemple.fr")) == 400  # hôte forgé par un proxy
    assert run(go("127.0.0.1", "code.exemple.fr")) == 200
    # Pair non fiable : l'en-tête est ignoré, c'est le vrai Host qui est contrôlé.
    assert run(go("8.8.8.8", "code.exemple.fr", host="evil.example")) == 400
    assert run(go("8.8.8.8", "evil.example", host="code.exemple.fr")) == 200


def test_hote_refuse_sur_websocket_ferme_la_connexion():
    middleware, seen = make(mode="off", allowed_hosts=["ok.example"])

    async def go():
        return await call(middleware, kind="websocket", path="/ws/x", headers={"host": "mal.example"})

    _, sent = run(go())
    assert sent == [{"type": "websocket.close", "code": 1008}] and not seen


# --------------------------------------------------------------------------
# Limitation de débit
# --------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    # On remplace le module `time` vu par proxy.py seulement (pas l'horloge d'asyncio).
    monkeypatch.setattr(proxy_module, "time", SimpleNamespace(monotonic=fake))
    return fake


def test_limite_de_debit_429_et_retry_after(clock):
    middleware, _ = make(mode="nginx", rate_limit=3)

    async def go():
        return [await call(middleware, peer=("127.0.0.1", 1), headers={"x-forwarded-for": "1.1.1.1"})
                for _ in range(4)]

    results = run(go())
    assert [status(sent) for _, sent in results] == [200, 200, 200, 429]
    refused = results[3][1]
    headers = response_headers(refused)
    assert 1 <= int(headers["retry-after"]) <= 61 and refused[1]["body"] == b"Trop de requetes."


def test_limite_par_ip_et_fenetre_glissante(clock):
    middleware, _ = make(mode="nginx", rate_limit=2)

    async def hit(ip):
        _, sent = await call(middleware, peer=("127.0.0.1", 1), headers={"x-forwarded-for": ip})
        return status(sent), response_headers(sent).get("retry-after")

    async def go():
        out = [await hit("1.1.1.1"), await hit("1.1.1.1")]
        clock.now += 30
        third = await hit("1.1.1.1")
        other = await hit("2.2.2.2")  # une autre IP a son propre compteur
        clock.now += 31  # les deux premières requêtes sortent de la fenêtre
        after = await hit("1.1.1.1")
        return out, third, other, after

    out, third, other, after = run(go())
    assert [s for s, _ in out] == [200, 200] and third[0] == 429 and other[0] == 200 and after[0] == 200
    assert 1 <= int(third[1]) <= 31  # il reste environ 30 s avant la libération


def test_limite_ne_concerne_que_l_api_http(clock):
    middleware, _ = make(mode="nginx", rate_limit=1)

    async def go():
        codes = [status((await call(middleware, path="/index.html"))[1]) for _ in range(5)]
        await call(middleware, kind="websocket", path="/ws/x")
        await call(middleware, kind="websocket", path="/api/ws")
        _, first = await call(middleware, path="/api/a")
        _, second = await call(middleware, path="/api/a")
        return codes, status(first), status(second)

    codes, first, second = run(go())
    assert set(codes) == {200} and first == 200 and second == 429


def test_limite_pair_non_fiable_ne_peut_pas_changer_d_ip(clock):
    middleware, _ = make(mode="nginx", rate_limit=2)

    async def go():
        codes = []
        for i in range(4):  # XFF forgé qui change à chaque requête
            _, sent = await call(middleware, peer=("8.8.8.8", 1), headers={"x-forwarded-for": f"9.9.9.{i}"})
            codes.append(status(sent))
        return codes

    assert run(go()) == [200, 200, 429, 429]


def test_limite_par_defaut_selon_le_mode():
    assert ProxySettings(mode="nginx").rate_limit == 240
    assert ProxySettings(mode="off").rate_limit == 0
    assert ProxySettings(mode="nginx", rate_limit=0).rate_limit == 0
    middleware, _ = make(mode="nginx", rate_limit=0)
    assert middleware._limiter is None


def test_purge_des_ip_inactives(clock):
    middleware, _ = make(mode="nginx", rate_limit=5)

    async def go():
        for i in range(20):
            await call(middleware, peer=("127.0.0.1", 1), headers={"x-forwarded-for": f"1.1.1.{i}"})
        assert len(middleware._limiter._hits) == 20
        clock.now += 400
        await call(middleware, peer=("127.0.0.1", 1), headers={"x-forwarded-for": "3.3.3.3"})
        return len(middleware._limiter._hits)

    assert run(go()) <= 2


# --------------------------------------------------------------------------
# En-têtes de sécurité
# --------------------------------------------------------------------------

def test_en_tetes_de_securite_et_csp():
    middleware, _ = make(mode="off")

    async def go(path):
        return response_headers((await call(middleware, path=path))[1])

    headers = run(go("/index.html"))
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer" and "camera=()" in headers["permissions-policy"]
    assert headers["content-security-policy"] == CSP.decode() and "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "strict-transport-security" not in headers
    for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
        exempt = run(go(path))
        assert "content-security-policy" not in exempt and exempt["x-frame-options"] == "DENY"
    # Un chemin qui commence seulement par le même texte n'est pas exempté.
    assert "content-security-policy" in run(go("/docsevil"))
    assert "content-security-policy" in run(go("/api/docs"))


def test_en_tetes_existants_non_ecrases():
    own = [(b"X-Frame-Options", b"SAMEORIGIN"), (b"Content-Security-Policy", b"default-src none")]
    middleware, _ = make(headers_out=own, mode="off")

    async def go():
        return (await call(middleware))[1]

    sent = run(go())
    raw = [(k.lower(), v) for k, v in sent[0]["headers"]]
    assert raw.count((b"x-frame-options", b"SAMEORIGIN")) == 1 and not any(k == b"x-frame-options" and v == b"DENY" for k, v in raw)
    assert [v for k, v in raw if k == b"content-security-policy"] == [b"default-src none"]


@pytest.mark.parametrize("force,headers,scheme,expected", [
    (True, {"x-forwarded-proto": "https"}, "http", True),
    (True, {}, "https", True),  # connexion directe déjà en HTTPS
    (True, {"x-forwarded-proto": "http"}, "http", False),
    (False, {"x-forwarded-proto": "https"}, "http", False),
    (True, {}, "http", False),
])
def test_hsts_seulement_si_force_https_et_https(force, headers, scheme, expected):
    middleware, _ = make(mode="nginx", force_https=force)

    async def go():
        return response_headers((await call(middleware, peer=("127.0.0.1", 1), headers=headers, scheme=scheme))[1])

    result = run(go())
    assert ("strict-transport-security" in result) is expected
    if expected:
        assert "max-age=31536000" in result["strict-transport-security"]


def test_hsts_non_credible_depuis_pair_non_fiable():
    middleware, _ = make(mode="nginx", force_https=True)

    async def go():
        return response_headers((await call(middleware, peer=("8.8.8.8", 1), headers={"x-forwarded-proto": "https"}))[1])

    assert "strict-transport-security" not in run(go())


def test_hsts_existant_non_duplique():
    middleware, _ = make(headers_out=[(b"strict-transport-security", b"max-age=1")], mode="nginx", force_https=True)

    async def go():
        return (await call(middleware, peer=("127.0.0.1", 1), headers={"x-forwarded-proto": "https"}))[1]

    sent = run(go())
    assert [v for k, v in sent[0]["headers"] if k == b"strict-transport-security"] == [b"max-age=1"]


# --------------------------------------------------------------------------
# Environnement et utilitaires
# --------------------------------------------------------------------------

def test_reglages_depuis_l_environnement(monkeypatch, tmp_path):
    ips = tmp_path / "cloudflare-ips.txt"
    ips.write_text("192.0.2.0/24\n# commentaire\n")
    for name, value in {
        "CODECENTER_PROXY_MODE": " Cloudflare ", "CODECENTER_TRUSTED_PROXIES": "10.0.0.0/8, ,bad",
        "CODECENTER_PUBLIC_HOST": " Code.Exemple.fr ", "CODECENTER_ALLOWED_HOSTS": "A.example, b.example,",
        "CODECENTER_FORCE_HTTPS": "OUI", "CODECENTER_RATE_LIMIT": "7", "CODECENTER_CLOUDFLARE_IPS_FILE": str(ips),
    }.items():
        monkeypatch.setenv(name, value)
    s = ProxySettings.from_environment()
    assert s.mode == "cloudflare" and s.force_https and s.rate_limit == 7
    assert s.public_host == "code.exemple.fr" and s.allowed_hosts == ["a.example", "b.example"]
    assert s.is_trusted("10.1.2.3") and s.is_cloudflare("192.0.2.1") and not s.is_cloudflare(CF_PEER)


def test_environnement_par_defaut_et_valeurs_invalides(monkeypatch):
    for name in ("CODECENTER_PROXY_MODE", "CODECENTER_RATE_LIMIT", "CODECENTER_FORCE_HTTPS",
                 "CODECENTER_ALLOWED_HOSTS", "CODECENTER_CLOUDFLARE_IPS_FILE", "CODECENTER_TRUSTED_PROXIES"):
        monkeypatch.delenv(name, raising=False)
    s = ProxySettings.from_environment()
    assert s.mode == "off" and s.rate_limit == 0 and not s.force_https and s.allowed_hosts == []
    monkeypatch.setenv("CODECENTER_PROXY_MODE", "nginx")
    monkeypatch.setenv("CODECENTER_RATE_LIMIT", "beaucoup")
    monkeypatch.setenv("CODECENTER_CLOUDFLARE_IPS_FILE", "/fichier/inexistant")
    assert ProxySettings.from_environment().rate_limit == 240


@pytest.mark.parametrize("value,expected", [
    ("1.2.3.4", "1.2.3.4"), (" 1.2.3.4 ", "1.2.3.4"), ("1.2.3.4:80", "1.2.3.4"), ("[::1]:80", "::1"),
    ("::1", "::1"), ("2001:db8::1", "2001:db8::1"), ("", None), (None, None), ("abc", None), ("1.2.3", None),
    ("999.1.1.1", None),
])
def test_parse_ip(value, expected):
    parsed = _parse_ip(value)
    assert (str(parsed) if parsed else None) == expected
