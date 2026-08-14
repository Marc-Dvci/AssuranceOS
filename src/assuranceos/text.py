"""Small helpers for text that a person reads.

These strings are not logs. An observed condition and a quality-review reason
are carried into the finding register, into the finding review screen, and into
the issued report — so "3 exception(s) identified" is not a formatting detail,
it is the sentence a reviewer signs their name under.
"""

from __future__ import annotations


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``2, "exception"`` -> ``"exceptions"``; ``1, "exception"`` -> ``"exception"``.

    Pass ``plural_form`` where adding an ``s`` is wrong.
    """
    if count == 1:
        return singular
    return plural_form if plural_form is not None else f"{singular}s"


def counted(count: int, singular: str, plural_form: str | None = None) -> str:
    """``2, "exception"`` -> ``"2 exceptions"``."""
    return f"{count} {plural(count, singular, plural_form)}"
