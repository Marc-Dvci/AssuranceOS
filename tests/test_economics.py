from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assuranceos.db.models import Engagement, ReasoningSpanRecord, Tenant
from assuranceos.db.session import Database
from assuranceos.economics import (
    ASSUMED_FUNCTION_COST_USD,
    LIST_PRICE_USD_PER_MILLION,
    engagement_economics,
)
from assuranceos.governance.telemetry import SPAN_MODEL


ROOT = Path(__file__).resolve().parents[1]
TENANT = "tnt_economics"


@pytest.fixture
def database(tmp_path):
    database = Database.from_sqlite_path(tmp_path / "economics.db")
    database.create_schema()
    with database.transaction() as session:
        session.add(
            Tenant(tenant_id=TENANT, slug="economics", name="Economics", status="active")
        )
    with database.transaction() as session:
        session.add(
            Engagement(
                engagement_id="eng_one",
                tenant_id=TENANT,
                code="ECO-1",
                title="Priced engagement",
                status="in_progress",
                audit_pack_ref="pack@1",
                period_start=datetime(2026, 7, 1).date(),
                period_end=datetime(2026, 7, 31).date(),
            )
        )
    try:
        yield database
    finally:
        database.dispose()


def _model_span(
    span_id: str,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    engagement_id: str | None = "eng_one",
    offset_seconds: int = 0,
) -> ReasoningSpanRecord:
    started = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc) + timedelta(
        seconds=offset_seconds
    )
    return ReasoningSpanRecord(
        span_row_id=f"row_{span_id}",
        tenant_id=TENANT,
        trace_id="trc_1",
        span_id=span_id,
        name=SPAN_MODEL,
        engagement_id=engagement_id,
        status="ok",
        started_at=started,
        ended_at=started + timedelta(seconds=2),
        duration_ms=2000.0,
        attributes_json={
            "gen_ai.response.model": model,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
        },
    )


def test_metered_usage_is_priced_at_the_published_rate(database):
    with database.transaction() as session:
        session.add(
            _model_span(
                "s1", model="gemini-3.7-flash", input_tokens=1_000_000, output_tokens=1_000_000
            )
        )

    result = engagement_economics(database, TENANT)

    list_in, list_out = LIST_PRICE_USD_PER_MILLION["gemini-3.7-flash"]
    assert result["measurement"] == "metered"
    assert result["cost"]["usd"] == pytest.approx(list_in + list_out)
    # The introductory rate is reported beside the headline, never instead of
    # it: a cost claim that depends on a promotion stops being true silently.
    assert result["cost"]["introductory_usd"] < result["cost"]["usd"]
    assert result["caveat"] is None


def test_a_scripted_run_says_so_and_draws_no_comparison(database):
    with database.transaction() as session:
        session.add(
            _model_span("s1", model="scripted", input_tokens=353, output_tokens=73)
        )

    result = engagement_economics(database, TENANT)

    assert result["measurement"] == "scripted"
    assert result["models"][0]["metered"] is False
    assert "word counts" in result["caveat"]
    # The whole point. Dividing a salary budget by arithmetic on word counts
    # yields a spectacular number that means nothing.
    assert result["comparison"]["equivalent_runs"] is None


def test_tokens_served_elsewhere_are_priced_not_billed(database):
    with database.transaction() as session:
        session.add(
            _model_span(
                "s1", model="gemma-4-12b-it-IQ4_XS.gguf", input_tokens=4032, output_tokens=391
            )
        )

    result = engagement_economics(database, TENANT)

    assert result["measurement"] == "metered"
    assert result["cost"]["priced_as"] == "gemini-3.7-flash"
    assert "priced at the published" in result["caveat"]
    assert "gemma-4-12b-it-IQ4_XS.gguf" in result["caveat"]


def test_one_scripted_call_among_metered_ones_taints_the_basis(database):
    with database.transaction() as session:
        session.add(
            _model_span("s1", model="gemini-3.7-flash", input_tokens=1000, output_tokens=100)
        )
        session.add(
            _model_span(
                "s2", model="scripted", input_tokens=50, output_tokens=5, offset_seconds=10
            )
        )

    result = engagement_economics(database, TENANT)

    assert result["measurement"] == "mixed"
    assert result["comparison"]["equivalent_runs"] is None


def test_the_default_scope_is_the_whole_programme(database):
    with database.transaction() as session:
        session.add(
            Engagement(
                engagement_id="eng_two",
                tenant_id=TENANT,
                code="ECO-2",
                title="Second engagement",
                status="planned",
                audit_pack_ref="pack@1",
                period_start=datetime(2026, 7, 1).date(),
                period_end=datetime(2026, 7, 31).date(),
            )
        )
    with database.transaction() as session:
        session.add(
            _model_span("s1", model="gemini-3.7-flash", input_tokens=1000, output_tokens=100)
        )
        session.add(
            _model_span(
                "s2",
                model="gemini-3.7-flash",
                input_tokens=2000,
                output_tokens=200,
                engagement_id="eng_two",
                offset_seconds=30,
            )
        )

    programme = engagement_economics(database, TENANT)
    one = engagement_economics(database, TENANT, engagement_id="eng_one")

    # An audit function is hired for a programme, not an engagement, and this
    # system splits one audit across several engagements by design.
    assert programme["scope"] == "programme"
    assert programme["engagements"] == 2
    assert programme["measured"]["input_tokens"] == 3000
    assert one["scope"] == "engagement"
    assert one["measured"]["input_tokens"] == 1000


