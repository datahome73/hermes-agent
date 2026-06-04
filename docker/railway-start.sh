#!/bin/sh
set -eu

export API_SERVER_ENABLED="${API_SERVER_ENABLED:-false}"
export API_SERVER_HOST="${API_SERVER_HOST:-0.0.0.0}"
export API_SERVER_PORT="${API_SERVER_PORT:-${PORT:-8642}}"
export HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HOME="${HOME:-/opt/data}"
export HERMES_PRESERVE_DEPLOY_ENV="${HERMES_PRESERVE_DEPLOY_ENV:-1}"

mkdir -p "$HERMES_HOME"

# ── 从环境变量自动生成 config.yaml ─────────────────────────────
# 脚本扫描已知 provider 的环境变量前缀，自动推导主模型和备用模型。
# 无需任何 HERMES_* 变量。只需在 Railway Dashboard 设置:
#
#   必选:  <PROVIDER>_API_KEY   (如 DEEPSEEK_API_KEY, GEMINI_API_KEY)
#   可选:  <PROVIDER>_BASE_URL  (如 DEEPSEEK_BASE_URL)
#   可选:  <PROVIDER>_MODEL     (如 DEEPSEEK_MODEL, 覆盖默认模型名)
#
# 主模型优先级: 设 PRIMARY_PROVIDER= 则用指定的，否则 deepseek > gemini > 其他
# 备用模型: 从剩余可用 provider 中选第一个

# ── 设置文件权限 ──
chmod 0600 "$HERMES_HOME/.env" 2>/dev/null || true
chmod 0600 "$HERMES_HOME/config.yaml" 2>/dev/null || true

python3 - <<'PY'
import os
import re
import yaml
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
config_path = home / "config.yaml"
home.mkdir(parents=True, exist_ok=True)

# ── 简单的脱敏工具 (只用于 print 输出, 不用于日志脱敏) ──
# 匹配已知 token 前缀 (sk-, AIza, ghp_ 等) 和 KEY=value 模式
_TOKEN_PREFIX_PAT = re.compile(
    r"(?<![A-Za-z0-9_-])("
    r"sk-[A-Za-z0-9_-]{10,}|"
    r"AIza[A-Za-z0-9_-]{30,}|"
    r"ghp_[A-Za-z0-9]{10,}|"
    r"github_pat_[A-Za-z0-9_]{10,}|"
    r"gsk_[A-Za-z0-9]{10,}|"
    r"xai-[A-Za-z0-9]{30,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"sk_live_[A-Za-z0-9]{10,}|"
    r"hf_[A-Za-z0-9]{10,}|"
    r"pplx-[A-Za-z0-9]{10,}|"
    r"fal_[A-Za-z0-9_-]{10,}"
    r")(?![A-Za-z0-9_-])"
)
def _safe(text: str) -> str:
    """脱敏后返回, 保留前6后4字符."""
    if not text:
        return text
    return _TOKEN_PREFIX_PAT.sub(lambda m: m.group(1)[:6] + "..." + m.group(1)[-4:] if len(m.group(1)) > 12 else "***", text)

# ── 已知 provider 配置表 ──
PROVIDERS = {
    "deepseek": {
        "key_envs": ["DEEPSEEK_API_KEY"],
        "base_url_envs": ["DEEPSEEK_BASE_URL"],
        "default_base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "model_env": "DEEPSEEK_MODEL",
    },
    "gemini": {
        "key_envs": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "base_url_envs": ["GEMINI_BASE_URL", "GOOGLE_BASE_URL"],
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
        "model_env": "GEMINI_MODEL",
    },
    "openai": {
        "key_envs": ["OPENAI_API_KEY"],
        "base_url_envs": ["OPENAI_BASE_URL"],
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "model_env": "OPENAI_MODEL",
    },
    "openrouter": {
        "key_envs": ["OPENROUTER_API_KEY"],
        "base_url_envs": ["OPENROUTER_BASE_URL"],
        "default_base_url": "https://openrouter.ai/api/v1",
        "default_model": "deepseek-chat",
        "model_env": "OPENROUTER_MODEL",
    },
    "anthropic": {
        "key_envs": ["ANTHROPIC_API_KEY"],
        "base_url_envs": ["ANTHROPIC_BASE_URL"],
        "default_base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-20250514",
        "model_env": "ANTHROPIC_MODEL",
    },
    "custom": {
        "key_envs": ["CUSTOM_API_KEY"],
        "base_url_envs": ["CUSTOM_BASE_URL"],
        "default_base_url": "",
        "default_model": "custom-model",
        "model_env": "CUSTOM_MODEL",
    },
}

# provider 优先级 (用于主模型选择)
PRIORITY = ["deepseek", "gemini", "openai", "openrouter", "anthropic", "custom"]

# ── 扫描环境变量，找出可用 provider ──
available = {}  # name -> {api_key, base_url, model}

for name, cfg in PROVIDERS.items():
    api_key = None
    for env in cfg["key_envs"]:
        v = os.environ.get(env, "").strip()
        if v:
            api_key = v
            break
    if not api_key:
        continue  # 没有 API key，跳过

    base_url = None
    for env in cfg["base_url_envs"]:
        v = os.environ.get(env, "").strip()
        if v:
            base_url = v
            break
    if not base_url:
        base_url = cfg["default_base_url"]

    model = os.environ.get(cfg["model_env"], "").strip()
    if not model:
        model = cfg["default_model"]

    available[name] = {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "key_env": cfg["key_envs"][0],
    }

if not available:
    print("⚠ No known provider API keys found. Starting with defaults.")
else:
    print(f"✅ Detected providers: {', '.join(available.keys())}")
    # 只在调试时显示 key 名 (不显示值)
    for n, p in available.items():
        print(f"   {n}: using ${p['key_env']}, model={p['model']}, base_url={_safe(p['base_url'])}")

# ── 选主模型 ──
primary_provider = os.environ.get("PRIMARY_PROVIDER", "").strip().lower()
if primary_provider and primary_provider in available:
    pass  # 已指定
elif primary_provider:
    print(f"⚠ PRIMARY_PROVIDER={primary_provider} not available, auto-selecting")
    primary_provider = None

if not primary_provider:
    for p in PRIORITY:
        if p in available:
            primary_provider = p
            break

# ── 选备用模型 (从剩余 provider 中选优先级最高的) ──
fallback_provider = None
if primary_provider:
    for p in PRIORITY:
        if p in available and p != primary_provider:
            fallback_provider = p
            break

# ── 读取现有 config ──
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

# ── 写入主模型 ──
if primary_provider:
    p = available[primary_provider]
    data["model"] = {
        "provider": primary_provider,
        "default": p["model"],
        "base_url": p["base_url"],
    }
    print(f"📌 Primary: {primary_provider}/{p['model']} → {_safe(p['base_url'])}")
    needs_write = True

# ── 写入备用模型 ──
if fallback_provider:
    p = available[fallback_provider]
    data["fallback_providers"] = [{
        "provider": fallback_provider,
        "model": p["model"],
        "base_url": p["base_url"],
    }]
    print(f"📌 Fallback: {fallback_provider}/{p['model']} → {_safe(p['base_url'])}")
    needs_write = True
else:
    data.pop("fallback_providers", None)

if needs_write:
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    os.chmod(config_path, 0o600)
    print(f"✅ config.yaml written to {config_path}")
PY

# ── 再次确保 .env 和 config.yaml 权限 (文件可能刚创建) ──
chmod 0600 "$HERMES_HOME/.env" 2>/dev/null || true
chmod 0600 "$HERMES_HOME/config.yaml" 2>/dev/null || true

exec hermes gateway run
