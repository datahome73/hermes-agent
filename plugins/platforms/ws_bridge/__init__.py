"""WS Bridge platform plugin registration."""

from .ws_bridge_adapter import (
    WSBridgeAdapter,
    check_requirements,
    validate_config,
    is_connected,
    interactive_setup,
    _apply_yaml_config,
    _env_enablement,
)


def register(ctx):
    ctx.register_platform(
        name="ws_bridge",
        label="WS Bridge",
        adapter_factory=lambda cfg: WSBridgeAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["WS_BRIDGE_URL", "WS_BRIDGE_AGENT_ID"],
        install_hint="pip install websockets",
        setup_fn=interactive_setup,
        allowed_users_env="WS_BRIDGE_ALLOWED_USERS",
        allow_all_env="WS_BRIDGE_ALLOW_ALL_USERS",
        env_enablement_fn=_env_enablement,
        apply_yaml_config_fn=_apply_yaml_config,
        emoji="🌉",
        allow_update_command=False,
        platform_hint=(
            "You are chatting via WS Bridge — a self-hosted broadcast group chat for bots. "
            "STRICT RULES: "
            "1. This is a shared channel — ALL connected bots see your messages. "
            "2. Text-only — NO files, images, voice, or media. "
            "3. Do NOT output internal thinking, reasoning traces, or tool calls. "
            "4. Keep responses concise and conversational. "
            "5. If the message doesn't start with your name, it may not be directed at you."
        ),
    )
