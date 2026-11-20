"""Test 5: skip the gateway and call the MCP server directly.

Two boundaries are checked separately:
  a. network: from the agent's network the MCP server cannot be reached at all;
  b. credential: from the backend network, without the upstream key, it answers 401.
"""

import json
import time

from lab import evidence

INIT = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "bypass", "version": "1"}},
    }
)

# Runs inside the agent container: try the MCP server by name and by address.
REACH = """
import json, socket, sys
out = {"targets": {}}
try:
    addrs = sorted({a[4][0] for a in socket.getaddrinfo("mcp-server", 8000)})
    out["dns"] = "resolved: " + ", ".join(addrs)
except OSError as e:
    addrs = []
    out["dns"] = f"failed: {e}"
for target in addrs + sys.argv[1:]:
    s = socket.socket(); s.settimeout(4)
    try:
        s.connect((target, 8000)); out["targets"][target] = "connected"
    except OSError as e:
        out["targets"][target] = f"failed: {e}"
    finally:
        s.close()
print(json.dumps(out))
"""


def backend_ip() -> str:
    cid = evidence.compose("ps", "-q", "mcp-server").stdout.strip()
    import subprocess

    fmt = '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}'
    nets = subprocess.run(["docker", "inspect", "-f", fmt, cid], capture_output=True, text=True, check=True).stdout
    return next(p.split("=")[1] for p in nets.split() if "backend-net" in p)


def test_agent_network_cannot_reach_the_mcp_server():
    ip = backend_ip()
    run = evidence.compose("run", "--rm", "-T", "agent", "python", "-c", REACH, ip, timeout=600)
    result = json.loads(run.stdout.strip().splitlines()[-1])
    # Not resolvable by name, and not routable by its backend address either.
    assert result["dns"].startswith("failed"), result
    assert result["targets"] and all(v.startswith("failed") for v in result["targets"].values()), result
    evidence.record("test_5a_network", {"mcp_server_backend_ip": ip, "from_agent_container": result})


def test_backend_network_without_the_key_gets_401():
    start_us = int(time.time() * 1e6)
    log_before = len(evidence.gateway_log(since_seconds=5))

    run = evidence.compose(
        "run", "--rm", "-T", "probe",
        "-s", "-o", "/dev/null", "-w", "%{http_code}",
        "-X", "POST", "http://mcp-server:8000/mcp",
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json, text/event-stream",
        "-d", INIT,
    )
    assert run.stdout.strip().endswith("401"), run.stdout

    # The MCP server recorded the rejection.
    deadline = time.time() + 12
    rejected = []
    while time.time() < deadline and not rejected:
        time.sleep(1)
        rejected = evidence.spans_since("mcp-server", "reject missing upstream key", start_us)
    assert rejected and rejected[0]["tags"]["lab.layer"] == "header"

    # The gateway has no record of it, because it never saw the call.
    assert len(evidence.gateway_log(since_seconds=5)) <= log_before

    evidence.record(
        "test_5b_header",
        {"http_status": 401, "mcp_server_span": rejected[0]["tags"], "gateway_log_entries": 0},
    )
