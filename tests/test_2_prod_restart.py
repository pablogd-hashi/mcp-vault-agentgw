"""Test 2: the gateway refuses restart_service(env=prod) because of its arguments."""

from lab import evidence
from lab.client import call, session


async def test_prod_restart_is_denied_by_the_gateway(trace):
    trace_id, headers = trace
    async with session(headers=headers) as s:
        dev = await call(s, "restart_service", env="dev", name="payments")
        prod = await call(s, "restart_service", env="prod", name="payments")

    # Same tool, same caller: only the argument differs.
    assert not dev.is_error, dev.text
    assert prod.is_error
    assert "Denied by gateway policy" in prod.text

    # Nothing happened in prod, and the dev restart did.
    assert evidence.restarts("prod") == 0
    assert evidence.restarts("dev") == 1

    # The MCP server saw exactly one tool call (the dev one). The prod call never arrived.
    spans = evidence.spans(trace_id, want_service="mcp-server")
    server_tools = [s for s in spans if s["service"] == "mcp-server" and s["name"].startswith("tool ")]
    assert [s["tags"]["lab.env"] for s in server_tools] == ["dev"]

    # The audit trail has the denied call with its arguments and the caller.
    logged = evidence.tool_calls_in_log(trace_id)
    denied = [e for e in logged if "error" in e]
    assert len(denied) == 1
    assert "mcpGuardrails rejected" in denied[0]["error"]
    assert denied[0]["tool_args"] == {"env": "prod", "name": "payments"}
    assert denied[0]["caller"] == "lab-agent"

    evidence.record(
        "test_2_prod_restart",
        {
            "trace_id": trace_id,
            "client_sees": {"isError": prod.is_error, "text": prod.text},
            "gateway_log": denied[0],
            "mcp_server_tool_spans": [s["tags"] for s in server_tools],
        },
    )
