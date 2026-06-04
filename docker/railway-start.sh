#!/bin/sh
set -eu

export API_SERVER_ENABLED="${API_SERVER_ENABLED:-false}"
export API_SERVER_HOST="${API_SERVER_HOST:-0.0.0.0}"
export API_SERVER_PORT="${API_SERVER_PORT:-${PORT:-8642}}"
export HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HOME="${HOME:-/opt/data}"
export HERMES_PRESERVE_DEPLOY_ENV="${HERMES_PRESERVE_DEPLOY_ENV:-1}"

mkdir -p "$HERMES_HOME"

exec hermes gateway run
