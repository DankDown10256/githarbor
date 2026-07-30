#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$project_dir/.env"

umask 077
touch "$env_file"
chmod 600 "$env_file"

if ! grep -q '^FLASK_SECRET_KEY=' "$env_file"; then
  secret_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  printf 'FLASK_SECRET_KEY=%s\n' "$secret_key" >> "$env_file"
  echo "FLASK_SECRET_KEY has been generated."
fi

if ! grep -q '^TOKEN_ENCRYPTION_KEY=' "$env_file"; then
  encryption_key="$(python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
  printf 'TOKEN_ENCRYPTION_KEY=%s\n' "$encryption_key" >> "$env_file"
  echo "TOKEN_ENCRYPTION_KEY has been generated."
fi

if ! grep -q '^GITHUB_CLIENT_ID=' "$env_file"; then
  read -r -p "GitHub OAuth Client ID: " github_client_id
  printf 'GITHUB_CLIENT_ID=%s\n' "$github_client_id" >> "$env_file"
fi

if ! grep -q '^GITHUB_CLIENT_SECRET=' "$env_file"; then
  read -r -s -p "GitHub OAuth Client Secret: " github_client_secret
  printf '\nGITHUB_CLIENT_SECRET=%s\n' "$github_client_secret" >> "$env_file"
fi

echo "Configuration saved in .env."
