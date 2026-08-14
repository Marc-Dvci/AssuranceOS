"""What one audit cost to run, measured rather than claimed.

Every other read model in this system answers *why should you believe the
conclusion*. This one answers the question a person deciding whether to buy an
audit function actually asks first: **what does one of these cost, and how does
that compare to the alternative?**

The alternative is a team. A small internal-audit function is a handful of
fully-loaded salaries, it works serially, and it still leaves most of the
universe uncovered — the plan screen says so, with the residual risk a human
signed for. So the comparison worth drawing is not "cheaper per hour", it is how
many complete audits a year of that function's budget buys.

Three rules keep this from becoming marketing.

**Everything measured comes from canonical state.** Token counts are the
``gen_ai.usage.*`` attributes the runtime writes onto the model-call span, which
are the server's own reported usage. Wall clock comes from span timestamps.
Population size comes from the signed control test's own run record. Nothing
here is estimated from a rate card or a rule of thumb.

**Cost is priced, never billed.** The tokens may have been served by a model
other than the one whose published rate is applied — the local Gemma path exists
precisely so an engagement can run inside the auditee's network. So the payload
names the model that *served* the tokens separately from the model the price
came from, and a caller that ignores the distinction is the one being dishonest,
not this module.

**A scripted run says it is scripted.** ``ScriptedClient`` reports word counts
as token counts so that offline demonstrations exercise the same accounting
path. Word counts priced at a real rate card produce a real-looking number with
nothing behind it, which is the same failure as an embedding index that ranks
without semantics. When any model call in the engagement was scripted, the
measurement basis says so and every surface showing the figure has to carry it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from .db.models import (
    ControlTestRun,
    Engagement,
    EngagementTask,
    EvidenceRecord,
    ReasoningSpanRecord,
)
from .db.session import Database
from .governance.telemetry import SPAN_MODEL

#: Published list price in USD per million tokens, by model.
#:
#: Source: Google's Gemini 3.7 Flash announcement, 13 August 2026. The
#: introductory rate ($0.75 / $3.75) runs to 31 December 2026 and the permanent
#: rate takes effect on 1 January 2027. The permanent rate is the one applied,
#: because a cost claim that depends on a promotion stops being true on a date
#: the reader cannot see. The introductory figure is reported alongside so the
#: discount is visible rather than baked in.
LIST_PRICE_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "gemini-3.7-flash": (1.50, 7.50),
}

#: The same table at the introductory rate, for the second figure.
INTRODUCTORY_PRICE_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "gemini-3.7-flash": (0.75, 3.75),
}

#: The model whose published price is applied when the engagement was served by
#: something else — a local Gemma, or a scripted client offline.
DEFAULT_PRICED_AS = "gemini-3.7-flash"

#: A declared planning assumption, not a measurement: the fully-loaded annual
#: cost of a four-person internal-audit function. Four FTEs — a lead, two
#: seniors, one associate — at USD 120,000 fully loaded, which is salary,
#: employer charges, tooling and training rather than headline salary. It is a
#: round number on purpose. Every surface that renders the comparison must also
#: render the assumption, because the comparison is only as good as this line
#: and the reader is entitled to substitute their own.
ASSUMED_FUNCTION_COST_USD = 480_000.0
ASSUMED_FUNCTION_HEADCOUNT = 4
ASSUMED_FUNCTION_BASIS = (
    "four fully-loaded internal-audit FTEs at USD 120,000 each — a declared "
    "planning assumption, not a measurement"
)

#: The runtime opens this span for every generation, and it is the only place
#: token usage is recorded. Imported rather than spelled out so a rename in the
#: tracer cannot silently empty this view.
_MODEL_SPAN = SPAN_MODEL

#: Reported by ``ScriptedClient``. Its token counts are word counts.
_SCRIPTED_MODELS = frozenset({"scripted", "unknown", ""})


@dataclass(frozen=True)
class ModelUsage:
    """Usage attributable to one model within one engagement."""

    model: str
    calls: int
    input_tokens: int
    output_tokens: int

    @property
    def metered(self) -> bool:
        """False when the numbers are a scripted client's word counts."""
        return self.model not in _SCRIPTED_MODELS


