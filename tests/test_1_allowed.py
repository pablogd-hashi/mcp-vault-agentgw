"""Test 1: the boring one. A dev log read is allowed by every layer."""

from lab import evidence
from lab.client import call, session


async def test_dev_read_is_allowed(trace):
    trace_id, headers = trace
    async with session(headers=headers) as s:
        tools = sorted(t.name for t in (await s.list_tools()).tools)
        out = await call(s, "read_logs", env="dev")

    assert tools == ["read_logs", "restart_service"]
    assert not out.is_error, out.text
    assert out.structured["lines"], "expected log lines back"
    # The query ran as a Vault-issued user, not as a static account.
    assert out.structured["db_user"].startswith("v-approle-logs-rea")

    spans = evidence.spans(trace_id, want_service="mcp-server")
    tool = next(s for s in spans if s["name"] == "tool read_logs")
    assert tool["tags"]["lab.decision"] == "allow"
    lease = next(s for s in spans if s["name"] == "vault.database.creds")
    assert lease["tags"]["vault.role"] == "logs-reader-dev"
    assert lease["tags"]["vault.lease_duration_s"] == 300

    logged = evidence.tool_calls_in_log(trace_id)
    assert len(logged) == 1 and "error" not in logged[0]
    assert logged[0]["caller"] == "oncall-agent"

    evidence.record(
        "test_1_allowed",
        {
            "trace_id": trace_id,
            "db_user": out.structured["db_user"],
            "span_services": sorted({s["service"] for s in spans}),
            "span_names": [f'{s["service"]}: {s["name"]}' for s in spans],
            "gateway_log": logged[0],
        },
    )


async def test_each_call_gets_its_own_credential(trace):
    _, headers = trace
    async with session(headers=headers) as s:
        first = await call(s, "read_logs", env="dev")
        second = await call(s, "read_logs", env="dev")
    assert first.structured["db_user"] != second.structured["db_user"]
