from __future__ import annotations

import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import TestExecutionError, TestExecutionTimeoutError
from .registry import LoadedControlTest


@dataclass(frozen=True)
class ExecutionOutput:
    value: dict[str, Any]
    environment: dict[str, Any]


def _resource_limits_available() -> bool:
    try:
        import resource  # noqa: F401
    except ImportError:
        return False
    return True


class DeterministicRuntime:
    """Executes released Python or SQL tests with bounded, network-denied local isolation.

    The process boundary is intentionally provider-neutral. Production deployments can replace
    this adapter with a Cloud Run Job or hardened sandbox while preserving the package contract.

    Hard memory and CPU limits require the POSIX ``resource`` interface. Platforms without it
    (Windows developer machines) can only run with ``allow_degraded_sandbox=True``, which is
    recorded in the execution environment and rejected by the production configuration.
    """

    def __init__(self, *, allow_degraded_sandbox: bool | None = None):
        if allow_degraded_sandbox is None:
            from ..config import settings

            allow_degraded_sandbox = settings.control_test_allow_degraded_sandbox
        self.allow_degraded_sandbox = allow_degraded_sandbox

    @property
    def resource_limits_enforced(self) -> bool:
        return _resource_limits_available()

    def execute(
        self,
        release: LoadedControlTest,
        *,
        datasets: dict[str, list[dict[str, Any]]],
        parameters: dict[str, Any],
        context: dict[str, Any],
    ) -> ExecutionOutput:
        if release.manifest.engine == "python":
            value = self._python(release, datasets=datasets, parameters=parameters, context=context)
            runtime = "isolated-python-subprocess"
        else:
            value = self._sql(release, datasets=datasets, parameters=parameters, context=context)
            runtime = "sqlite-read-only"
        return ExecutionOutput(
            value=value,
            environment={
                "runtime": runtime,
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "byteorder": sys.byteorder,
                "timezone": "UTC",
                "resource_limits_enforced": self.resource_limits_enforced,
            },
        )

    def _python(
        self,
        release: LoadedControlTest,
        *,
        datasets: dict[str, list[dict[str, Any]]],
        parameters: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        module_name, separator, function_name = release.manifest.entrypoint.partition(":")
        if not separator or not function_name:
            raise TestExecutionError("python entrypoint must use path.py:function syntax")
        module_path = (release.package_dir / module_name).resolve()
        if release.package_dir not in module_path.parents or not module_path.is_file():
            raise TestExecutionError("python entrypoint escapes package or does not exist")
        payload = {
            "module_path": module_path.as_posix(),
            "function_name": function_name,
            "datasets": datasets,
            "parameters": parameters,
            "context": context,
            "limits": release.manifest.resources.model_dump(mode="json"),
            "allow_degraded_sandbox": self.allow_degraded_sandbox,
        }
        worker = Path(__file__).with_name("python_worker.py")
        env = {
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": os.environ.get("PATH", ""),
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-I", worker.as_posix()],
                input=json.dumps(payload, sort_keys=True).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=release.manifest.resources.timeout_seconds,
                check=False,
                env=env,
                cwd=tempfile.gettempdir(),
            )
        except subprocess.TimeoutExpired as exc:
            raise TestExecutionTimeoutError(
                f"control test exceeded {release.manifest.resources.timeout_seconds}s timeout"
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")[-4000:]
            raise TestExecutionError(f"control-test subprocess failed: {stderr}")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TestExecutionError("control-test subprocess returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise TestExecutionError("control-test result must be a JSON object")
        return value

    def _sql(
        self,
        release: LoadedControlTest,
        *,
        datasets: dict[str, list[dict[str, Any]]],
        parameters: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        sql_path = (release.package_dir / release.manifest.entrypoint).resolve()
        if release.package_dir not in sql_path.parents or not sql_path.is_file():
            raise TestExecutionError("SQL entrypoint escapes package or does not exist")
        statement = sql_path.read_text(encoding="utf-8").strip()
        normalized = statement.lstrip().lower()
        if not (normalized.startswith("select") or normalized.startswith("with")):
            raise TestExecutionError("SQL control tests must be a single SELECT or WITH query")
        if ";" in statement.rstrip(";"):
            raise TestExecutionError("SQL control tests must contain one statement")
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            for name, records in datasets.items():
                self._load_table(connection, name, records)
            connection.execute("CREATE TABLE __context (json TEXT NOT NULL)")
            connection.execute("INSERT INTO __context VALUES (?)", (json.dumps(context),))
            connection.execute("CREATE TABLE __parameters (json TEXT NOT NULL)")
            connection.execute("INSERT INTO __parameters VALUES (?)", (json.dumps(parameters),))
            connection.execute("PRAGMA query_only=ON")
            rows = [dict(row) for row in connection.execute(statement)]
        except sqlite3.Error as exc:
            raise TestExecutionError(f"SQL control test failed: {exc}") from exc
        finally:
            connection.close()
        exceptions = []
        for row in rows:
            if bool(row.get("is_exception")):
                attributes = {
                    key: value
                    for key, value in row.items()
                    if key
                    not in {
                        "exception_key",
                        "subject_ref",
                        "classification",
                        "severity",
                        "status",
                        "reason",
                        "is_exception",
                    }
                }
                exceptions.append(
                    {
                        "exception_key": str(row.get("exception_key") or row.get("subject_ref")),
                        "subject_ref": str(row.get("subject_ref")),
                        "classification": str(row.get("classification", "control_exception")),
                        "severity": str(row.get("severity", "medium")),
                        "status": str(row.get("status", "open")),
                        "reason": str(row.get("reason", "deterministic SQL exception")),
                        "attributes": attributes,
                        "evidence_ids": [],
                    }
                )
        conclusion = "ineffective" if exceptions else "effective"
        return {"conclusion": conclusion, "rows": rows, "exceptions": exceptions}

    @staticmethod
    def _load_table(
        connection: sqlite3.Connection, name: str, records: list[dict[str, Any]]
    ) -> None:
        if not name.replace("_", "").isalnum() or name[0].isdigit():
            raise TestExecutionError(f"unsafe dataset name: {name}")
        columns = sorted({key for row in records for key in row})
        if not columns:
            connection.execute(f'CREATE TABLE "{name}" (__empty INTEGER)')
            return
        definitions = ", ".join(f'"{column}"' for column in columns)
        connection.execute(f'CREATE TABLE "{name}" ({definitions})')
        placeholders = ", ".join("?" for _ in columns)
        quoted = ", ".join(f'"{column}"' for column in columns)
        for row in records:
            values = [DeterministicRuntime._sqlite_value(row.get(column)) for column in columns]
            connection.execute(
                f'INSERT INTO "{name}" ({quoted}) VALUES ({placeholders})', values
            )

    @staticmethod
    def _sqlite_value(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        if isinstance(value, bool):
            return int(value)
        return value
