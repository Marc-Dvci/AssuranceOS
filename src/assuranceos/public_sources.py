"""Controlled retrieval of a company's public footprint.

Onboarding could always *record* a public source; it could not go and get one, so
the pages arrived from the published corpus and the platform's most autonomous
claim -- give it a domain and it learns the business -- had no fetch behind it.

Fetching on behalf of an agent is the most dangerous verb in the product, because
a URL is an instruction to make a request from inside the network the platform
runs in. The design therefore starts from what may be reached rather than from
what was asked for:

* a **collection grant** names the hosts, exactly as a connector grant names the
  systems a task may read. A URL outside it is refused before DNS is touched;
* every address the host resolves to is checked, and a private, loopback,
  link-local, reserved or multicast address refuses the fetch. All of them, not
  the first: a name that resolves to one public and one internal address is the
  attack, not an accident;
* the peer address is checked **again after the response**, so content fetched
  from an internal address cannot be used even if the name was rebound between
  the check and the connection;
* redirects are followed one hop at a time and re-validated against the grant,
  because a permitted host redirecting to metadata.google.internal is the same
  attack wearing the grant's clothes;
* `robots.txt` is obeyed, the response is size-capped while streaming rather than
  after, and only text-shaped content types are accepted.

The bytes come back as evidence and nothing more. They are ingested through the
vault, hashed, and marked `accepted=False` -- a page is a source, and what it
supports is a *proposal* that a human still decides on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import socket
from typing import Any
from urllib import robotparser
from urllib.parse import urlsplit, urlunsplit

USER_AGENT = "AssuranceOS-PublicIntelligence/0.8 (+https://github.com/Marc-Dvci/AssuranceOS)"

# Text-shaped only. A public page that answers with an archive or an image is not
# a source this pipeline can read, and accepting it would put arbitrary bytes
# through a parser that never asked for them.
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/xml",
)


class PublicSourceError(RuntimeError):
    """A fetch was refused. The reason is always the grant or an address rule."""


@dataclass(frozen=True)
class CollectionGrant:
    """Purpose-bound authority to read a named set of public hosts.

    The same shape as a connector grant, for the same reason: authority to read
    is granted to a purpose over a named scope, never to a component in general.
    """

    purpose: str
    allowed_hosts: frozenset[str]
    max_bytes: int = 2_000_000
    timeout_seconds: float = 10.0
    max_redirects: int = 3
    obey_robots: bool = True
    user_agent: str = USER_AGENT

    def permits(self, host: str) -> bool:
        return host.lower().rstrip(".") in {item.lower().rstrip(".") for item in self.allowed_hosts}


@dataclass
class FetchedSource:
    """One retrieved page, with everything needed to cite or re-verify it."""

    url: str
    final_url: str
    status_code: int
    content: str
    content_type: str
    sha256: str
    byte_length: int
    retrieved_at: datetime
    resolved_addresses: tuple[str, ...]
    robots_allowed: bool
    redirects: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "retrieved_at": self.retrieved_at.isoformat(),
            "resolved_addresses": list(self.resolved_addresses),
            "robots_allowed": self.robots_allowed,
            "redirects": list(self.redirects),
        }


def _addresses(host: str, port: int = 443) -> tuple[str, ...]:
    try:
        info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise PublicSourceError(f"{host} did not resolve: {exc}") from exc
    return tuple(sorted({item[4][0] for item in info}))


def _refuse_internal(addresses: tuple[str, ...], *, host: str) -> None:
    """Refuse if *any* resolved address is one the platform can reach internally.

    Checking only the first address is the subtle version of not checking: a name
    under an attacker's control can resolve to a public address and an internal
    one, and which is used is not the caller's decision.
    """
    for candidate in addresses:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError as exc:
            raise PublicSourceError(f"{host} resolved to an unparseable address") from exc
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise PublicSourceError(
                f"{host} resolves to a non-public address ({candidate}); refused before connecting"
            )


def _validate_url(url: str, grant: CollectionGrant) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise PublicSourceError("public sources must be retrieved over HTTPS")
    if parsed.username or parsed.password:
        raise PublicSourceError("public source URLs must not carry credentials")
    if parsed.fragment:
        raise PublicSourceError("public source URLs must not carry a fragment")
    host = (parsed.hostname or "").lower()
    if not host:
        raise PublicSourceError("public source URL has no host")
    if parsed.port not in (None, 443):
        raise PublicSourceError("public sources are read on the default HTTPS port only")
    if not grant.permits(host):
        raise PublicSourceError(
            f"{host} is outside the collection grant for {grant.purpose!r}"
        )
    return host, urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


@dataclass
class PublicSourceCollector:
    """Fetches public pages under a grant, and refuses everything else."""

    grant: CollectionGrant
    _robots: dict[str, robotparser.RobotFileParser] = field(default_factory=dict)

    def fetch(self, url: str) -> FetchedSource:
        host, normalized = _validate_url(url, self.grant)
        addresses = _addresses(host)
        _refuse_internal(addresses, host=host)
        robots_allowed = True
        if self.grant.obey_robots:
            robots_allowed = self._robots_allows(host, normalized)
            if not robots_allowed:
                raise PublicSourceError(f"robots.txt on {host} disallows {normalized}")
        return self._get(normalized, host=host, addresses=addresses, robots_allowed=robots_allowed)

    # -- internals -------------------------------------------------------------

    def _client(self, **kwargs: Any) -> Any:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx is a hard dependency
            raise PublicSourceError("httpx is required to collect public sources") from exc
        return httpx.Client(
            follow_redirects=False,
            timeout=self.grant.timeout_seconds,
            headers={"User-Agent": self.grant.user_agent, "Accept-Encoding": "identity"},
            **kwargs,
        )

    def _robots_allows(self, host: str, url: str) -> bool:
        parser = self._robots.get(host)
        if parser is None:
            parser = robotparser.RobotFileParser()
            try:
                with self._client() as client:
                    response = client.get(f"https://{host}/robots.txt")
                if response.status_code >= 400:
                    # No robots file is permission by omission, which is the
                    # convention. A server error is not, but treating a 5xx as a
                    # refusal makes a flaky host indistinguishable from a hostile
                    # one, so the standard reading is kept.
                    parser.parse([])
                else:
                    parser.parse(response.text.splitlines())
            except Exception:
                parser.parse([])
            self._robots[host] = parser
        return parser.can_fetch(self.grant.user_agent, url)

    def _get(
        self,
        url: str,
        *,
        host: str,
        addresses: tuple[str, ...],
        robots_allowed: bool,
    ) -> FetchedSource:
        redirects: list[str] = []
        current, current_host, current_addresses = url, host, addresses
        with self._client() as client:
            for _ in range(self.grant.max_redirects + 1):
                with client.stream("GET", current) as response:
                    self._refuse_internal_peer(response, host=current_host)
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise PublicSourceError(f"{current} redirected without a location")
                        target = str(response.url.join(location))
                        redirects.append(target)
                        # Re-validated from scratch. A permitted host redirecting
                        # to an internal one is the same attack with a nicer
                        # first hop.
                        current_host, current = _validate_url(target, self.grant)
                        current_addresses = _addresses(current_host)
                        _refuse_internal(current_addresses, host=current_host)
                        continue
                    if response.status_code != 200:
                        raise PublicSourceError(
                            f"{current} returned HTTP {response.status_code}"
                        )
                    content_type = (
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                        raise PublicSourceError(
                            f"{current} returned unsupported content type {content_type!r}"
                        )
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self.grant.max_bytes:
                        raise PublicSourceError(
                            f"{current} declares {declared} bytes, above the grant limit"
                        )
                    # Capped while streaming, not after. Reading the whole body
                    # and then measuring it is a size limit that has already been
                    # exceeded by the time it is enforced.
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.grant.max_bytes:
                            raise PublicSourceError(
                                f"{current} exceeded the {self.grant.max_bytes}-byte grant limit"
                            )
                        chunks.append(chunk)
                    payload = b"".join(chunks)
                    return FetchedSource(
                        url=url,
                        final_url=current,
                        status_code=response.status_code,
                        content=payload.decode("utf-8", errors="replace"),
                        content_type=content_type or "text/plain",
                        sha256=hashlib.sha256(payload).hexdigest(),
                        byte_length=len(payload),
                        retrieved_at=datetime.now(timezone.utc),
                        resolved_addresses=current_addresses,
                        robots_allowed=robots_allowed,
                        redirects=tuple(redirects),
                    )
        raise PublicSourceError(f"{url} exceeded {self.grant.max_redirects} redirects")

    @staticmethod
    def _refuse_internal_peer(response: Any, *, host: str) -> None:
        """Check the address actually connected to, not only the one resolved.

        Validating DNS and then handing the name to the HTTP client leaves a
        window in which the name can be rebound. Reading the peer off the open
        socket closes it for the decision that matters: content retrieved from an
        internal address is discarded rather than returned.
        """
        stream = response.extensions.get("network_stream")
        if stream is None:
            return
        peer = stream.get_extra_info("server_addr") or stream.get_extra_info("peername")
        if not peer:
            return
        candidate = peer[0] if isinstance(peer, (tuple, list)) else str(peer)
        try:
            _refuse_internal((str(candidate),), host=host)
        except PublicSourceError as exc:
            raise PublicSourceError(f"connection to {host} landed on {candidate}: {exc}") from exc
