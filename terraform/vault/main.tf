terraform {
  required_version = ">= 1.5"
  required_providers {
    vault = {
      source  = "hashicorp/vault"
      version = "~> 5.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

# Address and token come from VAULT_ADDR and VAULT_TOKEN (the Taskfile sets both).
provider "vault" {}

variable "postgres_root_password" {
  type      = string
  sensitive = true
}

variable "approle_dir" {
  description = "Where the MCP server's AppRole credentials are written (mounted read-only into its container)."
  type        = string
}

locals {
  ttl = 300 # five minutes

  # One Vault role per (job, environment). Each maps to Postgres grants on one schema only.
  roles = {
    "logs-reader-dev"  = { schema = "dev", grants = ["GRANT SELECT ON dev.logs TO \"{{name}}\";"] }
    "logs-reader-prod" = { schema = "prod", grants = ["GRANT SELECT ON prod.logs TO \"{{name}}\";"] }
    "operator-dev"     = { schema = "dev", grants = ["GRANT SELECT, UPDATE ON dev.services TO \"{{name}}\";"] }
    "operator-prod"    = { schema = "prod", grants = ["GRANT SELECT, UPDATE ON prod.services TO \"{{name}}\";"] }
  }
}

resource "vault_mount" "database" {
  path = "database"
  type = "database"
}

resource "vault_database_secret_backend_connection" "postgres" {
  backend       = vault_mount.database.path
  name          = "lab-postgres"
  allowed_roles = keys(local.roles)

  postgresql {
    connection_url = "postgresql://{{username}}:{{password}}@postgres:5432/lab?sslmode=disable"
    username       = "postgres"
    password       = var.postgres_root_password
  }
}

resource "vault_database_secret_backend_role" "role" {
  for_each = local.roles

  backend     = vault_mount.database.path
  name        = each.key
  db_name     = vault_database_secret_backend_connection.postgres.name
  default_ttl = local.ttl
  max_ttl     = local.ttl

  creation_statements = concat(
    [
      "CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}';",
      "GRANT USAGE ON SCHEMA ${each.value.schema} TO \"{{name}}\";",
    ],
    each.value.grants,
  )
}

# The MCP server may lease the two dev roles and nothing else. The prod roles
# exist, so a prod request fails on this policy (403) and not on a missing role.
resource "vault_policy" "mcp_server" {
  name   = "mcp-server"
  policy = <<-EOT
    path "database/creds/logs-reader-dev" {
      capabilities = ["read"]
    }
    path "database/creds/operator-dev" {
      capabilities = ["read"]
    }
  EOT
}

resource "vault_auth_backend" "approle" {
  type = "approle"
}

resource "vault_approle_auth_backend_role" "mcp_server" {
  backend        = vault_auth_backend.approle.path
  role_name      = "mcp-server"
  token_policies = [vault_policy.mcp_server.name]
  token_ttl      = 600
  token_max_ttl  = 1800
}

resource "vault_approle_auth_backend_role_secret_id" "mcp_server" {
  backend   = vault_auth_backend.approle.path
  role_name = vault_approle_auth_backend_role.mcp_server.role_name
}

resource "local_sensitive_file" "role_id" {
  filename        = "${var.approle_dir}/role_id"
  content         = vault_approle_auth_backend_role.mcp_server.role_id
  file_permission = "0644"
}

resource "local_sensitive_file" "secret_id" {
  filename        = "${var.approle_dir}/secret_id"
  content         = vault_approle_auth_backend_role_secret_id.mcp_server.secret_id
  file_permission = "0644"
}
