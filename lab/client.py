"""Small MCP client helpers shared by the tests and the scripts.

Everything goes through the gateway exactly as the agent's calls do: streamable
HTTP, with the agent key as a bearer token.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:13000/mcp")


@dataclass
class ToolOutcome:
    is_error: bool
    text: str
    structured: dict | None


@asynccontextmanager
async def session(url: str = GATEWAY_URL, key: str | None = None, headers: dict | None = None):
    """Open an initialised MCP session. `key` defaults to AGENT_KEY from the environment."""
    hdrs = dict(headers or {})
    key = os.environ.get("AGENT_KEY") if key is None else key
    if key:
        hdrs["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(headers=hdrs, timeout=httpx.Timeout(30, read=120)) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s


async def call(s: ClientSession, tool: str, **arguments) -> ToolOutcome:
    result = await s.call_tool(tool, arguments)
    text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    structured = result.structuredContent
    if structured is None and not result.isError:
        try:
            structured = json.loads(text)
        except ValueError:
            pass
    return ToolOutcome(bool(result.isError), text, structured)
