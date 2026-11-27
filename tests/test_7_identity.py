"""Test 7: who is calling, and what that changes.

The agent's credential for the gateway is a five-minute JWT that Vault signs
for one named agent. Four things are checked:
  a. the token is short-lived, addressed to the gateway, and names a Vault entity;
  b. the gateway refuses a missing, altered or misaddressed token;
  c. the same call gets a different answer for a different identity;
  d. an identity alone gives no access to data: the agent's Vault token cannot lease anything.
"""

import base64
import json

import httpx
import pytest

from lab import evidence
from lab.client import GATEWAY_URL, call, session
from lab.identity import VAULT, agent_jwt, claims, vault_login

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "identity", "version": "1"}},
}


def knock(token: str | None) -> int:
    """HTTP status the gateway gives an MCP initialize carrying this token."""
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.post(GATEWAY_URL, json=INIT, headers=headers, timeout=10).status_code


def with_claim(token: str, **changes) -> str:
    """The same token with its payload edited and the original signature kept."""
    header, _, signature = token.split(".")
    payload = json.dumps({**claims(token), **changes}, separators=(",", ":")).encode()
    return ".".join([header, base64.urlsafe_b64encode(payload).rstrip(b"=").decode(), signature])


def test_token_is_short_lived_and_names_one_agent():
    first, second = claims(agent_jwt("report-agent")), claims(agent_jwt("oncall-agent"))

    assert first["exp"] - first["iat"] == 300
    assert first["aud"] == "mcp-gateway"
    assert first["iss"] == "http://vault:8200/v1/identity/oidc"
    assert (first["agent"], first["role"]) == ("report-agent", "reader")
    assert (second["agent"], second["role"]) == ("oncall-agent", "operator")
    # `sub` is the Vault entity, a stable ID that survives a rotated AppRole secret.
    assert first["sub"] != second["sub"]
    assert claims(agent_jwt("report-agent"))["sub"] == first["sub"]

    evidence.record("test_7a_token", {"report-agent": first, "oncall-agent": second})


def test_gateway_refuses_missing_altered_and_misaddressed_tokens():
    reader = agent_jwt("report-agent")
    attempts = {
        "valid token": knock(reader),
        "no token": knock(None),
        "not a token": knock("abc.def.ghi"),
        # The reader edits its own token to claim the operator role.
        "payload edited to role=operator": knock(with_claim(reader, role="operator")),
        # Signed by the same Vault key for the same agent, but issued for another service.
        "valid token for another audience": knock(agent_jwt("report-agent", audience="other-service")),
    }

    assert attempts.pop("valid token") == 200
    assert all(status == 401 for status in attempts.values()), attempts

    reasons = sorted({e["error"] for e in evidence.gateway_log(30) if e.get("http.status") == 401 and "error" in e})
    evidence.record("test_7b_rejected_tokens", {"http_status": attempts, "gateway_reasons": reasons})


async def test_same_call_different_identity(trace):
    trace_id, headers = trace

    async with session(headers=headers, agent="report-agent") as s:
        reader_tools = sorted(t.name for t in (await s.list_tools()).tools)
        reader_read = await call(s, "read_logs", env="dev")

    # Same tool, same arguments as the operator below. Only the caller differs.
    # The gateway answers a call to a tool this caller may not use with HTTP 400,
    # which the SDK raises when the session unwinds, so the whole session is the attempt.
    try:
        async with session(headers=headers, agent="report-agent") as s:
            attempt = await call(s, "restart_service", env="dev", name="payments")
            reader_restart = {"isError": attempt.is_error, "text": attempt.text}
    except BaseException as exc:
        inner = exc.exceptions[0] if isinstance(exc, BaseExceptionGroup) else exc
        reader_restart = {"isError": True, "text": f"{type(inner).__name__}: {inner}"[:300]}
    restarts_after_reader = evidence.restarts("dev")

    async with session(headers=headers, agent="oncall-agent") as s:
        operator_tools = sorted(t.name for t in (await s.list_tools()).tools)
        operator_restart = await call(s, "restart_service", env="dev", name="payments")

    # The reader is not shown the tool, and cannot call it by name either.
    assert reader_tools == ["read_logs"]
    assert not reader_read.is_error, reader_read.text
    assert reader_restart["isError"], reader_restart
    assert restarts_after_reader == 0

    assert operator_tools == ["read_logs", "restart_service"]
    assert not operator_restart.is_error, operator_restart.text
    assert evidence.restarts("dev") == 1

    # The audit trail names the agent on every call, from a claim the caller cannot edit.
    logged = evidence.tool_calls_in_log(trace_id)
    by_caller = {(e["caller"], e["caller_role"], e.get("gen_ai.tool.name"), "error" in e) for e in logged}
    assert ("report-agent", "reader", "read_logs", False) in by_caller
    assert ("oncall-agent", "operator", "restart_service", False) in by_caller
    denied = [e for e in logged if e["caller"] == "report-agent" and e.get("gen_ai.tool.name") == "restart_service"]
    assert len(denied) == 1 and "authorization denied" in denied[0]["error"]

    evidence.record(
        "test_7c_per_identity",
        {
            "trace_id": trace_id,
            "report-agent": {"tools": reader_tools, "restart_service(dev)": reader_restart},
            "oncall-agent": {"tools": operator_tools, "restart_service(dev)": "allowed"},
            "gateway_log": logged,
        },
    )


@pytest.mark.parametrize("role", ["logs-reader-dev", "operator-dev", "logs-reader-prod"])
def test_an_identity_is_not_access_to_data(role):
    """The agent can prove who it is. That proof leases nothing: only the MCP server's AppRole can."""
    r = httpx.get(
        f"{VAULT}/v1/database/creds/{role}", headers={"X-Vault-Token": vault_login("oncall-agent")}, timeout=10
    )
    assert r.status_code == 403
