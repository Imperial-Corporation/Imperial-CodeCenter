# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

import httpx
import pytest

from conftest import TOKEN, FakeGitHub, FakeRepo, make_client
from icc_crawler.github import GitHubClient, GitHubError, NotFound, RateLimitExceeded

NOW = 1_000_000.0


class Scripted:
    """Transport qui rejoue une liste de réponses (ou d'exceptions) puis répète la dernière."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def client(script, sleeps, token=TOKEN, **kw):
    kw.setdefault("clock", lambda: NOW)
    return GitHubClient(token, transport=httpx.MockTransport(script), sleep=sleeps.append, **kw)


def ok(json=None, **headers):
    return httpx.Response(200, json=json if json is not None else {}, headers=headers)


def test_primary_limit_waits_until_reset_then_retries(sleeps):
    limited = httpx.Response(403, json={"message": "API rate limit exceeded"},
                             headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 30)})
    script = Scripted(limited, ok({"full_name": "a/b"}))
    assert client(script, sleeps).get_repository("a/b") == {"full_name": "a/b"}
    assert sleeps == [31.0]                       # 30 s jusqu'au reset + 1 s de marge
    assert len(script.requests) == 2


def test_primary_limit_on_429_status_too(sleeps):
    limited = httpx.Response(429, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 5)})
    assert client(Scripted(limited, ok({"x": 1})), sleeps).get_repository("a/b") == {"x": 1}
    assert sleeps == [6.0]


def test_reset_in_the_past_waits_minimum(sleeps):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) - 100)})
    client(Scripted(limited, ok()), sleeps).get_repository("a/b")
    assert sleeps == [1.0]


def test_malformed_reset_header_does_not_crash(sleeps):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "bientot"})
    client(Scripted(limited, ok()), sleeps).get_repository("a/b")
    assert sleeps == [61.0]


def test_success_with_empty_quota_defers_wait_to_next_request(sleeps):
    first = ok({"n": 1}, **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 20)})
    script = Scripted(first, ok({"n": 2}))
    c = client(script, sleeps)
    assert c.get_repository("a/b") == {"n": 1}
    assert sleeps == []                           # la réponse déjà reçue est rendue sans attendre
    assert c.get_repository("a/b") == {"n": 2}
    assert sleeps == [21.0]


def test_empty_search_quota_does_not_block_core_requests(sleeps):
    reset = str(int(NOW) + 40)
    script = Scripted(
        ok({"items": []}, **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset,
                             "x-ratelimit-resource": "search"}),
        ok({"full_name": "a/b"}),
    )
    c = client(script, sleeps)
    c.search_repositories("q")
    c.get_repository("a/b")
    assert sleeps == []
    c.search_repositories("q")      # une seconde recherche, elle, doit attendre
    assert sleeps == [41.0]


def test_retry_after_header(sleeps):
    limited = httpx.Response(403, json={"message": "secondary rate limit"}, headers={"retry-after": "5"})
    assert client(Scripted(limited, ok({"x": 1})), sleeps).get_repository("a/b") == {"x": 1}
    assert sleeps == [6.0]


def test_retry_after_on_429(sleeps):
    limited = httpx.Response(429, headers={"retry-after": "12"})
    client(Scripted(limited, limited, ok()), sleeps).get_repository("a/b")
    assert sleeps == [13.0, 13.0]


def test_429_without_headers_backs_off(sleeps):
    client(Scripted(httpx.Response(429), ok()), sleeps).get_repository("a/b")
    assert sleeps == [61.0]


def test_max_wait_cap_raises_rate_limit_exceeded(sleeps):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 2000)})
    c = client(Scripted(limited), sleeps, max_wait=900)
    with pytest.raises(RateLimitExceeded) as info:
        c.get_repository("a/b")
    assert sleeps == []                           # n'a pas dormi inutilement
    assert "2000" in str(info.value) and "900" in str(info.value)
    assert TOKEN not in str(info.value)
    assert isinstance(info.value, GitHubError)


def test_retry_after_above_cap_raises(sleeps):
    limited = httpx.Response(429, headers={"retry-after": "3600"})
    with pytest.raises(RateLimitExceeded):
        client(Scripted(limited), sleeps, max_wait=900).get_repository("a/b")


def test_deferred_wait_above_cap_raises_on_next_request_only(sleeps):
    first = ok({"n": 1}, **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 5000)})
    c = client(Scripted(first, ok({"n": 2})), sleeps, max_wait=900)
    assert c.get_repository("a/b") == {"n": 1}
    with pytest.raises(RateLimitExceeded):
        c.get_repository("a/b")
    with pytest.raises(RateLimitExceeded):        # le blocage persiste
        c.get_repository("a/b")


def test_persistent_rate_limit_eventually_gives_up(sleeps):
    limited = httpx.Response(429, headers={"retry-after": "1"})
    script = Scripted(limited)
    with pytest.raises(GitHubError):
        client(script, sleeps, retries=2).get_repository("a/b")
    assert len(script.requests) == 3


def test_5xx_retries_with_backoff_then_succeeds(sleeps):
    script = Scripted(httpx.Response(502), httpx.Response(500), ok({"x": 1}))
    assert client(script, sleeps).get_repository("a/b") == {"x": 1}
    assert sleeps == [1.0, 2.0]


def test_5xx_exhausts_retries(sleeps):
    script = Scripted(httpx.Response(503))
    with pytest.raises(GitHubError) as info:
        client(script, sleeps, retries=3).get_repository("a/b")
    assert info.value.status == 503
    assert len(script.requests) == 4
    assert sleeps == [1.0, 2.0, 4.0]
    assert TOKEN not in str(info.value)


def test_network_errors_are_retried_and_redacted(sleeps):
    boom = httpx.ConnectError(f"connexion refusée pour Bearer {TOKEN}")
    script = Scripted(boom, boom, ok({"x": 1}))
    assert client(script, sleeps).get_repository("a/b") == {"x": 1}

    script = Scripted(boom)
    with pytest.raises(GitHubError) as info:
        client(script, [], retries=1).get_repository("a/b")
    assert TOKEN not in str(info.value) and "ConnectError" in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__   # la trace n'affiche pas l'exception d'origine


def test_404_raises_not_found_without_retry(sleeps):
    script = Scripted(httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(NotFound) as info:
        client(script, sleeps).get_repository("a/b")
    assert info.value.status == 404 and len(script.requests) == 1 and sleeps == []


def test_other_4xx_message_never_contains_token(sleeps):
    echo = httpx.Response(401, json={"message": f"Bad credentials for {TOKEN}"})
    with pytest.raises(GitHubError) as info:
        client(Scripted(echo), sleeps).get_repository("a/b")
    assert TOKEN not in str(info.value) and "***" in str(info.value) and info.value.status == 401


def test_plain_403_is_an_error_not_a_wait(sleeps):
    forbidden = httpx.Response(403, json={"message": f"Resource not accessible {TOKEN}"})
    with pytest.raises(GitHubError) as info:
        client(Scripted(forbidden), sleeps).get_repository("a/b")
    assert sleeps == [] and TOKEN not in str(info.value)


def test_invalid_json_is_a_github_error(sleeps):
    with pytest.raises(GitHubError):
        client(Scripted(httpx.Response(200, text="<html>pas du json</html>")), sleeps).get_repository("a/b")


def test_non_json_error_body_is_truncated(sleeps):
    with pytest.raises(GitHubError) as info:
        client(Scripted(httpx.Response(422, text="x" * 5000)), sleeps).get_repository("a/b")
    assert len(str(info.value)) < 400


def test_token_sent_to_api_only_never_to_raw(sleeps):
    fake = FakeGitHub(FakeRepo("o/r", files={"a.py": b"print(1)\n"}))
    c = make_client(fake, sleeps)
    c.get_repository("o/r")
    c.get_raw("o/r", fake.repos["o/r"].commit, "a.py")
    api, raw = fake.log
    assert api.headers["authorization"] == f"Bearer {TOKEN}"
    assert "authorization" not in raw.headers


def test_no_authorization_header_without_token(sleeps):
    fake = FakeGitHub(FakeRepo("o/r"))
    c = make_client(fake, sleeps, token=None)
    c.get_repository("o/r")
    assert "authorization" not in fake.log[0].headers and c.authenticated is False


def test_redact_helper(sleeps):
    c = make_client(FakeGitHub(), sleeps)
    assert c.redact(f"a {TOKEN} b") == "a *** b"
    assert make_client(FakeGitHub(), sleeps, token=None).redact("abc") == "abc"


def test_pause_between_requests(sleeps):
    fake = FakeGitHub(FakeRepo("o/r"))
    c = make_client(fake, sleeps, pause=0.5)
    c.get_repository("o/r")
    c.get_repository("o/r")
    assert sleeps == [0.5, 0.5]


def test_branch_with_slash_is_quoted(sleeps):
    fake = FakeGitHub(FakeRepo("o/r"))
    make_client(fake, sleeps).get_head_commit("o/r", "feature/x")
    assert "/branches/feature%2Fx" in str(fake.log[0].url)


def test_get_license_text_and_missing_license(sleeps):
    fake = FakeGitHub(FakeRepo("o/r", license_text="MIT\nTexte é\n"), FakeRepo("o/nolic", license=None))
    c = make_client(fake, sleeps)
    assert c.get_license_text("o/r") == "MIT\nTexte é\n"
    assert c.get_license_text("o/nolic") is None


def test_get_raw_missing_returns_none_and_quotes_path(sleeps):
    fake = FakeGitHub(FakeRepo("o/r", files={"dir/mon fichier.py": b"x = 1\n"}))
    c = make_client(fake, sleeps)
    sha = fake.repos["o/r"].commit
    assert c.get_raw("o/r", sha, "dir/mon fichier.py") == b"x = 1\n"
    assert c.get_raw("o/r", sha, "absent.py") is None
    assert "%20" in str(fake.log[0].url)


def test_get_tree_and_search_shapes(sleeps):
    fake = FakeGitHub(FakeRepo("o/r", files={"a.py": b"x" * 10}))
    c = make_client(fake, sleeps)
    tree, truncated = c.get_tree("o/r", fake.repos["o/r"].commit)
    assert truncated is False and {"path": "a.py", "type": "blob", "size": 10} in tree
    assert [i["full_name"] for i in c.search_repositories("q")] == ["o/r"]
    assert c.search_repositories("q", page=2) == []


def test_requests_made_counter(sleeps):
    script = Scripted(httpx.Response(500), ok())
    c = client(script, sleeps)
    c.get_repository("a/b")
    assert c.requests_made == 2
