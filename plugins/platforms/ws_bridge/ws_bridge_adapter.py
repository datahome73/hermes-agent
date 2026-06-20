"""WS Bridge Gateway plugin — broadcast group chat for bots.

R19: Multi-connection support — single WSBridgeAdapter manages
multiple independent WS connections (production + dev).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

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
    """Validate config: supports both single-connection (legacy) and multi-connection mode."""
    extra = config.extra or {}

    # R19: multi-connection mode — validate each connection
    connections = extra.get("connections")
    if connections and isinstance(connections, list):
        if not connections:
            logger.warning("[WSBridge] connections list is empty")
            return False
        valid = 0
        for i, conn in enumerate(connections):
            if not isinstance(conn, dict):
                continue
            aid = conn.get("agent_id")
            url = conn.get("url") or conn.get("ws_url")
            if aid and url:
                valid += 1
            else:
                logger.warning(
                    "[WSBridge] connection[%d] missing agent_id or url, skipping",
                    i,
                )
        if valid == 0:
            logger.warning("[WSBridge] no valid connections configured")
            return False
        return True

    # Single-connection mode (legacy)
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
    # Multi-connection: check if any connection has agent_id
    connections = extra.get("connections")
    if connections and isinstance(connections, list):
        for conn in connections:
            if isinstance(conn, dict) and conn.get("agent_id"):
                return True
        return False
    # Single-connection (legacy)
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

    # R19: multi-connection mode — pass through connections list as-is
    connections = extra.get("connections")
    if connections and isinstance(connections, list):
        # Validate each connection has required fields
        validated = []
        for i, conn in enumerate(connections):
            if not isinstance(conn, dict):
                continue
            aid = conn.get("agent_id")
            url = conn.get("url") or conn.get("ws_url")
            if aid and url:
                validated.append(conn)
            else:
                logger.warning(
                    "[WSBridge] connection[%d] missing agent_id or url, skipping",
                    i,
                )
        if validated:
            return {"connections": validated}
        # Fall through to single-connection mode if all connections invalid

    # Single-connection mode (legacy): resolve fields from config and env
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


# ── Connection Manager ──────────────────────────────────────────────


class _WSConnection:
    """Manages a single WS connection — connect, auth, reader, reconnect, send.

    Each connection is fully independent: its own websocket, lock, reader
    loop, and exponential-backoff reconnection.  Connections share nothing
    except the parent adapter reference for dispatching inbound messages.
    """

    def __init__(
        self,
        name: str,
        url: str,
        agent_id: str,
        app_id: str = "",
        bot_name: str = "Hermes",
        role: str = "member",
        mention_mode: bool = False,
        mention_keyword: str = "小爱",
        is_primary: bool = False,
        on_message: Optional[callable] = None,
    ):
        self.name = name
        self.url = _normalize_url(url)
        self.agent_id = agent_id
        self.app_id = app_id
        self.bot_name = bot_name
        self.role = role
        self.mention_mode = mention_mode
        self.mention_keyword = mention_keyword
        self.is_primary = is_primary
        self._on_message = on_message

        # WS state
        self._ws: Optional[Any] = None
        self._ws_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._should_reconnect = True
        self._reconnect_delay = 3.0
        self._auth_ok = False
        self._active_channel: str = "lobby"
        self._connected = False

    # ── Lifecycle ──────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to WS server and authenticate with exponential backoff."""
        if not self.agent_id or not self.url:
            logger.error("[WSBridge: %s] Missing agent_id or url", self.name)
            return False

        import websockets

        self._stop_event.clear()
        self._auth_ok = False
        self._should_reconnect = True
        backoff = self._reconnect_delay

        while self._should_reconnect:
            try:
                self._ws = await websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                )
            except asyncio.CancelledError:
                self._should_reconnect = False
                break
            except Exception as e:
                logger.error(
                    "[WSBridge: %s] Connection failed: %s — retry in %ds",
                    self.name, e, backoff,
                )
                if self._should_reconnect:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 300)
                continue

            # Connected — reset backoff
            self._reconnect_delay = 3.0
            backoff = 3.0
            logger.warning(
                "[WSBridge: %s] CONNECTED — sending auth...", self.name,
            )

            # Auth
            auth_msg = json.dumps({
                "type": "auth",
                "app_id": self.app_id,
                "agent_id": self.agent_id,
                "name": self.bot_name,
            })
            try:
                await self._ws.send(auth_msg)
            except Exception as e:
                logger.error("[WSBridge: %s] Auth send failed: %s", self.name, e)
                await self._ws.close()
                continue

            # Wait for auth response
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
                resp = json.loads(raw)
            except Exception as e:
                logger.error("[WSBridge: %s] No auth response: %s", self.name, e)
                await self._ws.close()
                continue

            if resp.get("type") == "auth_ok":
                self._auth_ok = True
                self._connected = True
                channel = resp.get("active_channel")
                if channel:
                    self._active_channel = channel
                logger.warning(
                    "[WSBridge: %s] Auth OK — role=%s agent=%s channel=%s",
                    self.name,
                    resp.get("role"), resp.get("agent_id", "")[:20],
                    self._active_channel,
                )
                # Start reader loop
                asyncio.create_task(self._reader_loop())
                return True
            elif resp.get("type") == "pairing_code":
                code = resp.get("code", "?")
                logger.warning(
                    "[WSBridge: %s] PAIRING CODE — %s (send to admin)",
                    self.name, code,
                )
                await self._ws.close()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue
            else:
                logger.error(
                    "[WSBridge: %s] Unexpected auth response: %s",
                    self.name, str(resp)[:200],
                )
                await self._ws.close()
                continue

        return False

    async def disconnect(self) -> None:
        """Stop reconnection and close WS."""
        self._stop_event.set()
        self._should_reconnect = False
        self._auth_ok = False
        self._connected = False
        async with self._ws_lock:
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
        logger.warning("[WSBridge: %s] Disconnected", self.name)

    async def send(self, content: str) -> SendResult:
        """Send a message through this connection."""
        if not self._auth_ok or not self._ws:
            return SendResult(success=False, message_id="", error="Not connected")

        channel = self._active_channel or "lobby"
        payload = json.dumps({
            "type": "message",
            "from_name": self.bot_name,
            "agent_id": self.agent_id,
            "from": self.bot_name,
            "from_agent": self.agent_id,
            "content": content,
            "channel": channel,
            "ts": time.time(),
        })

        async with self._ws_lock:
            if not self._ws:
                return SendResult(success=False, message_id="", error="Not connected")
            try:
                await self._ws.send(payload)
                logger.warning("[WSBridge: %s] >> %s", self.name, content[:120])
                return SendResult(success=True, message_id=str(time.time()))
            except Exception as e:
                logger.error("[WSBridge: %s] send error: %s", self.name, e)
                return SendResult(success=False, message_id="", error=str(e))

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Reader Loop ────────────────────────────────────────────────

    async def _reader_loop(self) -> None:
        """Read messages and dispatch via callback."""
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
                logger.warning(
                    "[WSBridge: %s] Read error: %s", self.name, e,
                )
                asyncio.create_task(self._reconnect_with_backoff())
                break

            if self._stop_event.is_set():
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "[WSBridge: %s] raw: %s", self.name, str(raw)[:100],
                )
                continue

            # Tag message with connection name for routing
            msg["_conn_name"] = self.name
            await self._handle_message(msg)

    async def _reconnect_with_backoff(self) -> None:
        delay = self._reconnect_delay
        self._reconnect_delay = min(delay * 1.5, 30.0)
        await asyncio.sleep(delay)
        logger.warning(
            "[WSBridge: %s] Reconnecting in %.0fs...", self.name, delay,
        )
        if not self._stop_event.is_set():
            ok = await self.connect()
            if ok:
                self._reconnect_delay = 3.0

    # ── Message Handling ───────────────────────────────────────────

    async def _handle_message(self, msg: dict) -> None:
        """Route inbound message to parent adapter's handler."""
        if self._on_message:
            await self._on_message(self, msg)


