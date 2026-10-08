#!/bin/sh
#
# Deploy: fetch latest code from Gitea -> rebuild -> restart.
#
# Auth strategy: read-only Gitea PAT, passed via env or interactive prompt.
# The token lives only in process memory; it's `unset` before docker build runs
# and never written to disk or git config.
#
# Usage:
#   ./deploy.sh                           # prompts for token (token shows as ****)
#   bash deploy.sh
#   GIT_TOKEN=glpat_xxx ./deploy.sh       # non-interactive (leaks to shell history!)
#   pass tender/gitea | { read T; GIT_TOKEN="$T" ./deploy.sh; }

# dash/sh lacks pipefail and `read -rsp`; re-exec with bash when needed.
if [ -z "${BASH_VERSION:-}" ]; then
  exec /usr/bin/env bash "$0" "$@"
fi

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env.production ]; then
  echo "Missing .env.production — copy from .env.production.example and fill in values." >&2
  exit 1
fi

if [ ! -f firebase-service-account.json ]; then
  echo "Missing firebase-service-account.json — download from Firebase console → Service accounts." >&2
  exit 1
fi

if [ -z "${GIT_TOKEN:-}" ]; then
  read -rsp "Gitea access token: " GIT_TOKEN
  echo
fi
if [ -z "${GIT_TOKEN}" ]; then
  echo "No token provided. Aborting." >&2
  exit 1
fi

# One-shot auth header for this fetch only; bypass any cached credential helper.
git -c http.extraHeader="Authorization: token ${GIT_TOKEN}" \
    -c credential.helper= \
    fetch origin

unset GIT_TOKEN

git reset --hard origin/V2

# --env-file is required so docker compose can interpolate ${APP_HOST},
# ${TRAEFIK_CERT_RESOLVER}, ${POSTGRES_USER}, etc. inside docker-compose.prod.yml.
# The `env_file:` directive inside services only sets vars INSIDE containers —
# it does NOT make them available to YAML substitution.
docker compose \
  --env-file .env.production \
  -f docker-compose.prod.yml \
  up -d --build

docker compose \
  --env-file .env.production \
  -f docker-compose.prod.yml \
  ps
