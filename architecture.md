# Architecture

## What runs where

```
 host
 ├─ Ollama (model)            127.0.0.1:11434
 ├─ pytest, Terraform, vault CLI
 │
 └─ docker compose
     agent-net ─────────────┐
       agent (on demand)    │
                            ├── agentgateway      published 127.0.0.1:13000
     backend-net ───────────┘        │  adds x-api-key
       mcp-server  ◄─────────────────┘
          │  AppRole login, lease database/creds/<role>
          ├──► vault                 published 127.0.0.1:8200 (for Terraform)
          └──► postgres              not published
     telemetry-net
       otel-collector (also on agent-net and backend-net) ──► jaeger   published 127.0.0.1:16686
```

## One tool call, hop by hop

1. The agent sends `tools/call` to the gateway over streamable HTTP with its own key as a bearer token.
2. The gateway checks the key (`apiKey`, strict mode). No key means HTTP 401 and nothing else happens.
3. The gateway runs the request guardrail. `restart_service` with `env == 'prod'` is rejected here, and the agent gets a normal tool result with `isError: true` and the rejection text.
4. The gateway checks the tool against `mcpAuthorization`. Tools that are not listed do not appear in `tools/list` at all.
5. The gateway forwards the call to the MCP server and adds the upstream key as `x-api-key`. It also forwards the W3C trace context.
6. The MCP server rejects any request without that key (HTTP 401).
7. The tool logs in to Vault with AppRole (token cached) and asks for `database/creds/<role>-<env>`. The AppRole policy allows the two dev roles only, so a prod request gets HTTP 403 from Vault and the tool returns an error.
8. With a credential, the tool opens a Postgres connection as the leased user, runs one statement, and returns.

## Who decides what

| Layer | Configured in | Sees | Does not see |
|---|---|---|---|
| Gateway | `gateway/config.yaml` | Caller, tool name, arguments, result | What the tool does with its backend |
| MCP server | `mcp-server/server.py` | Whether the request came through the gateway (the key) | Who the original caller was |
| Vault | `terraform/vault/main.tf` | Which workload asks for which role | The agent, the prompt, the tool call |
| Postgres | Grants in the Vault role | The leased user and its grants | Everything above |

## Why two kinds of gateway rule

`mcpAuthorization` decides which tools exist for a caller and cannot read arguments. Rules on arguments are `mcpGuardrails` processors that run in the request phase, before the gateway contacts the MCP server. The lab uses both: the first lists the two tools, the second denies the prod restart.

## Networks

The agent container is on `agent-net` only. It cannot resolve or route to the MCP server, Vault or Postgres. The collector is the only service on all three networks.

The first version of the compose file put both the agent and the MCP server on a shared telemetry network, which gave the agent a direct path to the server. Test 5 failed on it. Any shared network between the two sides is a way around the gateway, including the ones added for observability.

One caveat on a laptop: the agent reaches Ollama through `host.docker.internal`, so it can also reach other ports published on the host's loopback (Vault on 8200, the gateway on 13000). It has no Vault token, and the MCP server is not published, but a stricter setup would run the model inside `agent-net`.

## Traces and audit

- Agent, gateway and MCP server export spans to the collector, which forwards to Jaeger. The trace context travels agent → gateway → MCP server, so one agent run is one trace.
- MCP server spans carry `lab.decision` (`allow` or `deny`) and `lab.layer` (`none`, `vault`, `header`).
- A gateway denial has no MCP server span, because the call never arrives. The evidence for it is the gateway span and the gateway's request log.
- The request log is JSON on the gateway's stdout. Each tool call has the caller, tool, arguments, any error, and the trace ID, so a log line and a trace can be matched directly.
