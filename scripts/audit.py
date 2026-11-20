"""Print the gateway's record of tool calls: who, which tool, which arguments, what happened.

Usage: python scripts/audit.py [minutes]   (default: the last 30 minutes)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import evidence  # noqa: E402

minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 30
calls = [e for e in evidence.gateway_log(minutes * 60) if e.get("mcp.method.name") == "tools/call"]
denied = 0
for e in calls:
    verdict = "DENIED " if "error" in e else "allowed"
    denied += "error" in e
    print(
        f'{e["time"][:19]}  {verdict}  {e.get("caller", "?"):10}  '
        f'{e.get("gen_ai.tool.name", "?")}({e.get("tool_args", {})})  trace={e.get("trace.id", "")[:16]}'
    )
print(f"\n{len(calls)} tool calls in the last {minutes} min, {denied} denied by the gateway")
