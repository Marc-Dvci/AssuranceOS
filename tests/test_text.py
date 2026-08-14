from __future__ import annotations

import pytest

from assuranceos.text import counted, plural


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 exceptions"), (1, "1 exception"), (2, "2 exceptions"), (43, "43 exceptions")],
)
def test_counted_agrees_with_its_number(count: int, expected: str) -> None:
    assert counted(count, "exception") == expected


def test_zero_is_plural_in_english() -> None:
    """"0 exceptions", not "0 exception" — the boundary the naive rule gets wrong."""
    assert plural(0, "finding") == "findings"


def test_an_irregular_plural_can_be_given() -> None:
    assert counted(2, "analysis", "analyses") == "2 analyses"
    assert counted(1, "analysis", "analyses") == "1 analysis"
