"""Test 6: the gateway goes away in the middle of a session.

There is no other path to the MCP server, so the expected result is that calls
fail and nothing reaches the backend. The numbers worth keeping are how long a
client waits and what error it gets.
"""

import asyncio
import time

import httpx
import pytest

from lab import evidence
from lab.client import call, session


def wait_for_gateway(timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.post("http://127.0.0.1:13000/mcp", timeout=2).status_code == 401:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise AssertionError("gateway did not come back")


@pytest.fixture
def gateway_restored():
    yield
    evidence.compose("start", "agentgateway")
    wait_for_gateway()


async def test_calls_fail_closed_when_the_gateway_stops(trace, gateway_restored):
    trace_id, headers = trace
    outcome: dict = {}
    started = None

    # The SDK reports a dead transport either on the call itself or when the
    # session's task group unwinds, so both places are treated as "the call failed".
    try:
        async with session(headers=headers) as s:
            before = await call(s, "read_logs", env="dev")
            assert not before.is_error

            evidence.compose("stop", "agentgateway")

            started = time.perf_counter()
            result = await asyncio.wait_for(call(s, "restart_service", env="dev", name="payments"), timeout=30)
            outcome = {"kind": "result", "isError": result.is_error, "text": result.text}
    except AssertionError:
        raise
    except BaseException as exc:
        inner = exc.exceptions[0] if isinstance(exc, BaseExceptionGroup) else exc
        outcome = {"kind": type(inner).__name__, "text": str(inner)[:400]}
    assert started is not None, "the session failed before the gateway was stopped"
    outcome["seconds_to_failure"] = round(time.perf_counter() - started, 3)

    # The call did not succeed, and the restart did not happen.
    assert not (outcome.get("kind") == "result" and outcome.get("isError") is False), outcome
    assert evidence.restarts("dev") == 0

    # A brand new client fails at connect time.
    started = time.perf_counter()
    with pytest.raises(httpx.HTTPError) as fresh:
        httpx.post("http://127.0.0.1:13000/mcp", timeout=10)
    outcome["new_client"] = {
        "error": type(fresh.value).__name__,
        "seconds": round(time.perf_counter() - started, 3),
    }

    print(f"\ngateway down: {outcome}")
    evidence.record("test_6_gateway_down", {"trace_id": trace_id, **outcome})


@pytest.fixture
def gateway_unpaused():
    yield
    evidence.compose("unpause", "agentgateway", check=False)
    wait_for_gateway()


async def test_calls_hang_when_the_gateway_freezes(trace, gateway_unpaused):
    """A stopped gateway refuses connections at once. A frozen one is worse: the
    client waits for as long as its own timeout allows, and the request it gave
    up on is still sitting in the gateway's socket."""
    _, headers = trace
    patience = 8
    async with session(headers=headers) as s:
        assert not (await call(s, "read_logs", env="dev")).is_error
        evidence.compose("pause", "agentgateway")
        started = time.perf_counter()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(call(s, "restart_service", env="dev", name="payments"), timeout=patience)
        waited = round(time.perf_counter() - started, 1)
        # While frozen, nothing reached the backend.
        assert evidence.restarts("dev") == 0
        evidence.compose("unpause", "agentgateway")

    # Recorded, not asserted: whether the abandoned call runs once the gateway wakes up.
    time.sleep(3)
    ran_late = evidence.restarts("dev") == 1
    print(f"\ngateway frozen: client gave up after {waited}s; abandoned call executed after resume: {ran_late}")
    evidence.record(
        "test_6_gateway_frozen",
        {"client_timeout_s": patience, "waited_s": waited, "abandoned_call_executed_after_resume": ran_late},
    )


async def test_service_resumes_after_restart(trace):
    wait_for_gateway()
    _, headers = trace
    async with session(headers=headers) as s:
        out = await call(s, "read_logs", env="dev")
    assert not out.is_error
