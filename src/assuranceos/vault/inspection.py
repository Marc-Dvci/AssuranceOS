from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


class ContentInspectionRejected(ValueError):
    pass


@dataclass(frozen=True)
class InspectionResult:
    accepted: bool
    tainted: bool = False
    findings: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


class ContentInspector(Protocol):
    def inspect(self, *, payload: bytes, mime_type: str | None, filename: str | None) -> InspectionResult: ...


class BaselineContentInspector:
    """Deterministic local guardrail used before evidence reaches parsers or model context.

    This is not a substitute for managed malware/DLP products. It blocks the standard EICAR test
    payload, identifies executable content, and taints text containing common prompt-injection
    instructions so downstream tools can isolate it.
    """

    _PROMPT_PATTERNS = (
        re.compile(r"ignore (all|any|the) previous instructions", re.I),
        re.compile(r"reveal (the )?(system|developer) prompt", re.I),
        re.compile(r"call (an? )?unauthorized tool", re.I),
        re.compile(r"exfiltrat(e|ion)", re.I),
    )
    _SECRET_PATTERNS = (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    _EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

    def inspect(self, *, payload: bytes, mime_type: str | None, filename: str | None) -> InspectionResult:
        if self._EICAR in payload:
            return InspectionResult(accepted=False, findings=("malware_test_signature:eicar",))
        findings: list[str] = []
        executable = (mime_type or "").lower() in {
            "application/x-dosexec",
            "application/x-executable",
            "application/vnd.microsoft.portable-executable",
        }
        if executable:
            findings.append("active_content:executable")
        text = payload[:2_000_000].decode("utf-8", errors="ignore")
        prompt_hits = [pattern.pattern for pattern in self._PROMPT_PATTERNS if pattern.search(text)]
        secret_hits = [pattern.pattern for pattern in self._SECRET_PATTERNS if pattern.search(text)]
        if secret_hits:
            findings.append("possible_secret_material")
        if prompt_hits:
            findings.append("prompt_injection_candidate")
        return InspectionResult(
            accepted=True,
            tainted=bool(prompt_hits),
            findings=tuple(findings),
            metadata={
                "inspector": "baseline-v1",
                "filename": filename,
                "prompt_pattern_count": len(prompt_hits),
                "secret_pattern_count": len(secret_hits),
            },
        )
