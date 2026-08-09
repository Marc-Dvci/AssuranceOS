"""Controlled public retrieval, and everything it must refuse.

A URL supplied to the platform is an instruction to make a request from inside
the network the platform runs in, so most of this file is about what does not
happen.
"""

from __future__ import annotations

import pytest

from assuranceos.public_sources import (
    ALLOWED_CONTENT_TYPES,
    CollectionGrant,
    FetchedSource,
    PublicSourceCollector,
    PublicSourceError,
    _refuse_internal,
    _validate_url,
)

GRANT = CollectionGrant(
    purpose="public company intelligence",
    allowed_hosts=frozenset({"asteria-demo.example", "status.asteria-demo.example"}),
)


def test_a_host_outside_the_grant_is_refused_before_dns():
    with pytest.raises(PublicSourceError, match="outside the collection grant"):
        _validate_url("https://not-granted.example/company", GRANT)


@pytest.mark.parametrize(
    "url, reason",
    [
        ("http://asteria-demo.example/company", "HTTPS"),
        ("https://user:pw@asteria-demo.example/company", "credentials"),
        ("https://asteria-demo.example:8443/company", "default HTTPS port"),
        ("https://asteria-demo.example/company#section", "fragment"),
        ("https:///company", "no host"),
    ],
)
def test_the_url_shape_is_constrained(url, reason):
    with pytest.raises(PublicSourceError, match=reason):
        _validate_url(url, GRANT)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.1.2.3",  # RFC1918
        "192.168.0.5",
        "172.16.4.4",
        "169.254.169.254",  # the cloud metadata address
        "0.0.0.0",
        "224.0.0.1",  # multicast
        "::1",
        "fd00::1",  # unique local
    ],
)
def test_every_non_public_address_refuses_the_fetch(address):
    with pytest.raises(PublicSourceError, match="non-public address"):
        _refuse_internal((address,), host="asteria-demo.example")


def test_one_bad_address_among_good_ones_is_enough():
    """A name resolving to a public and an internal address is the attack.

    Checking only the first would pass whenever the public one sorts first, which
    is not the caller's decision to make.
    """
    with pytest.raises(PublicSourceError, match="169.254.169.254"):
        _refuse_internal(("93.184.216.34", "169.254.169.254"), host="asteria-demo.example")


def test_a_public_address_is_allowed():
    _refuse_internal(("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"), host="example.com")


def test_the_grant_is_case_and_trailing_dot_insensitive():
    """`Example.COM.` and `example.com` are the same host to DNS."""
    assert GRANT.permits("ASTERIA-DEMO.EXAMPLE")
    assert GRANT.permits("asteria-demo.example.")
    assert not GRANT.permits("evil-asteria-demo.example")


def test_json_and_text_are_readable_and_archives_are_not():
    assert "application/json" in ALLOWED_CONTENT_TYPES
    assert "text/markdown" in ALLOWED_CONTENT_TYPES
    assert "application/zip" not in ALLOWED_CONTENT_TYPES
    assert "image/png" not in ALLOWED_CONTENT_TYPES


def test_a_redirect_off_the_grant_is_refused(monkeypatch):
    """A permitted host redirecting to an internal one is the same attack."""
    collector = PublicSourceCollector(grant=GRANT)
    with pytest.raises(PublicSourceError, match="outside the collection grant"):
        _validate_url("https://metadata.google.internal/computeMetadata/v1/", collector.grant)


def test_the_fetch_record_carries_what_a_citation_needs():
    """A retrieved page has to be re-verifiable, not merely retrieved."""
    from datetime import datetime, timezone

    fetched = FetchedSource(
        url="https://asteria-demo.example/company",
        final_url="https://asteria-demo.example/company",
        status_code=200,
        content="# Asteria",
        content_type="text/markdown",
        sha256="a" * 64,
        byte_length=10,
        retrieved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        resolved_addresses=("93.184.216.34",),
        robots_allowed=True,
    )
    record = fetched.as_dict()

    assert record["sha256"] == "a" * 64
    assert record["resolved_addresses"] == ["93.184.216.34"]
    assert record["retrieved_at"].startswith("2026-07-01")
    assert record["robots_allowed"] is True


# -- the fetch path, with the network replaced ---------------------------------


class _Stream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


