"""Test 3: the gateway rule is too broad on purpose, and Vault catches what it lets through."""

from lab import evidence
from lab.client import call, session


async def test_prod_read_passes_the_gateway_and_is_denied_by_vault(trace):
    trace_id, headers = trace
    async with session(headers=headers) as s:
        out = await call(s, "read_logs", env="prod")

    assert out.is_error
    assert "Vault denied a credential for role 'logs-reader-prod'" in out.text
    assert "403" in out.text

    # The gateway allowed it: the request log has the call and no gateway error.
    logged = evidence.tool_calls_in_log(trace_id)
    assert len(logged) == 1
    assert "error" not in logged[0]
    assert logged[0]["tool_args"] == {"env": "prod"}

    # The denial is one hop deeper: a Vault span inside the MCP server.
    spans = evidence.spans(trace_id, want_service="mcp-server")
    lease = next(s for s in spans if s["name"] == "vault.database.creds")
    assert lease["tags"]["http.response.status_code"] == 403
    assert lease["tags"]["lab.layer"] == "vault"
    assert not [s for s in spans if s["name"] == "postgres.query"], "no query may run without a credential"

    evidence.record(
        "test_3_vault_denial",
        {
            "trace_id": trace_id,
            "client_sees": {"isError": out.is_error, "text": out.text},
            "gateway_log": logged[0],
            "vault_span": lease["tags"],
            "span_names": [f'{s["service"]}: {s["name"]}' for s in spans],
        },
    )