def engagement_economics(
    database: Database,
    tenant_id: str,
    *,
    engagement_id: str | None = None,
    priced_as: str = DEFAULT_PRICED_AS,
    function_cost_usd: float = ASSUMED_FUNCTION_COST_USD,
) -> dict[str, Any]:
    """What the audit programme consumed, and what that costs at published rates.

    Scoped to the whole tenant by default, and that is deliberate rather than
    lazy. An audit *function* is the unit the cost comparison is about: a team
    is hired for a year and covers a programme, not one engagement. Scoping to a
    single engagement also misreports this system specifically, because work is
    split across engagements by design — collection lands on one, the signed
    population test on another, the report on a third — so any single one shows
    a fraction of what the programme cost and none of them is wrong.

    Pass ``engagement_id`` to narrow it, which is what a per-audit view wants.
    A tenant that has run nothing returns zeroed measurements rather than
    nothing, so a caller can render the card before the first lease.
    """
    with database.read_session() as session:
        engagements = list(
            session.scalars(select(Engagement).where(Engagement.tenant_id == tenant_id))
        )
        spans = list(
            session.scalars(
                select(ReasoningSpanRecord).where(
                    ReasoningSpanRecord.tenant_id == tenant_id
                )
            )
        )
        tasks = list(
            session.scalars(
                select(EngagementTask).where(EngagementTask.tenant_id == tenant_id)
            )
        )
        runs = list(
            session.scalars(
                select(ControlTestRun).where(ControlTestRun.tenant_id == tenant_id)
            )
        )
        evidence = list(
            session.scalars(
                select(EvidenceRecord).where(EvidenceRecord.tenant_id == tenant_id)
            )
        )

    selected = (
        next(
            (item for item in engagements if item.engagement_id == engagement_id),
            None,
        )
        if engagement_id is not None
        else None
    )
    if engagement_id is not None and selected is None:
        return {
            "engagement": None,
            "scope": "engagement",
            "engagements": 0,
            "measured": _measured([], [], [], []),
            "models": [],
            "cost": _cost([], priced_as=priced_as),
            "measurement": "none",
            "caveat": f"No engagement {engagement_id!r} in this tenant.",
            "comparison": _comparison(0.0, function_cost_usd, basis="none"),
        }

    def _in_scope(value: str | None) -> bool:
        if selected is None:
            return True
        return value == selected.engagement_id

    scoped_spans = [span for span in spans if _in_scope(span.engagement_id)]
    scoped_tasks = [task for task in tasks if _in_scope(task.engagement_id)]
    scoped_runs = [run for run in runs if _in_scope(run.engagement_id)]
    scoped_evidence = [
        record for record in evidence if _in_scope(record.engagement_id)
    ]

    usage = _usage(scoped_spans)
    cost = _cost(usage, priced_as=priced_as)
    basis = _measurement_basis(usage)
    return {
        "engagement": (
            {
                "engagement_id": selected.engagement_id,
                "code": selected.code,
                "title": selected.title,
                "status": selected.status,
            }
            if selected is not None
            else None
        ),
        "scope": "engagement" if selected is not None else "programme",
        "engagements": 1 if selected is not None else len(engagements),
        "measured": _measured(scoped_spans, scoped_tasks, scoped_runs, scoped_evidence),
        "models": [
            {
                "model": item.model,
                "calls": item.calls,
                "input_tokens": item.input_tokens,
                "output_tokens": item.output_tokens,
                "metered": item.metered,
            }
            for item in usage
        ],
        "cost": cost,
        "measurement": basis,
        "caveat": _caveat(basis, usage, priced_as),
        "comparison": _comparison(cost["usd"], function_cost_usd, basis=basis),
    }


def _usage(spans: Iterable[ReasoningSpanRecord]) -> list[ModelUsage]:
    """Token usage per model, from the server's own reported counts."""
    totals: dict[str, list[int]] = {}
    for span in spans:
        if span.name != _MODEL_SPAN:
            continue
        attributes = span.attributes_json or {}
        model = str(attributes.get("gen_ai.response.model") or "unknown")
        entry = totals.setdefault(model, [0, 0, 0])
        entry[0] += 1
        entry[1] += int(attributes.get("gen_ai.usage.input_tokens") or 0)
        entry[2] += int(attributes.get("gen_ai.usage.output_tokens") or 0)
    return [
        ModelUsage(model=model, calls=calls, input_tokens=inp, output_tokens=out)
        for model, (calls, inp, out) in sorted(totals.items())
    ]


def _measured(
    spans: list[ReasoningSpanRecord],
    tasks: list[EngagementTask],
    runs: list[ControlTestRun],
    evidence: list[EvidenceRecord],
) -> dict[str, Any]:
    model_spans = [span for span in spans if span.name == _MODEL_SPAN]
    return {
        "model_calls": len(model_spans),
        "input_tokens": sum(_tokens(span, "input") for span in model_spans),
        "output_tokens": sum(_tokens(span, "output") for span in model_spans),
        # Wall clock across the whole engagement, including the time it spent
        # waiting on a lease or a human. This is elapsed time for the audit, not
        # billed compute, and the two differ by design: work that resumes over
        # days is the point of the orchestrator.
        "wall_clock_seconds": _wall_clock(spans),
        # Time the fleet was actually executing. The gap between this and wall
        # clock is what an asynchronous engagement buys back.
        "agent_seconds": round(
            sum((span.duration_ms or 0.0) for span in model_spans) / 1000.0, 3
        ),
        "tasks": len(tasks),
        "task_attempts": sum(task.attempt_count for task in tasks),
        # Records the signed control tests actually examined. Populations, not
        # samples — the number a reviewer would otherwise have reconciled by
        # hand.
        "population_records": sum(run.population_count or 0 for run in runs),
        "control_tests": len(runs),
        "evidence_records": len(evidence),
        # The only human input the run required. Counted as decisions rather
        # than minutes: how long an approval takes is not something this system
        # measures, and inventing it would undo the point of the card.
        "human_decisions": sum(1 for task in tasks if task.human_gate),
    }


