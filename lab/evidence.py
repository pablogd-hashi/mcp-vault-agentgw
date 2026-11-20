"""Where the tests get their proof from: Jaeger, the gateway access log, Postgres.

These run on the host and shell out to `docker compose`, so they only work from
the repository root (pytest and the Taskfile both run there).
"""

from __future__ import annotations

import json
import secrets
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
JAEGER = "http://127.0.0.1:16686"


def compose(*args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, check=check, timeout=timeout
    )


def new_traceparent() -> tuple[str, str]:
    """A fresh W3C trace context. Sent on the MCP session so every hop joins one trace."""
    trace_id = secrets.token_hex(16)
    return trace_id, f"00-{trace_id}-{secrets.token_hex(8)}-01"


def spans(trace_id: str, *, want_service: str | None = None, wait: float = 12.0) -> list[dict]:
    """All spans of a trace as flat dicts: service, name, tags. Waits for export to settle."""

    def read() -> list[dict]:
        r = httpx.get(f"{JAEGER}/api/traces/{trace_id}", timeout=5)
        if r.status_code != 200 or not r.json().get("data"):
            return []
        t = r.json()["data"][0]
        procs = {k: v["serviceName"] for k, v in t["processes"].items()}
        return [
            {
                "service": procs[s["processID"]],
                "name": s["operationName"],
                "tags": {x["key"]: x["value"] for x in s["tags"]},
            }
            for s in t["spans"]
        ]

    deadline = time.time() + wait
    while time.time() < deadline:
        found = read()
        if found and (want_service is None or any(s["service"] == want_service for s in found)):
            break
        time.sleep(0.5)
    # Spans arrive in batches from three exporters; read once more after they settle.
    time.sleep(2)
    return read()


def spans_since(service: str, operation: str, start_us: int) -> list[dict]:
    """Spans of one operation recorded after a point in time (microseconds since epoch)."""
    r = httpx.get(
        f"{JAEGER}/api/traces",
        params={"service": service, "operation": operation, "start": start_us, "end": int(time.time() * 1e6), "limit": 50},
        timeout=5,
    )
    out = []
    for t in r.json().get("data") or []:
        for s in t["spans"]:
            if s["operationName"] == operation and s["startTime"] >= start_us:
                out.append({"name": s["operationName"], "tags": {x["key"]: x["value"] for x in s["tags"]}})
    return out


def gateway_log(since_seconds: int = 120) -> list[dict]:
    """The gateway's request log (its audit trail), parsed. One dict per request."""
    out = compose("logs", "--no-log-prefix", "--since", f"{since_seconds}s", "agentgateway").stdout
    lines = []
    for raw in out.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("scope") == "request":
            lines.append(entry)
    return lines


def tool_calls_in_log(trace_id: str, wait: float = 5.0) -> list[dict]:
    deadline = time.time() + wait
    while True:
        hits = [
            e for e in gateway_log() if e.get("trace.id") == trace_id and e.get("mcp.method.name") == "tools/call"
        ]
        if hits or time.time() > deadline:
            return hits
        time.sleep(0.5)


def psql(sql: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-qAt", "-U", "postgres", "-d", "lab", "-c", sql).stdout.strip()


def restarts(env: str, name: str = "payments") -> int:
    return int(psql(f"SELECT restarts FROM {env}.services WHERE name = '{name}'"))


def record(test: str, data: dict) -> None:
    """Keep what a test observed, for RESULTS.md and the write-up."""
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / f"{test}.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
