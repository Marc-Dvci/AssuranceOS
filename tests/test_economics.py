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
