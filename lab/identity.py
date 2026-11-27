"""Agent identity: trade an AppRole for a short-lived JWT that Vault signs.

An agent holds one secret, its own AppRole. It logs in to Vault and asks for an
identity token whose audience is the gateway. The token names the agent (its
Vault entity), carries its role, and expires after five minutes. The gateway
verifies it with Vault's public keys, so no secret is shared between the two.

    python -m lab.identity [agent] [--claims]
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
# Not VAULT_ADDR: a developer's shell often has that set for some other Vault.
VAULT = os.environ.get("LAB_VAULT_ADDR", "http://127.0.0.1:8200")
DEFAULT_AGENT = os.environ.get("AGENT_NAME", "oncall-agent")


def _creds_dir(agent: str) -> Path:
    # In the agent container exactly one identity is mounted; on the host all of them are in .lab/.
    mounted = os.environ.get("AGENT_CREDS_DIR")
    return Path(mounted) if mounted else ROOT / ".lab" / "agents" / agent


def vault_login(agent: str = DEFAULT_AGENT) -> str:
    """AppRole login. Returns the agent's Vault token."""
    creds = _creds_dir(agent)
    r = httpx.post(
        f"{VAULT}/v1/auth/approle/login",
        json={
            "role_id": (creds / "role_id").read_text().strip(),
            "secret_id": (creds / "secret_id").read_text().strip(),
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["auth"]["client_token"]


def agent_jwt(agent: str = DEFAULT_AGENT, audience: str = "mcp-gateway") -> str:
    """A fresh identity token for `agent`, issued for `audience` (a Vault OIDC role)."""
    r = httpx.get(
        f"{VAULT}/v1/identity/oidc/token/{audience}",
        headers={"X-Vault-Token": vault_login(agent)},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["data"]["token"]


def claims(token: str) -> dict:
    """The token's payload, decoded without verification (for display and tests)."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    token = agent_jwt(args[0] if args else DEFAULT_AGENT)
    print(json.dumps(claims(token), indent=2) if "--claims" in sys.argv else token)
