"""Lab MCP server: two tools, no database password of its own.

Every tool call logs in to Vault with AppRole (token cached), leases a
five-minute Postgres credential for exactly one role, and uses it once.
Requests that do not carry the upstream key are rejected before MCP sees them.
"""

from __future__ import annotations

import hmac
import os
import time
from pathlib import Path

import hvac
import psycopg
import uvicorn
from hvac.exceptions import Forbidden, VaultError
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.responses import JSONResponse

ENVS = ("dev", "prod")
UPSTREAM_KEY = os.environ["UPSTREAM_KEY"]
VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://vault:8200")
APPROLE_DIR = Path(os.environ.get("APPROLE_DIR", "/run/lab"))
PG_HOST = os.environ.get("PG_HOST", "postgres")
PG_DB = os.environ.get("PG_DB", "lab")

provider = TracerProvider(resource=Resource.create({"service.name": "mcp-server"}))
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
            insecure=True,
        ),
        schedule_delay_millis=500,
    )
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("lab.mcp-server")

mcp = FastMCP("lab-ops", host="0.0.0.0", port=8000, stateless_http=True)

_vault_token: tuple[str, float] | None = None


def _vault() -> hvac.Client:
    """Return a Vault client holding a fresh-enough AppRole token."""
    global _vault_token
    client = hvac.Client(url=VAULT_ADDR)
    if _vault_token and _vault_token[1] > time.time() + 30:
        client.token = _vault_token[0]
        return client
    with tracer.start_as_current_span("vault.approle.login", kind=SpanKind.CLIENT):
        auth = client.auth.approle.login(
            role_id=(APPROLE_DIR / "role_id").read_text().strip(),
            secret_id=(APPROLE_DIR / "secret_id").read_text().strip(),
        )["auth"]
    _vault_token = (auth["client_token"], time.time() + auth["lease_duration"])
    client.token = _vault_token[0]
    return client


def _lease(role: str) -> dict:
    """Ask Vault for a short-lived database credential for one role."""
    with tracer.start_as_current_span("vault.database.creds", kind=SpanKind.CLIENT) as span:
        span.set_attribute("vault.role", role)
        try:
            resp = _vault().secrets.database.generate_credentials(name=role)
        except Forbidden as exc:
            span.set_attribute("http.response.status_code", 403)
            span.set_attribute("lab.decision", "deny")
            span.set_attribute("lab.layer", "vault")
            span.set_status(Status(StatusCode.ERROR, "permission denied"))
            raise PermissionError(f"Vault denied a credential for role '{role}' (HTTP 403)") from exc
        span.set_attribute("http.response.status_code", 200)
        span.set_attribute("vault.lease_duration_s", resp["lease_duration"])
        span.set_attribute("db.user", resp["data"]["username"])
        return resp["data"]


def _run(tool: str, env: str, role_prefix: str, ctx: Context, work):
    """Shared wrapper: one span per tool call, recording who decided what."""
    headers = dict(ctx.request_context.request.headers) if ctx.request_context.request else {}
    parent = propagate.extract(headers)
    with tracer.start_as_current_span(f"tool {tool}", context=parent, kind=SpanKind.SERVER) as span:
        span.set_attribute("mcp.tool.name", tool)
        span.set_attribute("lab.env", env)
        if env not in ENVS:
            span.set_attribute("lab.decision", "deny")
            span.set_attribute("lab.layer", "mcp-server")
            raise ToolError(f"unknown env '{env}', expected one of {ENVS}")
        try:
            creds = _lease(f"{role_prefix}-{env}")
        except PermissionError as exc:
            span.set_attribute("lab.decision", "deny")
            span.set_attribute("lab.layer", "vault")
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise ToolError(str(exc)) from exc
        except VaultError as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise ToolError(f"Vault error: {exc}") from exc
        with tracer.start_as_current_span("postgres.query", kind=SpanKind.CLIENT) as db_span:
            db_span.set_attribute("db.user", creds["username"])
            with psycopg.connect(
                host=PG_HOST, dbname=PG_DB, user=creds["username"], password=creds["password"]
            ) as conn:
                result = work(conn)
        span.set_attribute("lab.decision", "allow")
        span.set_attribute("lab.layer", "none")
        span.set_attribute("db.user", creds["username"])
        return result


@mcp.tool()
def read_logs(env: str, ctx: Context) -> dict:
    """Return the most recent application log lines for an environment ("dev" or "prod")."""

    def work(conn):
        # env is checked against ENVS before this runs, so it is safe as an identifier.
        rows = conn.execute(
            f"SELECT ts, service, level, message FROM {env}.logs ORDER BY id DESC LIMIT 20"
        ).fetchall()
        return {
            "env": env,
            "db_user": conn.info.user,
            "lines": [f"{ts:%H:%M:%S} {svc} {lvl} {msg}" for ts, svc, lvl, msg in rows],
        }

    return _run("read_logs", env, "logs-reader", ctx, work)


@mcp.tool()
def restart_service(env: str, name: str, ctx: Context) -> dict:
    """Restart a service by name in an environment ("dev" or "prod")."""

    def work(conn):
        row = conn.execute(
            f"UPDATE {env}.services SET restarts = restarts + 1, last_restart = now() "
            "WHERE name = %s RETURNING name, restarts",
            (name,),
        ).fetchone()
        if row is None:
            raise ToolError(f"no service named '{name}' in {env}")
        return {"env": env, "service": row[0], "restarts": row[1], "db_user": conn.info.user}

    return _run("restart_service", env, "operator", ctx, work)


class RequireUpstreamKey:
    """Reject any HTTP request that does not carry the upstream key.

    The gateway adds this header; the agent never holds it. Rejections get a
    span of their own so a bypass attempt is visible in the trace backend.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if hmac.compare_digest(headers.get("x-api-key", ""), UPSTREAM_KEY):
            return await self.app(scope, receive, send)
        with tracer.start_as_current_span("reject missing upstream key", kind=SpanKind.SERVER) as span:
            span.set_attribute("lab.decision", "deny")
            span.set_attribute("lab.layer", "header")
            span.set_attribute("http.response.status_code", 401)
            span.set_attribute("client.address", (scope.get("client") or ["?"])[0])
        response = JSONResponse({"error": "missing or invalid upstream key"}, status_code=401)
        await response(scope, receive, send)


app = RequireUpstreamKey(mcp.streamable_http_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