def test_non_model_spans_never_reach_the_meter(database):
    with database.transaction() as session:
        session.add(
            _model_span("s1", model="gemini-3.7-flash", input_tokens=1000, output_tokens=100)
        )
        gateway = _model_span(
            "s2", model="gemini-3.7-flash", input_tokens=999_999, output_tokens=999_999
        )
        gateway.name = "assuranceos.gateway.decide"
        session.add(gateway)

    result = engagement_economics(database, TENANT)

    assert result["measured"]["model_calls"] == 1
    assert result["measured"]["input_tokens"] == 1000


def test_an_empty_tenant_renders_rather_than_failing(database):
    result = engagement_economics(database, TENANT)

    assert result["measurement"] == "none"
    assert result["cost"]["usd"] == 0.0
    assert result["comparison"]["annual_function_cost_usd"] == ASSUMED_FUNCTION_COST_USD
    assert result["comparison"]["equivalent_runs"] is None


def test_a_named_engagement_that_does_not_exist_is_not_silently_the_programme(database):
    result = engagement_economics(database, TENANT, engagement_id="eng_missing")

    assert result["engagement"] is None
    assert result["scope"] == "engagement"
    assert result["engagements"] == 0
    assert "eng_missing" in result["caveat"]


def test_the_comparison_always_carries_its_assumption(database):
    with database.transaction() as session:
        session.add(
            _model_span(
                "s1", model="gemini-3.7-flash", input_tokens=100_000, output_tokens=10_000
            )
        )

    comparison = engagement_economics(database, TENANT)["comparison"]

    assert comparison["equivalent_runs"] > 0
    assert "planning assumption" in comparison["assumption"]
    assert comparison["headcount"] == 4


# -- the projection --------------------------------------------------------------
#
# The measured figure answers "what did this run cost". The projection answers
# "what will mine cost", which is a different question with a different standard
# of proof, and these tests exist to keep the two from being confused: the
# projection must scale, must state every input it used, and must never present
# itself as a measurement.


def _evidence(database, *sizes: int) -> None:
    from assuranceos.db.models import EvidenceRecord

    with database.transaction() as session:
        for index, size in enumerate(sizes):
            session.add(
                EvidenceRecord(
                    evidence_id=f"evd_{index}",
                    tenant_id=TENANT,
                    engagement_id="eng_one",
                    record_kind="original",
                    source_type="confluence",
                    source_locator=f"confluence://page/{index}",
                    content_sha256=f"{index:064d}",
                    size_bytes=size,
                    classification="internal",
                    integrity_status="verified",
                    collected_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
                )
            )


def test_the_projection_scales_with_the_document_count(database):
    _evidence(database, 3500, 3500, 3500)
    projection = engagement_economics(database, TENANT)["projection"]

    points = {item["documents"]: item for item in projection["points"]}
    assert set(points) == {50, 500, 5000}
    # A hundredfold more documents costs far more, and not a hundredfold more:
    # the per-audit overhead does not scale with the population.
    assert points[5000]["usd"] > points[500]["usd"] > points[50]["usd"]
    assert points[5000]["usd"] < points[50]["usd"] * 100


def test_the_per_document_size_is_measured_from_this_tenants_evidence(database):
    _evidence(database, 3500, 3500)
    projection = engagement_economics(database, TENANT)["projection"]

    assert projection["measured_inputs"]["documents_measured"] == 2
    assert projection["measured_inputs"]["mean_document_bytes"] == 3500
    assert projection["measured_inputs"]["tokens_per_document"] == 1000


def test_a_tenant_with_no_evidence_says_the_size_could_not_be_measured(database):
    """An unmeasured input must not silently become a default one."""
    projection = engagement_economics(database, TENANT)["projection"]

    assert projection["measured_inputs"]["documents_measured"] == 0
    assert "could not be measured" in projection["caveat"]


def test_every_assumption_travels_with_the_number(database):
    """Same rule as the comparison: a caller cannot render the figure alone."""
    _evidence(database, 4000)
    projection = engagement_economics(database, TENANT)["projection"]

    assert len(projection["assumptions"]) >= 4
    assert any("read 2 times" in item for item in projection["assumptions"])
    assert any("human review time" in item for item in projection["assumptions"])
    assert "projection, not a measurement" in projection["caveat"]


def test_the_introductory_rate_is_shown_beside_the_permanent_one(database):
    _evidence(database, 4000)
    points = engagement_economics(database, TENANT)["projection"]["points"]

    for point in points:
        assert 0 < point["introductory_usd"] < point["usd"]


def test_the_comparison_is_drawn_at_a_stated_audit_size(database):
    """Not divided by whatever this tenant happened to run.

    The measured cost of a demonstration is three model calls. Dividing a salary
    budget by that produced "33 million audits" — arithmetically correct, wildly
    uninformative, and it reads as a lie. The quotient has to name the audit it
    is a quotient of.
    """
    _evidence(database, 3500, 3500)
    with database.transaction() as session:
        session.add(_model_span("s1", model="gemini-3.7-flash", input_tokens=4000, output_tokens=400))

    economics = engagement_economics(database, TENANT)
    comparison, projection = economics["comparison"], economics["projection"]

    assert comparison["run_size_documents"] == 500
    point = next(p for p in projection["points"] if p["documents"] == 500)
    assert comparison["run_cost_usd"] == point["usd"]
    assert comparison["equivalent_runs"] == int(ASSUMED_FUNCTION_COST_USD // point["usd"])
    # And it is nothing like the number the measured run alone would have given.
    assert comparison["equivalent_runs"] < ASSUMED_FUNCTION_COST_USD // economics["cost"]["usd"]
