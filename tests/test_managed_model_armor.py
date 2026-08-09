from __future__ import annotations

import pytest

from assuranceos.governance.managed_armor import (
    GoogleManagedModelArmor,
    verify_model_armor_template,
)


TEMPLATE = "projects/assurance-project/locations/us-central1/templates/assurance-guardrails"


def test_managed_model_armor_allows_only_successful_no_match_results():
    calls = []

    def transport(method, payload):
        calls.append((method, payload))
        return {
            "sanitizationResult": {
                "invocationResult": "SUCCESS",
                "filterMatchState": "NO_MATCH_FOUND",
                "filterResults": {},
            }
        }

    armor = GoogleManagedModelArmor(template=TEMPLATE, transport=transport)
    result = armor.inspect_output("A safe audit conclusion")

    assert result.verdict == "allow"
    assert calls == [
        (
            "sanitizeModelResponse",
            {"modelResponseData": {"text": "A safe audit conclusion"}},
        )
    ]


def test_managed_model_armor_blocks_matches_without_persisting_content():
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"

    def transport(_method, _payload):
        return {
            "sanitizationResult": {
                "invocationResult": "SUCCESS",
                "filterMatchState": "MATCH_FOUND",
                "filterResults": {"sdp": {"sdpFilterResult": {}}},
            }
        }

    result = GoogleManagedModelArmor(template=TEMPLATE, transport=transport).inspect_output(secret)

    assert result.verdict == "block"
    managed = next(item for item in result.findings if item.detector == "google_model_armor")
    assert managed.category == "managed_model_armor"
    assert secret not in managed.detail
    assert "sdp" in managed.detail


def test_managed_model_armor_fails_closed_on_service_error():
    def unavailable(_method, _payload):
        raise TimeoutError("provider unavailable")

    result = GoogleManagedModelArmor(template=TEMPLATE, transport=unavailable).inspect_context(
        "ordinary evidence"
    )

    assert result.verdict == "block"
    assert result.findings[-1].category == "managed_service_failure"


def test_managed_model_armor_rejects_ambiguous_template_names():
    with pytest.raises(ValueError, match="projects"):
        GoogleManagedModelArmor(template="assurance-guardrails")


def test_model_armor_verification_receipt_requires_both_decision_paths():
    def transport(method, _payload):
        return {
            "sanitizationResult": {
                "invocationResult": "SUCCESS",
                "filterMatchState": (
                    "MATCH_FOUND" if method == "sanitizeUserPrompt" else "NO_MATCH_FOUND"
                ),
            }
        }

    receipt = verify_model_armor_template(TEMPLATE, transport=transport)

    assert receipt["schema"] == "assurance.model_armor_verification.v1"
    assert receipt["safe_model_response"] == "NO_MATCH_FOUND"
    assert receipt["adversarial_user_prompt"] == "MATCH_FOUND"
