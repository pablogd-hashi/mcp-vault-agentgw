"""Latency of read_logs(env=dev): straight to the MCP server vs. through the gateway.

Runs inside the mcp-server container (`task bench`), so both paths start from
the same place and the only difference is the gateway hop. Every call includes
a Vault lease and a new Postgres connection, which is most of the time.
"""

import asyncio
import json
import os
import statistics
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

N = int(os.environ.get("BENCH_CALLS", "50"))
WARMUP = 5


async def measure(url: str, headers: dict) -> dict:
    async with httpx.AsyncClient(headers=headers, timeout=60) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                samples = []
                for i in range(N + WARMUP):
                    t = time.perf_counter()
                    r = await s.call_tool("read_logs", {"env": "dev"})
                    assert not r.isError, r.content
                    if i >= WARMUP:
                        samples.append((time.perf_counter() - t) * 1000)
    q = statistics.quantiles(samples, n=20)
    return {"calls": N, "p50_ms": round(statistics.median(samples), 1), "p95_ms": round(q[18], 1)}


async def main():
    direct = await measure("http://localhost:8000/mcp", {"x-api-key": os.environ["UPSTREAM_KEY"]})
    gateway = await measure("http://agentgateway:3000/mcp", {"Authorization": f"Bearer {os.environ['AGENT_TOKEN']}"})
    print(
        json.dumps(
            {
                "direct": direct,
                "through_gateway": gateway,
                "added_p50_ms": round(gateway["p50_ms"] - direct["p50_ms"], 1),
                "added_p95_ms": round(gateway["p95_ms"] - direct["p95_ms"], 1),
            },
            indent=2,
        )
    )


asyncio.run(main())
