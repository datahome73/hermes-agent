"""WS Bridge Gateway plugin — broadcast group chat for bots."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.platforms.base import BasePlatformAdapter, SendResult

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def _normalize_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if raw.startswith("http://"):
        raw = raw.replace("http://", "ws://", 1)
    elif raw.startswith("https://"):
        raw = raw.replace("https://", "wss://", 1)
    elif not raw.startswith("ws://") and not raw.startswith("wss://"):
        raw = f"wss://{raw}"
    return raw


def check_requirements() -> bool:
    try:
        import websockets  # noqa: F401
        return True
    except ImportError:
        return False


def validate_config(config: PlatformConfig) -> bool:
    extra = config.extra or {}
    agent_id = extra.get("agent_id") or _env("WS_BRIDGE_AGENT_ID")
    url = extra.get("url") or _env("WS_BRIDGE_URL")
    if not agent_id:
        logger.warning("[WSBridge] WS_BRIDGE_AGENT_ID not configured")
        return False
    if not url:
        logger.warning("[WSBridge] WS_BRIDGE_URL not configured")
        return False
    return True


def is_connected(config: PlatformConfig) -> bool:
    extra = config.extra or {}
    return bool(extra.get("agent_id") or _env("WS_BRIDGE_AGENT_ID"))


def interactive_setup() -> None:
    print("\n--- WS Bridge Setup ---")
    url = input("WS Bridge URL (e.g. wss://example.com): ").strip()
    if url:
        print(f"Set env var: export WS_BRIDGE_URL={url}")
    agent_id = input("Your Agent ID: ").strip()
    if agent_id:
        print(f"Set env var: export WS_BRIDGE_AGENT_ID={agent_id}")
    print("Done.\n")


def _apply_yaml_config(yaml_cfg: dict, platform_cfg: dict) -> Optional[dict]:
    extra = platform_cfg.get("extra", {})
    if not isinstance(extra, dict):
        extra = {}
    seeded = {}
    # Accept both 'url' and 'ws_url' as the server URL
    url_val = extra.get("url") or extra.get("ws_url") or _env("WS_BRIDGE_URL")
    if url_val:
        seeded["url"] = url_val
    for key in ("agent_id", "app_id", "bot_name", "role"):
        val = extra.get(key) or _env(f"WS_BRIDGE_{key.upper()}")
        if val:
            seeded[key] = val
    # mention config
    mention_mode = extra.get("mention_mode")
    if mention_mode is not None:
        seeded["mention_mode"] = bool(mention_mode)
    mention_keyword = extra.get("mention_keyword") or "小爱"
    seeded["mention_keyword"] = mention_keyword
    return seeded if seeded else None


def _env_enablement() -> Optional[dict]:
    extra = {}
    agent_id = _env("WS_BRIDGE_AGENT_ID")
    if agent_id:
        extra["agent_id"] = agent_id
    url = _env("WS_BRIDGE_URL")
    if url:
        extra["url"] = url
    return extra if extra else None


# ── Adapter ────────────────────────────────────────────────────────────


class WSBridgeAdapter(BasePlatformAdapter):
    """WS Bridge client adapter — connects to a self-hosted WS broadcast hub."""

    supports_code_blocks = True
    name = "ws_bridge"

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("ws_bridge"))
        extra = config.extra or {}

        self._url = _normalize_url(
            extra.get("url") or extra.get("ws_url") or _env("WS_BRIDGE_URL") or ""
        )
        self._agent_id = extra.get("agent_id") or _env("WS_BRIDGE_AGENT_ID") or ""
        self._app_id = extra.get("app_id") or _env("WS_BRIDGE_APP_ID") or ""
        self._bot_name = extra.get("bot_name") or _env("WS_BRIDGE_BOT_NAME") or "Hermes"
        self._role = extra.get("role") or "member"
        self._mention_mode = bool(extra.get("mention_mode", False))
        self._mention_keyword = extra.get("mention_keyword", "小爱")
        self._auth_ok = False

        # WS state
        self._ws: Optional[Any] = None
        self._ws_lock = __import__("asyncio").Lock()
        self._stop_event = __import__("asyncio").Event()
        self._reconnect_delay = 3.0

        logger.warning(
            "[WSBridge] Initialized (agent=%s url=%s role=%s mention=%s)",
            self._agent_id[:20], self._url, self._role, self._mention_mode,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to WS Bridge and authenticate with exponential backoff retry."""
        if not self._agent_id or not self._url:
            logger.error("[WSBridge] Missing agent_id or url")
            return False

        import asyncio
        import json as _json

        self._stop_event.clear()
        self._auth_ok = False
        self._should_reconnect = True
        backoff = self._reconnect_delay

        try:
            import websockets
        except ImportError:
            logger.error("[WSBridge] websockets not installed")
            return False

        while self._should_reconnect:
            try:
                self._ws = await websockets.connect(
                    self._url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                )
            except asyncio.CancelledError:
                self._should_reconnect = False
                break
            except Exception as e:
                logger.error(
                    "[WSBridge] Connection failed: %s — retry in %ds", e, backoff
                )
                if self._should_reconnect:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 300)  # max 5 min
                continue

            # Connected — reset backoff
            self._reconnect_delay = 3.0
            backoff = 3.0
            logger.warning("[WSBridge] CONNECTED — sending auth...")

            # Auth
            auth_msg = _json.dumps({
                "type": "auth",
                "app_id": self._app_id,
                "agent_id": self._agent_id,
                "name": self._bot_name,
            })
            try:
                await self._ws.send(auth_msg)
            except Exception as e:
                logger.error("[WSBridge] Auth send failed: %s", e)
                await self._ws.close()
                continue

            # Wait for auth response
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
                resp = _json.loads(raw)
            except Exception as e:
                logger.error("[WSBridge] No auth response: %s", e)
                await self._ws.close()
                continue

            if resp.get("type") == "auth_ok":
                self._auth_ok = True
                logger.warning(
                    "[WSBridge] Auth OK — role=%s agent_id=%s",
                    resp.get("role"), resp.get("agent_id", "")[:20],
                )
                # Start reader loop
                asyncio.create_task(self._reader_loop())
                return True
            elif resp.get("type") == "pairing_code":
                code = resp.get("code", "?")
                logger.warning(
                    "[WSBridge] PAIRING CODE — %s (send to admin for approval)", code
                )
                await self._ws.close()
                # Wait and retry
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue
            else:
                logger.error("[WSBridge] Unexpected auth response: %s", str(resp)[:200])
                await self._ws.close()
                continue

        return False

    async def disconnect(self) -> None:
        """Disconnect from WS Bridge and stop reconnection."""
        self._stop_event.set()
        self._should_reconnect = False
        self._auth_ok = False
        async with self._ws_lock:
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
        logger.warning("[WSBridge] Disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Broadcast a message to all connected bots via WS Bridge."""
        if not self._auth_ok or not self._ws:
            return SendResult(success=False, message_id="", error="Not connected")

        import json, time
        payload = json.dumps({
            "type": "message",
            "from": self._bot_name,
            "from_agent": self._agent_id,
            "content": content,
            "ts": time.time(),
        })

        async with self._ws_lock:
            if not self._ws:
                return SendResult(success=False, message_id="", error="Not connected")
            try:
                await self._ws.send(payload)
                logger.warning("[WSBridge] >> %s", content[:120])
                return SendResult(success=True, message_id=str(time.time()))
            except Exception as e:
                logger.error("[WSBridge] send error: %s", e)
                return SendResult(success=False, message_id="", error=str(e))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {
            "name": "WS Bridge",
            "type": "group",
        }

    # ── Reader Loop ────────────────────────────────────────────────────

    async def _reader_loop(self) -> None:
        """Read messages from WS Bridge and dispatch."""
        import asyncio, json

        while not self._stop_event.is_set():
            async with self._ws_lock:
                ws = self._ws
            if not ws:
                break

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning("[WSBridge] Read error: %s", e)
                asyncio.create_task(self._reconnect_with_backoff())
                break

            if self._stop_event.is_set():
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[WSBridge] raw: %s", str(raw)[:100])
                continue

            await self._handle_ws_message(msg)

    async def _reconnect_with_backoff(self) -> None:
        delay = self._reconnect_delay
        self._reconnect_delay = min(delay * 1.5, 30.0)
        await __import__("asyncio").sleep(delay)
        logger.warning("[WSBridge] Reconnecting in %.0fs...", delay)
        if not self._stop_event.is_set():
            ok = await self.connect()
            if ok:
                self._reconnect_delay = 3.0

    # ── Message Handling ───────────────────────────────────────────────

    async def _handle_ws_message(self, msg: dict) -> None:
        """Handle incoming WS message."""
        msg_type = msg.get("type")

        if msg_type == "broadcast":
            content = msg.get("content", "")
            from_name = msg.get("from", "")
            from_agent = msg.get("from_agent", "")

            if not content or not from_name:
                return

            logger.warning("[WSBridge] << broadcast from=%s: %s", from_name, content[:200])

            # Filter self-messages
            if from_agent == self._agent_id or from_name == self._bot_name:
                return

            # Mention mode: only respond when keyword present
            if self._mention_mode:
                if self._mention_keyword not in content:
                    logger.warning(
                        "[WSBridge] Silent: no mention keyword '%s'", self._mention_keyword
                    )
                    return

            # Strip mention prefix if present
            text = content
            if text.startswith(self._mention_keyword):
                text = text[len(self._mention_keyword):].strip()

            await self._process_inbound_message(text, msg)

        elif msg_type == "auth_ok":
            logger.warning("[WSBridge] Re-auth OK — role=%s", msg.get("role"))
            self._auth_ok = True

        elif msg_type == "pairing_code":
            code = msg.get("code", "?")
            logger.warning(
                "[WSBridge] PAIRING CODE — %s (forward to admin)", code
            )

        elif msg_type == "error":
            logger.warning("[WSBridge] Server error: %s", msg.get("error", ""))

    async def _process_inbound_message(self, content: str, raw_msg: dict) -> None:
        """Build MessageEvent and dispatch to Gateway handler."""
        from datetime import datetime
        from gateway.platforms.base import MessageEvent, MessageType

        source = self.build_source(
            chat_id="ws_bridge_group",
            chat_name="WS Bridge",
            chat_type="group",
            user_id="ws_bridge_user",
            user_name="WS Bridge",
            message_id=str(raw_msg.get("ts", "")),
        )

        event = MessageEvent(
            text=content,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=raw_msg,
            message_id=str(raw_msg.get("ts", "")),
            timestamp=datetime.now(),
        )

        logger.warning("[WSBridge] Dispatching to handle_message")
        try:
            await self.handle_message(event)
        except Exception as e:
            logger.error("[WSBridge] handle_message error: %s", e, exc_info=True)


# ── Register ────────────────────────────────────────────────────────────


def register(ctx) -> None:
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
