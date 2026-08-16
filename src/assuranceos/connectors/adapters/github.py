from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterator

from ..definitions import (
    CollectionRequest,
    ConnectorDescriptor,
    ConnectorHealth,
    ConnectorPage,
    SourceObject,
)
from ..exceptions import ConnectorProtocolError
from ..pagination import parse_link_header, query_value
from .common import RestAdapter, parse_timestamp


class GitHubConnector(RestAdapter):
    """GitHub as three populations, not one.

    ``pull_requests`` is what a change process is *supposed* to look like.
    ``commits`` is what actually reached the branch. The two are different
    populations and testing the first while calling it the second is the
    standard way a change control passes without meaning anything: a repository
    can hold ten immaculate pull requests and still take every change through a
    direct push.

    ``commit_reviews`` is the join between them, and it is a stream rather than
    something computed later for one reason: it is *collected*, so it is hashed
    on arrival, carries its own provenance, and is checked against the same
    grant. A projection that quietly issued its own HTTP calls would be an
    ungoverned read wearing the costume of a derived column.
    """

    descriptor = ConnectorDescriptor(
        connector_type="github",
        display_name="GitHub REST",
        streams=("pull_requests", "commits", "commit_reviews"),
        required_read_scopes={
            "pull_requests": ("Pull requests: read",),
            "commits": ("Contents: read",),
            "commit_reviews": ("Contents: read", "Pull requests: read"),
        },
        documentation_urls=(
            "https://docs.github.com/en/rest/pulls/pulls",
            "https://docs.github.com/en/rest/commits/commits",
            "https://docs.github.com/en/rest/commits/commits#list-pull-requests-associated-with-a-commit",
            "https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api",
        ),
    )

    #: How many association and review lookups one collection may make. The
    #: association is one request per commit and the review count one per
    #: distinct pull request, so an unbounded stream on a busy repository is a
    #: request storm. Reaching the ceiling is not an error: the affected rows are
    #: marked as undetermined and the signed procedure reports them as a scope
    #: limitation rather than as passes.
    DEFAULT_MAX_LOOKUPS = 120

    def __init__(self, *args: Any, api_version: str = "2026-03-10", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.api_version = api_version

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "AssuranceOS-Connector/0.6",
        }

    def health(self) -> ConnectorHealth:
        response = self.request("GET", "/rate_limit", headers=self._headers())
        core = response.json_body.get("resources", {}).get("core", {})
        return ConnectorHealth(
            status="healthy",
            checked_at=datetime.now(timezone.utc),
            details={
                "remaining": core.get("remaining"),
                "limit": core.get("limit"),
                "reset": core.get("reset"),
                "api_version": self.api_version,
            },
        )

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        repository = request.scope.get("repository")
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise ValueError("GitHub request.scope.repository must be 'owner/repository'")
        return {"repositories": repository}

    def collect_pages(self, request: CollectionRequest, checkpoint: dict[str, object]):
        if request.stream == "pull_requests":
            yield from self._collect_pull_requests(request, checkpoint)
        elif request.stream == "commits":
            yield from self._collect_commits(request, checkpoint)
        elif request.stream == "commit_reviews":
            yield from self._collect_commit_reviews(request, checkpoint)
        else:
            raise ConnectorProtocolError(f"GitHub does not expose stream {request.stream!r}")

    # -- pull requests --------------------------------------------------------

    def _collect_pull_requests(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        repository = str(self.scope_for(request)["repositories"])
        owner, repo = repository.split("/", 1)
        page = int(checkpoint.get("next_page", 1) or 1)
        max_updated: datetime | None = None
        while True:
            response = self.request(
                "GET",
                f"/repos/{owner}/{repo}/pulls",
                headers=self._headers(),
                params={
                    "state": request.parameters.get("state", "all"),
                    "sort": "updated",
                    "direction": "asc",
                    "per_page": min(int(request.parameters.get("page_size", 100)), 100),
                    "page": page,
                },
            )
            if not isinstance(response.json_body, list):
                raise ConnectorProtocolError("GitHub pull request response must be a JSON array")
            objects: list[SourceObject] = []
            for raw in response.json_body:
                updated = parse_timestamp(raw.get("updated_at"))
                max_updated = max(filter(None, [max_updated, updated]), default=max_updated)
                number = raw.get("number")
                head_sha = (raw.get("head") or {}).get("sha") or "unknown"
                objects.append(
                    SourceObject(
                        source_object_id=str(raw.get("node_id") or number),
                        source_version=f"{raw.get('updated_at') or 'unknown'}:{head_sha}",
                        source_locator=str(raw.get("html_url") or self.url(f"/{owner}/{repo}/pull/{number}")),
                        payload=raw,
                        source_time=updated,
                        original_filename=f"pull-request-{number}.json",
                        metadata={"repository": repository, "number": number},
                    )
                )
            next_url = parse_link_header(response.headers.get("link") or response.headers.get("Link")).get("next")
            if next_url:
                next_page = int(query_value(next_url, "page") or page + 1)
                cursor = {"next_page": next_page}
            else:
                cursor = {
                    "next_page": 1,
                    "last_completed_updated_at": max_updated.isoformat() if max_updated else None,
                }
            yield ConnectorPage(
                objects=objects,
                next_cursor=cursor,
                request_metadata={
                    "endpoint": f"/repos/{owner}/{repo}/pulls",
                    "page": page,
                    "api_version": self.api_version,
                },
            )
            if not next_url:
                break
            page = next_page

    # -- commits --------------------------------------------------------------

    def _commit_params(self, request: CollectionRequest, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "per_page": min(int(request.parameters.get("page_size", 100)), 100),
            "page": page,
        }
        # `sha` names the branch or commit to start from. Left unset, GitHub walks
        # the default branch, which is the population an SCM control is about.
        for name in ("sha", "since", "until", "path", "author"):
            value = request.parameters.get(name)
            if value:
                params[name] = str(value)
        return params

    def _walk_commits(
        self, owner: str, repo: str, request: CollectionRequest, start_page: int
    ) -> Iterator[tuple[int, list[dict[str, Any]], str | None]]:
        page = start_page
        while True:
            response = self.request(
                "GET",
                f"/repos/{owner}/{repo}/commits",
                headers=self._headers(),
                params=self._commit_params(request, page),
            )
            if not isinstance(response.json_body, list):
                raise ConnectorProtocolError("GitHub commit response must be a JSON array")
            next_url = parse_link_header(
                response.headers.get("link") or response.headers.get("Link")
            ).get("next")
            yield page, response.json_body, next_url
            if not next_url:
                return
            page = int(query_value(next_url, "page") or page + 1)

    def _collect_commits(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        repository = str(self.scope_for(request)["repositories"])
        owner, repo = repository.split("/", 1)
        start = int(checkpoint.get("next_page", 1) or 1)
        for page, body, next_url in self._walk_commits(owner, repo, request, start):
            objects: list[SourceObject] = []
            for raw in body:
                sha = str(raw.get("sha") or "")
                if not sha:
                    raise ConnectorProtocolError("GitHub commit carries no sha")
                committed = parse_timestamp(((raw.get("commit") or {}).get("author") or {}).get("date"))
                objects.append(
                    SourceObject(
                        source_object_id=sha,
                        # A commit is immutable, so its identity is its version.
                        # The service's reuse check therefore means something
                        # here: the same sha arriving with different bytes is a
                        # real conflict rather than an ordinary update.
                        source_version=sha,
                        source_locator=str(
                            raw.get("html_url") or self.url(f"/{owner}/{repo}/commit/{sha}")
                        ),
                        payload=raw,
                        source_time=committed,
                        original_filename=f"commit-{sha[:12]}.json",
                        metadata={"repository": repository, "sha": sha},
                    )
                )
            yield ConnectorPage(
                objects=objects,
                next_cursor=(
                    {"next_page": int(query_value(next_url, "page") or page + 1)}
                    if next_url
                    else {"next_page": 1}
                ),
                request_metadata={
                    "endpoint": f"/repos/{owner}/{repo}/commits",
                    "page": page,
                    "api_version": self.api_version,
                },
            )

    # -- the join between them ------------------------------------------------

    def _collect_commit_reviews(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        """For each commit, the pull requests it arrived through, and their approvals.

        This is the only authoritative answer to "did this change go through
        review". Matching a commit sha against a merged pull request's
        ``merge_commit_sha`` finds squash and merge-commit strategies and misses
        rebase merges entirely, which would report a reviewed change as
        unreviewed. GitHub knows the association; this asks it.
        """

        repository = str(self.scope_for(request)["repositories"])
        owner, repo = repository.split("/", 1)
        budget = int(request.parameters.get("max_lookups", self.DEFAULT_MAX_LOOKUPS))
        required_approvals = bool(request.parameters.get("include_approvals", True))
        start = int(checkpoint.get("next_page", 1) or 1)
        reviews_cache: dict[int, dict[str, Any]] = {}
        spent = 0

        for page, body, next_url in self._walk_commits(owner, repo, request, start):
            objects: list[SourceObject] = []
            for raw in body:
                sha = str(raw.get("sha") or "")
                if not sha:
                    raise ConnectorProtocolError("GitHub commit carries no sha")
                associations: list[dict[str, Any]] = []
                determined = False
                reason = f"the {budget}-lookup ceiling for this collection was reached"
                if spent < budget:
                    spent += 1
                    determined = True
                    reason = "read from the commit's pull-request association"
                    associated = self.request(
                        "GET",
                        f"/repos/{owner}/{repo}/commits/{sha}/pulls",
                        headers=self._headers(),
                    ).json_body
                    if not isinstance(associated, list):
                        raise ConnectorProtocolError(
                            "GitHub commit association response must be a JSON array"
                        )
                    for pull in associated:
                        number = pull.get("number")
                        entry = {
                            "number": number,
                            "state": pull.get("state"),
                            "merged_at": pull.get("merged_at"),
                            "merge_commit_sha": pull.get("merge_commit_sha"),
                            "html_url": pull.get("html_url"),
                            "user": (pull.get("user") or {}).get("login"),
                            "approvals": None,
                            "approvals_determined": False,
                        }
                        if required_approvals and isinstance(number, int):
                            counts = reviews_cache.get(number)
                            if counts is None and spent < budget:
                                spent += 1
                                counts = self._review_counts(owner, repo, number)
                                reviews_cache[number] = counts
                            if counts is not None:
                                entry["approvals"] = counts["approvals"]
                                entry["approving_reviewers"] = counts["reviewers"]
                                entry["approvals_determined"] = True
                        associations.append(entry)

                payload = {
                    "sha": sha,
                    "repository": repository,
                    "committed_at": ((raw.get("commit") or {}).get("author") or {}).get("date"),
                    "author_login": (raw.get("author") or {}).get("login"),
                    "parents": [item.get("sha") for item in raw.get("parents") or []],
                    "associated_pull_requests": associations,
                    "association": {"determined": determined, "reason": reason},
                }
                objects.append(
                    SourceObject(
                        source_object_id=sha,
                        # Deliberately not the bare sha. A commit's association
                        # can legitimately change -- a pull request opened after
                        # the fact associates retrospectively -- so versioning
                        # this by sha alone would make an honest update look like
                        # a source-integrity conflict.
                        #
                        # The determination state is inside the digest as well,
                        # and that is not decoration. A row that hit the lookup
                        # ceiling carries an empty association list, exactly like
                        # a commit that genuinely arrived without review; hashing
                        # only the list gives those two rows the same version and
                        # different bytes, which is the shape the service raises
                        # a source-version conflict on.
                        source_version=f"{sha}:{_digest([payload['associated_pull_requests'], determined])}",
                        source_locator=str(
                            raw.get("html_url") or self.url(f"/{owner}/{repo}/commit/{sha}")
                        ),
                        payload=payload,
                        source_time=parse_timestamp(payload["committed_at"]),
                        original_filename=f"commit-review-{sha[:12]}.json",
                        metadata={"repository": repository, "sha": sha},
                    )
                )
            yield ConnectorPage(
                objects=objects,
                next_cursor=(
                    {"next_page": int(query_value(next_url, "page") or page + 1)}
                    if next_url
                    else {"next_page": 1}
                ),
                request_metadata={
                    "endpoint": f"/repos/{owner}/{repo}/commits/{{sha}}/pulls",
                    "page": page,
                    "api_version": self.api_version,
                    "lookups_spent": spent,
                    "lookup_budget": budget,
                },
            )

    def _review_counts(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """Approvals on one pull request, counted the way an auditor would.

        The latest review per reviewer decides, because a reviewer who approved
        and then requested changes has not approved, and counting review events
        would score that as an approval.
        """

        response = self.request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            headers=self._headers(),
            params={"per_page": 100},
        )
        body = response.json_body
        if not isinstance(body, list):
            raise ConnectorProtocolError("GitHub review response must be a JSON array")
        latest: dict[str, str] = {}
        for review in body:
            login = (review.get("user") or {}).get("login")
            state = str(review.get("state") or "").upper()
            if not login or state == "COMMENTED":
                continue
            latest[str(login)] = state
        reviewers = sorted(login for login, state in latest.items() if state == "APPROVED")
        return {"approvals": len(reviewers), "reviewers": reviewers}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:16]


#: The name this adapter had when it exposed one stream. Kept bound so the
#: factory, the fixture demonstration and any existing configuration keep
#: resolving while the class is about more than pull requests.
GitHubPullRequestConnector = GitHubConnector
