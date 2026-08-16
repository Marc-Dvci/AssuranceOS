"""The whole audit, over a fixture repository, with nothing stubbed in the middle.

Collection, projection, the signed procedure in its sandbox, the governed agent,
the boundary probe and the proposed finding all run here. The only thing replaced
is the network, and it is replaced at the transport rather than at the adapter,
so every layer above it is the one that runs in production.

The repository these fixtures describe is deliberately mixed: a direct push, a
commit merged through an approved pull request, and a commit whose review path
was never determined. A test built only from failures would not notice a
procedure that had stopped being able to pass.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from assuranceos.connectors.transport import HttpResponse, normalized_url
from assuranceos.db.session import Database
from assuranceos.evaluator_audit import AuditError, AuditRequest, WorkspaceAudit
from assuranceos.evaluator_sandbox import EvaluatorSandbox, SandboxLimits
from assuranceos.vault import BaselineContentInspector, EvidenceVault

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://api.github.com"
REPO = "acme/platform"
PERIOD_START = date(2026, 8, 1)
PERIOD_END = date(2026, 8, 20)

COMMITS = [
    {
        "sha": "aaa1",
        "html_url": f"https://github.com/{REPO}/commit/aaa1",
        "commit": {"author": {"date": "2026-08-04T09:00:00Z", "name": "Alice"}},
        "author": {"login": "alice"},
        "parents": [{"sha": "root"}],
    },
    {
        "sha": "bbb2",
        "html_url": f"https://github.com/{REPO}/commit/bbb2",
        "commit": {"author": {"date": "2026-08-06T11:30:00Z", "name": "Bob"}},
        "author": {"login": "bob"},
        "parents": [{"sha": "aaa1"}, {"sha": "feature"}],
    },
    {
        "sha": "ccc3",
        "html_url": f"https://github.com/{REPO}/commit/ccc3",
        "commit": {"author": {"date": "2026-08-09T14:00:00Z", "name": "Carol"}},
        "author": {"login": "carol"},
        "parents": [{"sha": "bbb2"}],
    },
]

ASSOCIATIONS = {
    # A direct push: nothing carried it.
    "aaa1": [],
    # Merged through pull request 42, approved by one reviewer.
    "bbb2": [
        {
            "number": 42,
            "state": "closed",
            "merged_at": "2026-08-06T11:29:00Z",
            "merge_commit_sha": "bbb2",
            "html_url": f"https://github.com/{REPO}/pull/42",
            "user": {"login": "bob"},
        }
    ],
    # An open pull request exists and did not merge; the code arrived anyway.
    "ccc3": [
        {
            "number": 43,
            "state": "open",
            "merged_at": None,
            "merge_commit_sha": None,
            "html_url": f"https://github.com/{REPO}/pull/43",
            "user": {"login": "carol"},
        }
    ],
}

REVIEWS = {
    42: [
        {"user": {"login": "dana"}, "state": "APPROVED"},
        {"user": {"login": "erin"}, "state": "COMMENTED"},
    ],
    43: [],
}


class _GitHubFixture:
    """Answers the real endpoints the adapter calls, and refuses anything else.

    Counting requests matters here: the audit's cost is one call per commit plus
    one per distinct pull request, and a change that quietly turned that into one
    call per commit *per page* would still pass every assertion about the
    conclusion.
    """

    def __init__(self, *, lookup_budget: int | None = None):
        self.requests: list[str] = []
        self.lookup_budget = lookup_budget

    def send(self, request):
        url = normalized_url(request.url, request.params)
        self.requests.append(url)
        path = url.replace(BASE, "").split("?")[0]
        if path == "/rate_limit":
            return HttpResponse(
                200, {}, {"resources": {"core": {"remaining": 4999, "limit": 5000, "reset": 0}}}
            )
        if path == f"/repos/{REPO}/commits":
            return HttpResponse(200, {}, list(COMMITS))
        if path.startswith(f"/repos/{REPO}/commits/") and path.endswith("/pulls"):
            sha = path.split("/")[-2]
            if sha not in ASSOCIATIONS:
                raise AssertionError(f"unexpected association lookup for {sha}")
            return HttpResponse(200, {}, list(ASSOCIATIONS[sha]))
        if path.startswith(f"/repos/{REPO}/pulls/") and path.endswith("/reviews"):
            number = int(path.split("/")[-2])
            return HttpResponse(200, {}, list(REVIEWS.get(number, [])))
        if path == f"/repos/{REPO}/pulls":
            return HttpResponse(200, {}, [])
        raise AssertionError(f"the adapter called an endpoint no fixture covers: {url}")


@pytest.fixture()
def workspace(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "audit.db")
    database.create_schema()
    transport = _GitHubFixture()
    sandbox = EvaluatorSandbox(
        database,
        EvidenceVault.local(database, tmp_path / "objects", inspector=BaselineContentInspector()),
        limits=SandboxLimits(audit_max_commits=50, audit_max_lookups=20, audit_period_days=60),
        transport_factory=lambda _profile: transport,
    )
    created = sandbox.create_workspace("Acme Platform Ltd")
    attached = sandbox.connect(
        created.workspace_id,
        provider="github",
        base_url=BASE,
        stream="pull_requests",
        scope_value=REPO,
    )
    try:
        yield sandbox, created, attached["connector"]["connector_instance_id"], transport
    finally:
        database.dispose()


def _run(workspace, **overrides):
    sandbox, created, connector_id, _transport = workspace
    audit = WorkspaceAudit(sandbox, repository_root=ROOT)
    fields = {
        "workspace_id": created.workspace_id,
        "connector_instance_id": connector_id,
        "repository": REPO,
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        **overrides,
    }
    return audit.run(AuditRequest(**fields))


def test_the_audit_runs_end_to_end_and_concludes_ineffective(workspace):
    report = _run(workspace)

    assert report["collection"]["commits"]["objects_ingested"] == 3
    assert report["collection"]["commit_reviews"]["objects_ingested"] == 3
    assert report["population"]["commits"] == 3

    test = report["control_test"]
    assert test["test_id"] == "SCM-02"
    assert test["status"] == "succeeded"
    assert test["conclusion"] == "ineffective"
    assert test["population_count"] == 3
    assert test["population_complete"] is True
    # The direct push and the never-merged pull request. The approved merge is
    # not an exception, which is the half that proves the procedure discriminates.
    assert test["exception_count"] == 2
    assert sorted(item["subject_ref"] for item in test["exceptions"]) == [
        f"github:{REPO}@aaa1",
        f"github:{REPO}@ccc3",
    ]
    assert len(test["result_manifest_hash"]) == 64


def test_the_agent_ran_the_signed_procedure_rather_than_deciding_itself(workspace):
    report = _run(workspace)
    agent = report["agent"]
    assert agent["status"] in {"completed", "succeeded"}
    assert "tests.execute" in agent["tools_granted"]
    assert len(agent["tool_calls_allowed"]) >= 1
    # The conclusion the agent reports has to be the one the signed run produced.
    assert report["control_test"]["run_id"]
    assert agent["conclusion"] in {"ineffective", None}


def test_a_tool_outside_the_envelope_is_denied_under_the_agents_own_identity(workspace):
    report = _run(workspace)
    probe = report["agent"]["boundary_probe"]
    assert probe["denied"] is True
    assert probe["tool"] == "connector.write"
    assert probe["stage"]


def test_a_finding_is_proposed_and_waits_for_a_human(workspace):
    report = _run(workspace)
    finding = report["finding"]
    assert finding["proposed"] is True
    assert finding["status"] == "proposed"
    assert finding["exception_count"] == 2
    assert finding["finding_id"]
    assert "human decision" in finding["awaiting"]


def test_the_grant_is_revoked_when_the_work_it_authorised_is_done(workspace):
    report = _run(workspace)
    assert report["grant"]["allowed_streams"] == ["commits", "commit_reviews"]
    assert report["grant"]["read_only"] is True
    assert report["grant"]["revoked"]["status"] == "revoked"
    assert report["grant"]["revoked"]["revoked_at"]


def test_every_exception_cites_the_evidence_of_its_own_commit(workspace):
    """One evidence identifier per row is the difference this projection buys.

    A projection that attached the collection's identifier to every row would
    still produce exceptions with evidence, and every one of them would point at
    the same object.
    """

    sandbox, created, _connector, _transport = workspace
    report = _run(workspace)
    cited = [item["evidence_ids"] for item in report["control_test"]["exceptions"]]
    assert all(ids for ids in cited)
    flattened = [value for ids in cited for value in ids]
    assert len(set(flattened)) == len(flattened), "two exceptions cite the same evidence"

    records = {item.evidence_id: item for item in sandbox.vault.list(created.tenant_id)}
    for evidence_id in flattened:
        assert evidence_id in records
        assert records[evidence_id].integrity_status == "verified"


def test_the_collection_asks_for_one_lookup_per_commit_and_one_per_pull_request(workspace):
    _sandbox, _created, _connector, transport = workspace
    _run(workspace)
    associations = [url for url in transport.requests if url.endswith("/pulls") and "/commits/" in url]
    # Normalised URLs carry their query string, so match on the path segment.
    reviews = [url for url in transport.requests if "/reviews" in url]
    assert len(associations) == 3
    # Pull request 42 is the only merged one, and its reviews are read once even
    # though the cache is asked for it on every commit that names it.
    assert len(reviews) == 2


def test_an_undetermined_association_is_a_limitation_rather_than_a_pass(tmp_path: Path):
    """With the lookup budget spent, the run must not call the control effective."""

    database = Database.from_sqlite_path(tmp_path / "budget.db")
    database.create_schema()
    transport = _GitHubFixture()
    try:
        sandbox = EvaluatorSandbox(
            database,
            EvidenceVault.local(database, tmp_path / "objects", inspector=BaselineContentInspector()),
            # One lookup for the whole collection: the first commit is
            # determined and the rest are not.
            limits=SandboxLimits(audit_max_lookups=1, audit_period_days=60),
            transport_factory=lambda _profile: transport,
        )
        created = sandbox.create_workspace("Budget Ltd")
        attached = sandbox.connect(
            created.workspace_id,
            provider="github",
            base_url=BASE,
            stream="pull_requests",
            scope_value=REPO,
        )
        audit = WorkspaceAudit(sandbox, repository_root=ROOT)
        report = audit.run(
            AuditRequest(
                workspace_id=created.workspace_id,
                connector_instance_id=attached["connector"]["connector_instance_id"],
                repository=REPO,
                period_start=PERIOD_START,
                period_end=PERIOD_END,
            )
        )
        test = report["control_test"]
        # The one determined commit is the direct push, so there is still an
        # exception; what matters is that the undetermined two are reported as a
        # limitation and not counted either way.
        assert test["conclusion"] == "ineffective"
        assert test["exception_count"] == 1
        assert report["population"]["notes"]
    finally:
        database.dispose()


def test_an_audit_refuses_a_period_longer_than_the_ceiling(workspace):
    with pytest.raises(AuditError, match="at most"):
        _run(workspace, period_start=date(2020, 1, 1))


def test_an_audit_refuses_a_connector_that_is_not_a_repository(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "wrong.db")
    database.create_schema()
    try:
        sandbox = EvaluatorSandbox(
            database,
            EvidenceVault.local(database, tmp_path / "objects"),
            transport_factory=lambda _profile: _GitHubFixture(),
        )
        created = sandbox.create_workspace("Wrong Provider Ltd")
        attached = sandbox.connect(
            created.workspace_id,
            provider="jira",
            base_url="https://acme.atlassian.net",
            stream="issues",
            scope_value="CHANGE",
            credentials={"email": "a@b.example", "token": "secret"},
        )
        audit = WorkspaceAudit(sandbox, repository_root=ROOT)
        with pytest.raises(AuditError, match="commits"):
            audit.run(
                AuditRequest(
                    workspace_id=created.workspace_id,
                    connector_instance_id=attached["connector"]["connector_instance_id"],
                    repository=REPO,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )
            )
    finally:
        database.dispose()


def test_the_agent_cannot_reach_a_population_this_task_does_not_own(workspace):
    """``tests.execute`` on another test must return nothing, not the corpus.

    The workspace binds one population. A provider that fell back to the
    published corpus when asked for an unknown test would hand an evaluator the
    demonstration company's data through their own audit.
    """

    from assuranceos.governance.domain_tools import DomainToolContext, DomainToolError

    context = DomainToolContext(
        database=workspace[0].database,
        repository_root=ROOT,
        population_provider=lambda test_id: None,
    )
    with pytest.raises(DomainToolError, match="no population is bound"):
        context.bind_population("SCM-01")


def test_report_carries_no_credential_material(workspace):
    report = _run(workspace)
    serialised = json.dumps(report, default=str)
    for forbidden in ("Authorization", "Bearer ", "gcp-secret://", "sandbox-memory://"):
        assert forbidden not in serialised


def test_the_audit_refuses_to_start_on_a_nearly_spent_request_budget(tmp_path: Path):
    """Finding out at commit forty is worse than being told now.

    The audit costs about one provider request per commit, so starting with a
    handful left produces a half-collected population, and a population that was
    cut off by a quota reconciles to nothing while looking like a real result.
    """

    class _Exhausted(_GitHubFixture):
        def send(self, request):
            url = normalized_url(request.url, request.params)
            if url.endswith("/rate_limit"):
                self.requests.append(url)
                return HttpResponse(
                    200, {}, {"resources": {"core": {"remaining": 3, "limit": 60, "reset": 0}}}
                )
            raise AssertionError("the audit spent a request after being told the budget was gone")

    database = Database.from_sqlite_path(tmp_path / "budgetless.db")
    database.create_schema()
    transport = _Exhausted()
    try:
        sandbox = EvaluatorSandbox(
            database,
            EvidenceVault.local(database, tmp_path / "objects"),
            transport_factory=lambda _profile: transport,
        )
        created = sandbox.create_workspace("Rate Limited Ltd")
        attached = sandbox.connect(
            created.workspace_id,
            provider="github",
            base_url=BASE,
            stream="pull_requests",
            scope_value=REPO,
        )
        audit = WorkspaceAudit(sandbox, repository_root=ROOT)
        with pytest.raises(AuditError, match=r"request\(s\) left this hour"):
            audit.run(
                AuditRequest(
                    workspace_id=created.workspace_id,
                    connector_instance_id=attached["connector"]["connector_instance_id"],
                    repository=REPO,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )
            )
    finally:
        database.dispose()


def test_the_refusal_tells_an_unauthenticated_caller_what_would_fix_it(tmp_path: Path):
    class _Exhausted(_GitHubFixture):
        def send(self, request):
            return HttpResponse(
                200, {}, {"resources": {"core": {"remaining": 1, "limit": 60, "reset": 0}}}
            )

    database = Database.from_sqlite_path(tmp_path / "advice.db")
    database.create_schema()
    try:
        sandbox = EvaluatorSandbox(
            database,
            EvidenceVault.local(database, tmp_path / "objects"),
            transport_factory=lambda _profile: _Exhausted(),
        )
        created = sandbox.create_workspace("Advice Ltd")
        attached = sandbox.connect(
            created.workspace_id,
            provider="github",
            base_url=BASE,
            stream="pull_requests",
            scope_value=REPO,
        )
        audit = WorkspaceAudit(sandbox, repository_root=ROOT)
        with pytest.raises(AuditError, match="personal access token"):
            audit.run(
                AuditRequest(
                    workspace_id=created.workspace_id,
                    connector_instance_id=attached["connector"]["connector_instance_id"],
                    repository=REPO,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )
            )
    finally:
        database.dispose()
