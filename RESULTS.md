# Results

These results are from runs of the Model Context Protocol (MCP) gateway and Vault lab on November 20, 2026, on an Apple Silicon laptop. The runs used agentgateway 1.7.0, Vault 1.20 in dev mode, Postgres 16, and `qwen2.5:3b` on Ollama. The test 7 results are from a run on November 27, 2026.

`task test` writes the raw evidence for each test to `results/*.json`.

## Test results

Each row gives the observed result and the command that reproduces it:

| # | Result | Reproduce with |
|---|---|---|
| 1 | The dev read succeeds. Postgres returns rows to a user that Vault issued, `v-approle-logs-rea-…`. Each call gets a different user with a 300-second lease. One trace covers the gateway, the MCP server, Vault, and Postgres. | `task test -- tests/test_1_allowed.py` |
| 2 | The gateway denies the prod restart. The client gets a tool result with `isError: true` and the text `Denied by gateway policy: restart_service is not allowed with env=prod`. The gateway log has the caller, the arguments, and `mcpGuardrails rejected`. The trace has no MCP server span, and `prod.services` is unchanged. | `task test -- tests/test_2_prod_restart.py` |
| 3 | The prod read passes the gateway, which logs it with no error. Vault denies the credential: `Vault denied a credential for role 'logs-reader-prod' (HTTP 403)`. The trace has a `vault.database.creds` span with status 403 and no Postgres span. | `task test -- tests/test_3_vault_denial.py`, `task vault:check` |
| 4 | The injected log line reaches the client unchanged. The replayed prod restart gets the same denial as test 2. | `task test -- tests/test_4_poisoned_log.py` |
| 5 | From the agent container, `mcp-server` does not resolve and its backend address times out. A request from the backend network without the key gets HTTP 401. The MCP server records a `reject missing upstream key` span, and the gateway log has no entry. | `task test -- tests/test_5_bypass.py` |
| 6 | With the gateway stopped, the in-flight session fails with a connection error in under 10 ms, and nothing reaches the backend. With the gateway frozen by `docker pause`, the client gets no answer and waits for its own timeout, which is 8 s in the test. | `task chaos` |
| 7 | Vault issues each agent a JSON Web Token (JWT) with a 300-second life, the audience `mcp-gateway`, and the agent's entity ID as `sub`. The gateway returns HTTP 401 for no token, for a token whose payload was edited to `role=operator` (`InvalidSignature`), and for a valid token issued for another audience (`InvalidAudience`). `report-agent` sees only `read_logs`, and its `restart_service(env=dev)` call gets HTTP 400 with `authorization denied for tool`. The same call from `oncall-agent` runs. The agent's Vault token gets HTTP 403 on every database role. | `task test -- tests/test_7_identity.py`, `task identity` |

## Latency

`task bench` makes 50 `read_logs(env=dev)` calls straight to the MCP server and 50 through the gateway, after five warm-up calls on each path. Both paths start inside the `mcp-server` container, so the gateway hop is the only difference:

| Run | Direct p50 / p95 | Through gateway p50 / p95 |
|---|---|---|
| 1 | 18.7 ms / 22.4 ms | 19.7 ms / 27.9 ms |
| 2 | 21.4 ms / 33.4 ms | 21.3 ms / 24.0 ms |

The gateway adds a millisecond or less at the median, which is inside the variation between runs. The Vault lease and a new Postgres connection take most of the time in each call.

## Injection rate

`task demo:poisoned` gives the agent a task that does not mention a restart. The instruction to restart prod is only in the log data that `read_logs` returns:

| Model | Runs | Runs with a prod restart attempt | After the denial |
|---|---|---|---|
| `qwen2.5:3b` | 20 | 1 | Stopped calling tools and wrote its answer |
| `qwen2:7b` | 20 | 1 | Stopped calling tools and wrote its answer |

The gateway denied both attempts, and `prod.services` never changed. Two earlier batches of five runs on `qwen2.5:3b` gave at least one attempt in one batch and none in the other.

The same model, prompt, and data follow the injected line in some runs and not in others. Because of this, test 4 replays the call itself and does not depend on the model to make it.

Set `MODEL` and `N` to measure another model or run count.

```bash
MODEL=<name> N=20 task demo:poisoned
```

## Vault boundary

`task vault:check` requests a credential for each of the four roles with the MCP server's AppRole token, and then requests `logs-reader-prod` with the root token. Vault denies the AppRole both prod roles:

