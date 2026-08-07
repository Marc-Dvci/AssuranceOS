from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

try:  # POSIX only; Windows exposes no equivalent rlimit interface.
    import resource
except ImportError:  # pragma: no cover - exercised on Windows developer machines
    resource = None  # type: ignore[assignment]


def _deny_network(*_: Any, **__: Any) -> None:
    raise PermissionError("network access is denied for deterministic tests")


def _limits(payload: dict[str, Any]) -> None:
    """Apply hard resource limits, or refuse to run when they cannot be enforced.

    The caller states whether an unenforced sandbox is acceptable. Silently
    skipping the limits would leave the isolation guarantee untested on the very
    platform that lacks it, so the degraded path must be requested explicitly.
    """
    if resource is None:
        if payload.get("allow_degraded_sandbox"):
            return
        raise RuntimeError(
            "resource limits cannot be enforced on this platform; deterministic "
            "control tests refuse to run without an enforced sandbox"
        )
    limits = payload["limits"]
    memory = int(limits["memory_mb"]) * 1024 * 1024
    cpu = int(limits["cpu_seconds"])
    output = int(limits["max_output_bytes"])
    if hasattr(resource, "RLIMIT_DATA"):
        resource.setrlimit(resource.RLIMIT_DATA, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (output, output))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def main() -> None:
    payload = json.load(sys.stdin)
    _limits(payload)
    socket.socket = _deny_network  # type: ignore[assignment]
    socket.create_connection = _deny_network  # type: ignore[assignment]
    os.environ.clear()
    os.environ.update({"PYTHONHASHSEED": "0", "TZ": "UTC", "LANG": "C.UTF-8"})

    module_path = Path(payload["module_path"]).resolve()
    function_name = payload["function_name"]
    spec = importlib.util.spec_from_file_location("assurance_control_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load control test module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, function_name)
    result = function(
        datasets=payload["datasets"],
        parameters=payload["parameters"],
        context=payload["context"],
    )
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > int(payload["limits"]["max_output_bytes"]):
        raise RuntimeError("control-test output exceeds configured limit")
    sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
