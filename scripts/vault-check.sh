#!/bin/sh
# Show the Vault boundary without any MCP in the way: the MCP server's AppRole
# can lease dev credentials and is refused prod ones; root can lease both.
set -eu
cd "$(dirname "$0")/.."
export VAULT_ADDR=http://127.0.0.1:8200

APPROLE_TOKEN=$(vault write -field=token auth/approle/login \
  role_id=@.lab/mcp-server/role_id secret_id=@.lab/mcp-server/secret_id)

try() { # label, token, role
  if VAULT_TOKEN=$2 vault read -field=username "database/creds/$3" >/dev/null 2>/tmp/vault-check.err; then
    echo "$1  $3  -> credential issued"
  else
    echo "$1  $3  -> $(grep -o 'Code: [0-9]*' /tmp/vault-check.err | head -1) permission denied"
  fi
  rm -f /tmp/vault-check.err
}

try "mcp-server AppRole" "$APPROLE_TOKEN" logs-reader-dev
try "mcp-server AppRole" "$APPROLE_TOKEN" operator-dev
try "mcp-server AppRole" "$APPROLE_TOKEN" logs-reader-prod
try "mcp-server AppRole" "$APPROLE_TOKEN" operator-prod
try "root              " "$VAULT_DEV_ROOT_TOKEN" logs-reader-prod