def _tokens(span: ReasoningSpanRecord, direction: str) -> int:
    attributes = span.attributes_json or {}
    return int(attributes.get(f"gen_ai.usage.{direction}_tokens") or 0)


def _wall_clock(spans: list[ReasoningSpanRecord]) -> float:
    starts = [span.started_at for span in spans if span.started_at]
    ends = [span.ended_at for span in spans if span.ended_at]
    if not starts or not ends:
        return 0.0
    return round(_seconds(min(starts), max(ends)), 3)


def _seconds(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds(), 0.0)


def _cost(usage: list[ModelUsage], *, priced_as: str) -> dict[str, Any]:
    """Published-rate cost for the metered tokens.

    Priced at one model's rate regardless of which model served the tokens, and
    the payload says so. A run served by a local Gemma has no invoice at all;
    what the figure answers is "what would this audit cost on the hosted path",
    which is the question a buyer is asking.
    """
    input_tokens = sum(item.input_tokens for item in usage)
    output_tokens = sum(item.output_tokens for item in usage)
    list_in, list_out = LIST_PRICE_USD_PER_MILLION.get(priced_as, (0.0, 0.0))
    intro_in, intro_out = INTRODUCTORY_PRICE_USD_PER_MILLION.get(priced_as, (0.0, 0.0))
    return {
        "priced_as": priced_as,
        "price_basis": "published list price, effective 1 January 2027",
        "input_usd_per_million": list_in,
        "output_usd_per_million": list_out,
        "usd": round(
            (input_tokens / 1_000_000) * list_in
            + (output_tokens / 1_000_000) * list_out,
            6,
        ),
        "introductory_usd": round(
            (input_tokens / 1_000_000) * intro_in
            + (output_tokens / 1_000_000) * intro_out,
            6,
        ),
        "introductory_note": (
            "Google's introductory rate for this model runs to 31 December 2026; "
            "the headline figure uses the permanent rate that follows it."
        ),
    }


def _measurement_basis(usage: list[ModelUsage]) -> str:
    if not usage:
        return "none"
    metered = [item for item in usage if item.metered]
    if not metered:
        return "scripted"
    if len(metered) != len(usage):
        return "mixed"
    return "metered"


def _caveat(basis: str, usage: list[ModelUsage], priced_as: str) -> str | None:
    """The sentence a surface must print beside the number, or None.

    Returned as text rather than as a flag so that a caller cannot render the
    figure and quietly drop the qualification.
    """
    if basis == "none":
        return "No model call has been recorded yet."
    if basis == "scripted":
        return (
            "This ran on the scripted client, whose token counts are word "
            "counts. The cost is arithmetic on those counts, not a measurement "
            "of a model, so no comparison is drawn from it."
        )
    if basis == "mixed":
        return (
            "Some model calls here were scripted. Their word counts are "
            "included in the total and are not measured usage."
        )
    served = sorted({item.model for item in usage})
    if served != [priced_as]:
        return (
            f"Tokens were served by {', '.join(served)} and priced at the "
            f"published {priced_as} rate. Nothing was billed at that rate."
        )
    return None


def _comparison(
    cost_usd: float, function_cost_usd: float, *, basis: str
) -> dict[str, Any]:
    """One year of a small audit function, expressed in audit runs.

    Deliberately the weaker direction of the comparison. It does not claim the
    software replaces the team — the plan screen already says a third of the
    universe stays uncovered and a human signed for it. It says what a year of
    that budget buys in complete, evidenced runs, which is a fact about scale
    rather than a claim about substitution.

    Computed only against metered usage. Dividing a real salary budget by
    arithmetic on a scripted client's word counts produces a very large number
    that means nothing, and a very large number that means nothing is worse on
    a page like this than no number at all.
    """
    runs = (
        int(function_cost_usd // cost_usd)
        if cost_usd > 0 and basis == "metered"
        else None
    )
    return {
        "annual_function_cost_usd": function_cost_usd,
        "headcount": ASSUMED_FUNCTION_HEADCOUNT,
        "assumption": ASSUMED_FUNCTION_BASIS,
        "equivalent_runs": runs,
    }
