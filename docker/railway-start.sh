#!/bin/sh
set -eu

export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
export API_SERVER_HOST="${API_SERVER_HOST:-0.0.0.0}"
export API_SERVER_PORT="${API_SERVER_PORT:-${PORT:-8642}}"
export HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HOME="${HOME:-/opt/data}"
export HERMES_PRESERVE_DEPLOY_ENV="${HERMES_PRESERVE_DEPLOY_ENV:-1}"

mkdir -p "$HERMES_HOME"

if [ -n "${HERMES_API_KEY:-}" ]; then
  export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-$HERMES_API_KEY}"
  export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$HERMES_API_KEY}"
  export OPENAI_API_KEY="${OPENAI_API_KEY:-$HERMES_API_KEY}"
  export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-$HERMES_API_KEY}"
fi

python - <<'PY'
from __future__ import annotations

import os
import shutil
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
config_path = home / "config.yaml"
template_path = Path("/opt/hermes/cli-config.yaml.example")
provider = os.environ.get("HERMES_PROVIDER", "").strip()
model = os.environ.get("HERMES_MODEL", "").strip()

home.mkdir(parents=True, exist_ok=True)

data = {}
config_needs_write = False
try:
    import yaml

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if isinstance(loaded, dict):
            data = loaded
        else:
            raise TypeError("config root is not a mapping")
except Exception:
    if config_path.exists():
        backup_path = config_path.with_suffix(".yaml.broken")
        shutil.copy2(config_path, backup_path)
    if template_path.exists():
        shutil.copy2(template_path, config_path)
        with config_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        data = loaded if isinstance(loaded, dict) else {}
    else:
        data = {}
    config_needs_write = True

if provider or model:
    model_cfg = data.get("model")
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    if provider:
        model_cfg["provider"] = provider
    if model:
        model_cfg["default"] = model
    data["model"] = model_cfg
    config_needs_write = True

if config_needs_write:
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
PY

exec hermes gateway run
