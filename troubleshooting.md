# Troubleshooting

`task down` followed by `task all` rebuilds the Model Context Protocol (MCP) gateway and Vault lab from an empty state. The following sections cover failures that a rebuild does not fix, or that have a faster fix.

## `docker pull` hangs with no output

On Docker Desktop 24, every pull stalled during the build of this lab, although running containers had network access. Restart Docker Desktop.

If you cannot restart Docker Desktop, copy the image with `skopeo` from inside a container and load the archive.

```bash
docker run --rm -v "$PWD/.lab:/out" postgres:16-alpine sh -c 'apk add -q skopeo && skopeo copy --override-os linux --override-arch arm64 docker://cr.agentgateway.dev/agentgateway:v1.7.0 docker-archive:/out/agw.tar:cr.agentgateway.dev/agentgateway:v1.7.0' && docker load -i .lab/agw.tar
```

## The gateway port is taken

The lab publishes the gateway on `127.0.0.1:13000`, because web development servers commonly use port 3000. To use another port, change the left side of the port mapping in `docker-compose.yml`, and set `GATEWAY_URL` when you run the tests.

## Tests exit with "The lab is not running"

The tests need the gateway on port 13000 and Jaeger on port 16686. Run `task up`, and then run `task vault:configure`.

## Every tool call fails with a Vault error

The MCP server cannot log in to Vault with its AppRole. This has two causes:

- The `.lab/mcp-server/role_id` or `.lab/mcp-server/secret_id` file is missing. Run `task vault:configure`.
- Vault restarted. Dev mode keeps no data, so run `task vault:configure` again. If Terraform reports existing state, delete `terraform/vault/terraform.tfstate*` first.

`task vault:check` requests the credentials from Vault directly, without the gateway or the MCP server in the path.

## A tool call fails with a Postgres authentication error

Each call gets a new database user with a five-minute lease, so this error does not occur in normal use. It occurs when you recreate Postgres and keep Vault, because Vault still holds a connection to the old database. Run `task down`, and then start the lab again.

## The gateway returns HTTP 401

The client did not send a valid `Authorization: Bearer <token>` header. The token is a JSON Web Token (JWT) from Vault that lasts five minutes. `task identity` prints its claims, and the gateway log gives the reason for the refusal, such as `ExpiredSignature` or `InvalidAudience`.

If the gateway refuses every token after Vault restarts or after you reconfigure Vault, the gateway holds old signing keys. Run `task vault:configure`, which restarts the gateway, or run `task gateway:reload`.

## The gateway returns HTTP 406

MCP over streamable HTTP requires `Accept: application/json, text/event-stream` on POST requests, and the client did not send it.

## Client output shows "Session termination failed: 202"

The MCP Python SDK logs this message when the gateway answers the session DELETE request with HTTP 202. The message is harmless.

## A trace has no spans from the MCP server

The MCP server exports spans in batches, and the test helpers wait for them. In the Jaeger UI, reload the trace after a few seconds.

If the spans never arrive, read the collector logs.

```bash
docker compose logs otel-collector
```

## The live agent prints "does not contain any JSON blob"

`qwen2.5:3b` often answers in plain text and does not call the final-answer tool. The smolagents library retries until `max_steps` and then summarizes. The tool calls before that point are real.

A larger model calls the final-answer tool more often. Set `LLM_MODEL` in `.env` to change the model.

## The agent cannot reach Ollama

The agent container calls `http://host.docker.internal:11434`. Run `ollama serve` on the host, and pull the model before you start the agent.
