from __future__ import annotations

from io import StringIO
import json
import pytest

from assuranceos.connectors.exceptions import (
    ConnectorAuthenticationError,
    ConnectorPermissionError,
    ConnectorProtocolError,
)
from assuranceos.connectors.transport import (
    HttpRequest,
    HttpResponse,
    HttpxTransport,
    validate_response,
)
from assuranceos.control_testing import python_worker


def test_python_worker_applies_all_resource_limits(monkeypatch):
    calls = []

    class Resource:
        RLIMIT_DATA = 1
        RLIMIT_CPU = 2
        RLIMIT_FSIZE = 3
        RLIMIT_NOFILE = 4

        @staticmethod
        def setrlimit(kind, value):
            calls.append((kind, value))

    monkeypatch.setattr(python_worker, "resource", Resource)
    python_worker._limits(
        {"limits": {"memory_mb": 2, "cpu_seconds": 3, "max_output_bytes": 4096}}
    )
    assert calls == [
        (1, (2 * 1024 * 1024, 2 * 1024 * 1024)),
        (2, (3, 3)),
        (3, (4096, 4096)),
        (4, (64, 64)),
    ]
    with pytest.raises(PermissionError, match="network access"):
        python_worker._deny_network()


def test_python_worker_executes_typed_entrypoint(tmp_path, monkeypatch):
    module = tmp_path / "released_test.py"
    module.write_text(
        "def run(*, datasets, parameters, context):\n"
        "    return {'rows': len(datasets), 'threshold': parameters['threshold'], "
        "'tenant': context['tenant_id']}\n",
        encoding="utf-8",
    )
    payload = {
        "module_path": str(module),
        "function_name": "run",
        "datasets": [{"id": 1}],
        "parameters": {"threshold": 7},
        "context": {"tenant_id": "tnt_a"},
        "limits": {"memory_mb": 32, "cpu_seconds": 2, "max_output_bytes": 4096},
        "allow_degraded_sandbox": True,
    }
    output = StringIO()
    monkeypatch.setattr(python_worker, "resource", None)
    monkeypatch.setattr(python_worker.sys, "stdin", StringIO(json.dumps(payload)))
    monkeypatch.setattr(python_worker.sys, "stdout", output)
    monkeypatch.setattr(python_worker.os, "environ", {})
    original_socket = python_worker.socket.socket
    original_create_connection = python_worker.socket.create_connection
    try:
        python_worker.main()
    finally:
        python_worker.socket.socket = original_socket
        python_worker.socket.create_connection = original_create_connection
    assert json.loads(output.getvalue()) == {
        "rows": 1,
        "tenant": "tnt_a",
        "threshold": 7,
    }


def test_httpx_transport_retries_bounded_transient_failure():
    class RawResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self.headers = {}
            self._body = body
            self.text = ""

        def json(self):
            return self._body

    class Client:
        def __init__(self):
            self.responses = [
                RawResponse(503, {"error": "busy"}),
                RawResponse(200, {"ok": True}),
            ]

        def request(self, *args, **kwargs):
            return self.responses.pop(0)

    delays = []
    response = HttpxTransport(client=Client(), sleep_fn=delays.append).send(
        HttpRequest(method="GET", url="https://provider.example/items")
    )
    assert response.json_body == {"ok": True}
    assert delays == [1.0]


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, ConnectorAuthenticationError),
        (403, ConnectorPermissionError),
        (418, ConnectorProtocolError),
    ],
)
def test_connector_response_error_taxonomy(status_code, error_type):
    with pytest.raises(error_type):
        validate_response(HttpResponse(status_code=status_code, headers={}, json_body={}))