class _Response:
    def __init__(self, *, status_code=200, headers=None, body=b"", peer=None, url=""):
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/markdown"}
        self._body = body
        self.extensions = {"network_stream": _Peer(peer)} if peer else {}
        self.url = _Url(url)

    def iter_bytes(self):
        for index in range(0, len(self._body), 64):
            yield self._body[index : index + 64]


class _Peer:
    def __init__(self, address):
        self._address = address

    def get_extra_info(self, name):
        return (self._address, 443) if name == "server_addr" else None


class _Url:
    def __init__(self, value):
        self._value = value

    def join(self, other):
        return other


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):  # robots.txt
        return _Response(status_code=404, headers={}, body=b"")

    def stream(self, method, url):
        return _Stream(self._responses.pop(0))


def _collector(responses, monkeypatch, **grant_kwargs):
    grant = CollectionGrant(
        purpose="fetch path",
        allowed_hosts=frozenset({"asteria-demo.example", "elsewhere.example"}),
        obey_robots=False,
        **grant_kwargs,
    )
    collector = PublicSourceCollector(grant=grant)
    monkeypatch.setattr(collector, "_client", lambda **kw: _Client(responses))
    monkeypatch.setattr(
        "assuranceos.public_sources._addresses", lambda host, port=443: ("93.184.216.34",)
    )
    return collector


def test_a_page_is_retrieved_hashed_and_measured(monkeypatch):
    collector = _collector([_Response(body=b"# Asteria Systems")], monkeypatch)

    fetched = collector.fetch("https://asteria-demo.example/company")

    assert fetched.status_code == 200
    assert fetched.content == "# Asteria Systems"
    assert fetched.byte_length == 17
    assert len(fetched.sha256) == 64


def test_the_size_cap_stops_the_stream_rather_than_measuring_after(monkeypatch):
    """Reading it all and then measuring is a limit already exceeded."""
    collector = _collector([_Response(body=b"x" * 5000)], monkeypatch, max_bytes=1000)

    with pytest.raises(PublicSourceError, match="exceeded the 1000-byte grant limit"):
        collector.fetch("https://asteria-demo.example/company")


def test_a_declared_oversize_body_is_refused_before_reading(monkeypatch):
    collector = _collector(
        [_Response(headers={"content-type": "text/html", "content-length": "9999"})],
        monkeypatch,
        max_bytes=1000,
    )

    with pytest.raises(PublicSourceError, match="declares 9999 bytes"):
        collector.fetch("https://asteria-demo.example/company")


def test_an_unsupported_content_type_is_refused(monkeypatch):
    collector = _collector(
        [_Response(headers={"content-type": "application/zip"}, body=b"PK")], monkeypatch
    )

    with pytest.raises(PublicSourceError, match="unsupported content type"):
        collector.fetch("https://asteria-demo.example/company")


def test_a_redirect_is_followed_and_revalidated(monkeypatch):
    collector = _collector(
        [
            _Response(status_code=302, headers={"location": "https://elsewhere.example/moved"}),
            _Response(body=b"moved here"),
        ],
        monkeypatch,
    )

    fetched = collector.fetch("https://asteria-demo.example/company")

    assert fetched.final_url == "https://elsewhere.example/moved"
    assert fetched.redirects == ("https://elsewhere.example/moved",)


def test_a_redirect_off_the_grant_stops_the_fetch(monkeypatch):
    collector = _collector(
        [
            _Response(
                status_code=302,
                headers={"location": "https://metadata.google.internal/computeMetadata/v1/"},
            )
        ],
        monkeypatch,
    )

    with pytest.raises(PublicSourceError, match="outside the collection grant"):
        collector.fetch("https://asteria-demo.example/company")


def test_content_from_an_internal_peer_is_discarded(monkeypatch):
    """DNS said public; the socket landed somewhere else. The bytes are not used."""
    collector = _collector(
        [_Response(body=b"secret", peer="169.254.169.254")], monkeypatch
    )

    with pytest.raises(PublicSourceError, match="landed on 169.254.169.254"):
        collector.fetch("https://asteria-demo.example/company")


def test_a_redirect_loop_ends(monkeypatch):
    collector = _collector(
        [
            _Response(status_code=302, headers={"location": "https://asteria-demo.example/a"})
            for _ in range(6)
        ],
        monkeypatch,
        max_redirects=2,
    )

    with pytest.raises(PublicSourceError, match="exceeded 2 redirects"):
        collector.fetch("https://asteria-demo.example/company")
