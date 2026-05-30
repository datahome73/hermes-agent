#!/bin/sh
set -eu

export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
export API_SERVER_HOST="${API_SERVER_HOST:-0.0.0.0}"
export API_SERVER_PORT="${API_SERVER_PORT:-${PORT:-8642}}"
export HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HOME="${HOME:-/opt/data}"

mkdir -p "$HERMES_HOME"

if [ -n "${HERMES_PROVIDER:-}" ] || [ -n "${HERMES_MODEL:-}" ]; then
  python - <<'PY'
from __future__ import annotations

import os
import shutil
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
config_path = home / "config.yaml"
provider = os.environ.get("HERMES_PROVIDER", "openrouter").strip() or "openrouter"
model = os.environ.get("HERMES_MODEL", "deepseek/deepseek-chat").strip() or "deepseek/deepseek-chat"

home.mkdir(parents=True, exist_ok=True)

data = {}
try:
    import yaml

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if isinstance(loaded, dict):
            data = loaded
        else:
            raise TypeError("config root is not a mapping")
    model_cfg = data.get("model")
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    model_cfg["provider"] = provider
    model_cfg["default"] = model
    data["model"] = model_cfg
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
except Exception:
    if config_path.exists():
        backup_path = config_path.with_suffix(".yaml.broken")
        shutil.copy2(config_path, backup_path)
    config_path.write_text(
        "model:\n"
        f"  provider: {provider}\n"
        f"  default: {model}\n",
        encoding="utf-8",
    )
PY
fi

exec /init /opt/hermes/docker/main-wrapper.sh gateway run
