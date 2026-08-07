from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse


def parse_link_header(value: str | None) -> dict[str, str]:
    links: dict[str, str] = {}
    if not value:
        return links
    for part in value.split(","):
        sections = [section.strip() for section in part.split(";")]
        if not sections or not sections[0].startswith("<") or not sections[0].endswith(">"):
            continue
        target = sections[0][1:-1]
        for section in sections[1:]:
            if section.startswith("rel="):
                relation = section[4:].strip('"')
                links[relation] = target
    return links


def query_value(url: str, name: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(name)
    return values[-1] if values else None


def absolute_next_url(base_url: str, next_url: str | None) -> str | None:
    return urljoin(base_url.rstrip("/") + "/", next_url) if next_url else None
