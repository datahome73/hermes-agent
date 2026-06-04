#!/bin/sh
set -eu

export API_SERVER_ENABLED="${API_SERVER_ENABLED:-false}"
export API_SERVER_HOST="${API_SERVER_HOST:-0.0.0.0}"
export API_SERVER_PORT="${API_SERVER_PORT:-${PORT:-8642}}"
export HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HOME="${HOME:-/opt/data}"
export HERMES_PRESERVE_DEPLOY_ENV="${HERMES_PRESERVE_DEPLOY_ENV:-1}"

mkdir -p "$HERMES_HOME"

# ── 初始化 config.yaml ──────────────────────────────────────────
# 从环境变量写入主模型和备用模型配置
# 支持的变量:
#   HERMES_PROVIDER   — provider 名称 (如 deepseek)
#   HERMES_MODEL      — 模型名称 (如 deepseek-chat)
#   HERMES_BASE_URL   — 自定义 base URL (可选)
#   HERMES_FALLBACK_PROVIDER  — 备用 provider (可选)
#   HERMES_FALLBACK_MODEL     — 备用模型 (可选)
#   HERMES_FALLBACK_BASE_URL  — 备用 base URL (可选)
#   HERMES_API_KEY    — 统一 API key (可选, 自动设置到对应 provider 的环境变量)

python3 - <<'PY'
import os
import yaml
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
config_path = home / "config.yaml"

home.mkdir(parents=True, exist_ok=True)

# 读取现有 config
data = {}
if config_path.exists():
    try:
        with open(config_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        pass

needs_write = False

# ── 主模型配置 ──
provider = os.environ.get("HERMES_PROVIDER", "").strip()
model = os.environ.get("HERMES_MODEL", "").strip()
base_url = os.environ.get("HERMES_BASE_URL", "").strip()

if provider or model:
    model_cfg = data.get("model")
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    if provider:
        model_cfg["provider"] = provider
    if model:
        model_cfg["default"] = model
    if base_url:
        model_cfg["base_url"] = base_url
    elif provider and not base_url:
        # 清理旧 base_url 当只设 provider 时
        model_cfg.pop("base_url", None)
        model_cfg.pop("api_mode", None)
    data["model"] = model_cfg
    needs_write = True

# ── 备用模型配置 ──
fb_provider = os.environ.get("HERMES_FALLBACK_PROVIDER", "").strip()
fb_model = os.environ.get("HERMES_FALLBACK_MODEL", "").strip()
fb_base_url = os.environ.get("HERMES_FALLBACK_BASE_URL", "").strip()

if fb_provider and fb_model:
    entry = {
        "provider": fb_provider,
        "model": fb_model,
    }
    if fb_base_url:
        entry["base_url"] = fb_base_url

    # 读取现有 fallback chain, 替换或追加
    existing = data.get("fallback_providers")
    if isinstance(existing, list):
        # 替换同 provider 的条目, 否则追加
        replaced = False
        for i, e in enumerate(existing):
            if isinstance(e, dict) and e.get("provider") == fb_provider:
                existing[i] = entry
                replaced = True
                break
        if not replaced:
            existing.append(entry)
    else:
        data["fallback_providers"] = [entry]
    needs_write = True
elif fb_provider or fb_model:
    # 只设了一个, 忽略
    pass

# ── API Key 自动映射 ──
# 主 provider API key
api_key = os.environ.get("HERMES_API_KEY", "").strip()
if api_key:
    p = provider.lower() if provider else ""
    if p in ("deepseek",) or not p:
        os.environ["DEEPSEEK_API_KEY"] = api_key

# 备用 provider API key (从 HERMES_FALLBACK_PROVIDER 推断)
fb_p = fb_provider.lower() if fb_provider else ""
if fb_p == "gemini" and not os.environ.get("GEMINI_API_KEY"):
    # 如果备用是 gemini 且没有单独设 GEMINI_API_KEY, 尝试用 HERMES_API_KEY
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key

if needs_write:
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    print(f"✅ config.yaml written to {config_path}")
PY

exec hermes gateway run
