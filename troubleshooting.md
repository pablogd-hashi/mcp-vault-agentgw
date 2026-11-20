# Troubleshooting

## `docker pull` hangs with no output

Seen on Docker Desktop 24 while building this lab: every pull stalled, although containers themselves had network access. Restarting Docker Desktop is the normal fix. Without restarting, images can be fetched from inside a container and loaded:

```bash
docker run --rm -v "$PWD/.lab:/out" postgres:16-alpine sh -c 'apk add -q skopeo && skopeo copy --override-os linux --override-arch arm64 docker://cr.agentgateway.dev/agentgateway:v1.7.0 docker-archive:/out/agw.tar:cr.agentgateway.dev/agentgateway:v1.7.0' && docker load -i .lab/agw.tar
```

## The gateway port is taken

The gateway is published on `127.0.0.1:13000` because 3000 is commonly used by web dev servers. Change the left side of the port mapping in `docker-compose.yml` and set `GATEWAY_URL` when running tests.

## Tests exit with "The lab is not running"

Run `task up` and then `task vault:configure`. The tests need the gateway on 13000 and Jaeger on 16686.

## Every tool call fails with a Vault error

- `.lab/role_id` or `.lab/secret_id` is missing: run `task vault:configure`.
- Vault was restarted. Dev mode keeps nothing, so run `task vault:configure` again. If Terraform complains about existing state, delete `terraform/vault/terraform.tfstate*` first.
- `task vault:check` shows what the AppRole can lease without MCP in the way.

## A tool call fails with a Postgres authentication error

Leased users live for five minutes and each call gets a new one, so this should not happen in normal use. If Postgres was recreated but Vault was not, the connection Vault holds is stale: run `task down`, then start again.

## 401 from the gateway

The client did not send `Authorization: Bearer $AGENT_KEY`. The key is in `.env`.

## 406 from the gateway

MCP over streamable HTTP requires `Accept: application/json, text/event-stream` on POST requests.

## "Session termination failed: 202" in client output

The MCP Python SDK logs this when the gateway answers the session DELETE with 202. It is harmless.

## No spans from the MCP server in a trace

Spans are exported in batches. The test helpers wait for them; in the Jaeger UI, reload after a couple of seconds. If they never arrive, check `docker compose logs otel-collector`.

## The live agent prints "does not contain any JSON blob"

`qwen2.5:3b` often answers in plain text instead of calling the final-answer tool, so smolagents retries until `max_steps` and then summarises. The tool calls before that point are real. A larger model behaves better; set `LLM_MODEL` in `.env`.

## The agent cannot reach Ollama

The agent container uses `http://host.docker.internal:11434`. Ollama must be running on the host (`ollama serve`) with the model pulled.
