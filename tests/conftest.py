from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent

# Load the lab's .env so tests can be run with plain `pytest` as well as `task test`.
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

# The gateway answers the SDK's session DELETE with 202, which the SDK logs as a warning.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)

from lab import evidence  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def stack_is_up():
    try:
        httpx.get("http://127.0.0.1:16686/", timeout=3)
        r = httpx.post("http://127.0.0.1:13000/mcp", timeout=3)
    except httpx.HTTPError as exc:
        pytest.exit(f"The lab is not running ({exc}). Run `task up && task vault:configure` first.", 2)
    assert r.status_code == 401, "gateway should refuse a request without the agent key"


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts from clean logs and zeroed restart counters."""
    evidence.psql(
        "DELETE FROM dev.logs WHERE message LIKE '%OPERATOR NOTE%';"
        "UPDATE dev.services SET restarts = 0, last_restart = NULL;"
        "UPDATE prod.services SET restarts = 0, last_restart = NULL;"
    )


@pytest.fixture
def trace():
    """(trace_id, headers) for one test. The id ties client, gateway, server and Vault spans together."""
    trace_id, traceparent = evidence.new_traceparent()
    return trace_id, {"traceparent": traceparent}
