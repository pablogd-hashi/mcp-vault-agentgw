# MCP gateway + Vault lab

An agent that authenticates perfectly can still do the wrong thing. This lab puts three controls around one small MCP server and runs six tests to see which control stops what.

| Question | Control in the lab | Where it stops |
|---|---|---|
| Who is calling? | The agent's key for the gateway | Says nothing about what the call does |
| Should this call happen? | [agentgateway](https://agentgateway.dev) rules on tools and their arguments | Only sees calls that go through it |
| What can the tool touch? | Vault: five-minute Postgres credentials, scoped per role | Never sees the agent or the prompt |

It was designed around Runlayer and built with agentgateway instead, because agentgateway is open source and runs in the same compose file. Everything is local; nothing is exposed beyond `127.0.0.1`.

## What you need

- Docker with Compose, [Task](https://taskfile.dev), Terraform, [uv](https://docs.astral.sh/uv/) and the `vault` CLI.
- For the live agent only: [Ollama](https://ollama.com) on the host with `qwen2.5:3b` pulled. The six tests do not need a model.

## Run it

```bash
task setup            # .env with random lab secrets, Python venv
task up               # Postgres, Vault, MCP server, gateway, collector, Jaeger
task vault:configure  # Terraform: database engine, four roles, AppRole
task test             # the six scenarios
```

Then look at what happened:

```bash
task traces           # Jaeger UI at http://127.0.0.1:16686
task audit            # the gateway's record of tool calls and verdicts
task vault:check      # what the MCP server's AppRole can and cannot lease
task bench            # latency with and without the gateway
task demo:poisoned    # live agent against a poisoned log line, N=5 by default
task down             # stop and remove everything
```

## The six tests

| # | Scenario | Stopped by | Proof |
|---|---|---|---|
| 1 | `read_logs(env=dev)` | Nothing, allowed | Rows returned under a Vault-issued database user |
| 2 | `restart_service(env=prod)` | Gateway rule on the `env` argument | Denial in the tool result and the gateway log; no MCP server span |
| 3 | `read_logs(env=prod)` | Vault policy (the gateway rule is too broad on purpose) | HTTP 403 span inside the MCP server |
| 4 | A log line tells the agent to restart prod | Same gateway rule as test 2 | The line reaches the client; the replayed call is denied |
| 5 | Calling the MCP server directly | Network, then the upstream key | Unreachable from the agent's network; 401 from the backend network |
| 6 | Gateway stopped or frozen mid-session | Nothing to stop: there is no other path | Connection error at once when stopped; client timeout when frozen |

Results from a real run, and the command behind each number, are in [RESULTS.md](RESULTS.md). How the pieces connect is in [architecture.md](architecture.md). When something does not start, see [troubleshooting.md](troubleshooting.md).

## Where things are

| Path | What |
|---|---|
| `gateway/config.yaml` | The whole gateway policy: caller key, tool rules, argument rule, upstream key, tracing, access log |
| `terraform/vault/` | Database secrets engine, the four roles, the AppRole and its policy |
| `mcp-server/server.py` | Two tools, Vault client, upstream key check, spans |
| `agent/` | smolagents agent on a local model; `injection_rate.py` for the live half of test 4 |
| `tests/` | One file per scenario |
| `lab/` | Shared client, evidence helpers (Jaeger, gateway log, Postgres), telemetry bootstrap |
| `seed/` | Schema and log rows; `poison.sql` is the injected row |

## Limits worth knowing

- The agent's identity to the gateway is a static key. A short-lived JWT from Vault, checked by the gateway, is the obvious next step and is not built here.
- The upstream key between gateway and MCP server is also static.
- Nothing scans tool results for injected instructions. Test 4 shows the rule holding, and `task demo:poisoned` shows how often the model tries.
- Vault runs in dev mode and Postgres has no TLS. This is a laptop lab.
