from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from alembic import context  # noqa: E402
from sqlalchemy import engine_from_config, pool  # noqa: E402

from assuranceos.db.base import Base  # noqa: E402
import assuranceos.db.models  # noqa: E402, F401 - imported to register mapped tables


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = os.getenv("ASSURANCEOS_DATABASE_URL")
if url:
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        # SQLite cannot recreate a referenced table while foreign-key enforcement is on.
        # Alembic batch migrations perform that recreation, so enforcement is disabled for
        # the migration connection only and restored after the revision transaction.
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()
        if sqlite:
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