```
mcp-server AppRole  logs-reader-dev   -> credential issued
mcp-server AppRole  operator-dev      -> credential issued
mcp-server AppRole  logs-reader-prod  -> Code: 403 permission denied
mcp-server AppRole  operator-prod     -> Code: 403 permission denied
root                logs-reader-prod  -> credential issued
```

## Findings from the build

The build contradicted the draft in five places.

### Tool rules and argument rules

The prod rule could not go in `mcpAuthorization`, because that setting cannot read tool arguments. The rule is an `mcpGuardrails` request processor. Refer to [Gateway rule types](architecture.md#gateway-rule-types).

### Shared telemetry network

Test 5 failed on the first Compose file. The agent and the MCP server shared a network for tracing, so the agent could reach the server without the gateway. Refer to [Networks](architecture.md#networks).

### Client timeout with a frozen gateway

A client timeout does not mean that the call did not run. With the gateway frozen, the client stopped waiting after 8 s. When the gateway resumed, it processed the abandoned request and the restart ran, on every run.

### Guardrail denial format

The gateway returns a guardrail denial as HTTP 200 with `isError: true`, not as a protocol error. The model receives the rejection text as tool output and can react to it.

### Trace ID in the gateway log

The gateway log carries the trace ID of each call, so a denied call in Jaeger and its audit line match by ID. Refer to [Traces and audit](architecture.md#traces-and-audit).

## Findings from the identity test

Test 7 and one manual check produced four findings.

### Authorization denial format

A denial by `mcpAuthorization` does not look like a guardrail denial to the client. The gateway returns HTTP 400, and the MCP Python SDK raises it as a transport error that ends the session. The model gets no rejection text to read.

### Expired tokens

The gateway allows 60 seconds of clock skew, so a five-minute token works for six minutes. The gateway accepted a token with a 5-second life 57 seconds after it expired, and refused it at 65 seconds with `ExpiredSignature`.

### Gateway start order

The gateway reads Vault's public keys when it starts, and it does not start without them. The gateway must start after Vault has the signing key, so `task vault:configure` restarts it.

### Identity scope

The identity stops at the gateway. The audit log names the agent on every call, but the MCP server and Vault see one workload. The Vault boundary in test 3 is the same for every agent.

## Answers to the draft placeholders

Each placeholder in the draft has an answer in this repository, or a statement that the lab does not cover it:

| Placeholder in the draft | Answer |
|---|---|
| Latency added by the gateway check | The table in [Latency](#latency) |
| Exact error the agent receives, and the audit entry | The test 2 row and `results/test_2_prod_restart.json` |
| How the model reacts to the denial | In the two runs with an attempt, the model stopped calling tools. Refer to [Injection rate](#injection-rate). |
| Trace screenshot with the Vault span | The trace ID is in `results/test_3_vault_denial.json`. Open it in Jaeger. |
| Model attempt rate and scanner flag rate | The attempt rate is in [Injection rate](#injection-rate). This lab has no scanner. |
| Confirm the 401 and the empty audit log | The test 5 row |
| Time to failure and error text | The test 6 row and [Client timeout with a frozen gateway](#client-timeout-with-a-frozen-gateway) |
| Policy as code | Yes. The gateway policy is `gateway/config.yaml`. |
| Shared ID between audit log and trace | Yes, the trace ID. Refer to [Trace ID in the gateway log](#trace-id-in-the-gateway-log). |
| Rotation of the upstream key | Not built. The gateway can read the key from a file, so a file that Vault renders is the approach to try. |

## Draft passages that the gateway change makes wrong

The draft describes Runlayer, and the lab runs agentgateway. The following passages no longer match the lab:

- The "Runlayer" section describes a different product and needs a rewrite around agentgateway.
- "Policy now lives in two places with two lifecycles" names a web UI as one of the places. Both policies are files in this repository, and they can still drift apart.
- Test 4 reports "how often ToolGuard flagged it". This lab has no scanner.
- Test 5 says "the header is the boundary, not the network". This lab enforces both, and the network control was the one that failed first. Refer to [Shared telemetry network](#shared-telemetry-network).
- Test 6 "blocks the proxy's route to the Runlayer API". In this lab, the test stops or freezes the gateway itself.
- "I ran the agent as myself after `runlayer login`" does not apply. The agent presents a five-minute token that Vault issues to its own identity, and no person signs in.
