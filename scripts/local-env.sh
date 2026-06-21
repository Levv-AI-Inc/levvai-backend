#!/bin/sh

# Source this file before local Django commands. Shell environment variables
# override the repository's existing .env without modifying it.
export DATABASE_URL="${LOCAL_DATABASE_URL:-postgresql://levvai:levvai@127.0.0.1:5433/levvai_local}"
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-local-development-only-secret-key}"
export DJANGO_DEBUG="${DJANGO_DEBUG:-true}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1}"
export DATABASE_CONN_MAX_AGE="${DATABASE_CONN_MAX_AGE:-0}"