# ── Adapter ────────────────────────────────────────────────────────────


class WSBridgeAdapter(BasePlatformAdapter):
    """WS Bridge client adapter — manages multiple WS connections.

    R19: Supports dual connections (production + dev) via ``connections``
    list in config.  Legacy single-connection config still works unchanged.
    """

    supports_code_blocks = True
    name = "ws_bridge"

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("ws_bridge"))
        extra = config.extra or {}

        # R19: read multi-connection config
        connections_cfg = extra.get("connections")
        if connections_cfg and isinstance(connections_cfg, list):
            self._connections: List[_WSConnection] = []
            for i, conn in enumerate(connections_cfg):
                is_primary = (i == 0)
                ws_conn = _WSConnection(
                    name=conn.get("name", f"conn_{i}"),
                    url=conn.get("url") or conn.get("ws_url") or "",
                    agent_id=conn.get("agent_id") or "",
                    app_id=conn.get("app_id") or "",
                    bot_name=conn.get("bot_name") or "Hermes",
                    role=conn.get("role") or "member",
                    mention_mode=bool(conn.get("mention_mode", False)),
                    mention_keyword=conn.get("mention_keyword", "小爱"),
                    is_primary=is_primary,
                    on_message=self._on_connection_message,
                )
                self._connections.append(ws_conn)
            logger.warning(
                "[WSBridge] Multi-connection mode: %d connection(s)",
                len(self._connections),
            )
        else:
            # Single-connection mode (legacy)
            url = _normalize_url(
                extra.get("url") or extra.get("ws_url")
                or _env("WS_BRIDGE_URL") or ""
            )
            agent_id = extra.get("agent_id") or _env("WS_BRIDGE_AGENT_ID") or ""
            app_id = extra.get("app_id") or _env("WS_BRIDGE_APP_ID") or ""
            bot_name = extra.get("bot_name") or _env("WS_BRIDGE_BOT_NAME") or "Hermes"
            role = extra.get("role") or "member"
            mention_mode = bool(extra.get("mention_mode", False))
            mention_keyword = extra.get("mention_keyword", "小爱")

            ws_conn = _WSConnection(
                name="production",
                url=url,
                agent_id=agent_id,
                app_id=app_id,
                bot_name=bot_name,
                role=role,
                mention_mode=mention_mode,
                mention_keyword=mention_keyword,
                is_primary=True,
                on_message=self._on_connection_message,
            )
            self._connections = [ws_conn]
            logger.warning(
                "[WSBridge] Single-connection mode (agent=%s url=%s)",
                agent_id[:20], url,
            )

    def _get_connection(self, conn_name: Optional[str] = None) -> Optional[_WSConnection]:
        """Resolve a connection by name. Falls back to primary."""
        if conn_name:
            for conn in self._connections:
                if conn.name == conn_name:
                    return conn
        # Default: return primary (first) connection
        return self._connections[0] if self._connections else None

    # ── Lifecycle ──────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect all managed WS connections concurrently."""
        if not self._connections:
            logger.error("[WSBridge] No connections configured")
            return False

        logger.warning(
            "[WSBridge] Connecting %d connection(s)...",
            len(self._connections),
        )

        results = await asyncio.gather(
            *[conn.connect() for conn in self._connections],
            return_exceptions=True,
        )

        success_count = sum(
            1 for r in results if r is True
        )
        logger.warning(
            "[WSBridge] %d/%d connection(s) connected",
            success_count, len(self._connections),
        )
        return success_count > 0

    async def disconnect(self) -> None:
        """Disconnect all connections concurrently."""
        logger.warning(
            "[WSBridge] Disconnecting %d connection(s)...",
            len(self._connections),
        )
        await asyncio.gather(
            *[conn.disconnect() for conn in self._connections],
            return_exceptions=True,
        )
        logger.warning("[WSBridge] All connections disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a message. Supports routing by connection prefix.

        Format: ``conn_name:channel`` (e.g. ``production:lobby``,
        ``dev:workspace``).  Bare channel names (no ``:`` prefix) route
        to the primary connection.
        """
        # Parse connection name from chat_id if present
        target_conn: Optional[_WSConnection] = None
        target_channel = chat_id

        if ":" in chat_id:
            parts = chat_id.split(":", 1)
            conn_name = parts[0]
            target_channel = parts[1]
            target_conn = self._get_connection(conn_name)
            if not target_conn:
                logger.warning(
                    "[WSBridge] Unknown connection '%s', falling back to primary",
                    conn_name,
                )

        if not target_conn:
            target_conn = self._get_connection()

        if not target_conn:
            return SendResult(success=False, message_id="", error="No connection available")

        # Store target channel on the connection for the send
        original_channel = target_conn._active_channel
        if target_channel:
            target_conn._active_channel = target_channel

        result = await target_conn.send(content)

        # Restore active_channel if we overrode it
        if target_conn._active_channel != original_channel:
            target_conn._active_channel = original_channel

        return result

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {
            "name": "WS Bridge",
            "type": "group",
        }

    # ── Connection Message Router ──────────────────────────────────

    async def _on_connection_message(
        self,
        conn: _WSConnection,
        msg: dict,
    ) -> None:
        """Receive a message from a connection and dispatch to gateway handler.

        This is the callback passed to each _WSConnection.  It tags the
        message with connection metadata and feeds it into the standard
        message processing pipeline.
        """
        msg_type = msg.get("type")

        if msg_type == "broadcast":
            content = msg.get("content", "")
            from_name = msg.get("from", "")
            from_agent = msg.get("from_agent", "")

            # Track active channel
            broadcast_channel = msg.get("channel", "lobby")
            conn._active_channel = broadcast_channel

            if not content or not from_name:
                return

            logger.warning(
                "[WSBridge: %s] << broadcast from=%s: %s",
                conn.name, from_name, content[:200],
            )

            # Filter self-messages
            if from_agent == conn.agent_id or from_name == conn.bot_name:
                return

            # Mention mode: only respond when keyword present
            if conn.mention_mode:
                if conn.mention_keyword not in content:
                    logger.warning(
                        "[WSBridge: %s] Silent: no mention keyword '%s'",
                        conn.name, conn.mention_keyword,
                    )
                    return

            # Strip mention prefix if present
            text = content
            if text.startswith(conn.mention_keyword):
                text = text[len(conn.mention_keyword):].strip()

            await self._process_inbound_message(text, msg, conn)

        elif msg_type == "auth_ok":
            logger.warning(
                "[WSBridge: %s] Re-auth OK — role=%s",
                conn.name, msg.get("role"),
            )
            conn._auth_ok = True
            channel = msg.get("active_channel")
            if channel:
                conn._active_channel = channel

        elif msg_type == "pairing_code":
            code = msg.get("code", "?")
            logger.warning(
                "[WSBridge: %s] PAIRING CODE — %s (forward to admin)",
                conn.name, code,
            )

        elif msg_type == "error":
            logger.warning(
                "[WSBridge: %s] Server error: %s",
                conn.name, msg.get("error", ""),
            )

        elif msg_type == "channel_updated":
            new_channel = msg.get("active_channel") or msg.get("channel", "lobby")
            conn._active_channel = new_channel
            logger.warning(
                "[WSBridge: %s] Active channel updated to '%s'",
                conn.name, new_channel,
            )

        elif msg_type == "workspace_closing":
            conn._active_channel = "lobby"
            logger.warning(
                "[WSBridge: %s] Workspace closing, channel reset to lobby",
                conn.name,
            )

    async def _process_inbound_message(
        self,
        content: str,
        raw_msg: dict,
        conn: _WSConnection,
    ) -> None:
        """Build MessageEvent and dispatch to Gateway handler."""
        from datetime import datetime
        from gateway.platforms.base import MessageEvent, MessageType

        # Use actual channel from broadcast, fallback to lobby
        channel = raw_msg.get("channel", "lobby") or "lobby"

        # Include connection name in source so routing works
        source_channel = f"{conn.name}:{channel}"
        source = self.build_source(
            chat_id=source_channel,
            chat_name=source_channel,
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

        logger.warning(
            "[WSBridge: %s] Dispatching to handle_message", conn.name,
        )
        try:
            await self.handle_message(event)
        except Exception as e:
            logger.error(
                "[WSBridge: %s] handle_message error: %s",
                conn.name, e, exc_info=True,
            )


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
