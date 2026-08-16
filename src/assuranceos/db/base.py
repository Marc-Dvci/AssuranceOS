from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Read a stored timestamp back as an aware one, whatever the backend gave.

    ``DateTime(timezone=True)`` is honoured by PostgreSQL and ignored by SQLite,
    which stores the value and hands it back naive. Comparing that against an
    aware ``now`` raises ``TypeError``, so a comparison written once and correct
    on the deployed database still crashes on the local one -- and it crashes on
    the path where an expiry was actually set, which is the path least likely to
    be exercised before it matters. Everything that compares a persisted
    timestamp goes through here.
    """

    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
