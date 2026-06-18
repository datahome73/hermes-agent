# R7 第二轮回归测试报告

**测试日期:** 2026-06-17  
**测试对象:** `plugins/platforms/ws_bridge/ws_bridge_adapter.py`  
**升级提交:** 1091581e3a356935615d8370a683114a41b52092 (小谷)  
**测试者:** @泰虾 (爱泰)  

---

## 测试结果总览

| # | 测试项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 适配器 send() 带 channel 字段 | ✅ 通过 | 代码审查确认 |
| 2 | auth_ok 读取 active_channel | ✅ 通过 | 代码审查确认 |
| 3 | broadcast 频道记录逻辑 | ✅ 通过 | 代码审查确认 |
| 4 | channel_updated 处理 | ✅ 通过 | 代码审查确认 |
| 5 | workspace_closing 重置 | ✅ 通过 | 代码审查确认 |
| 6 | 编译完整性 py_compile | ✅ 通过 | 无语法错误 |
| 7 | 向后兼容（旧服务端不返回 channel） | ✅ 通过 | 默认 lobby |
| | **总计 7/7 通过** | **✅ 100%** | |

---

## 逐项测试详情

### 1. 适配器 send() 带 channel 字段

**预期:** `send()` 方法在 JSON 负载中包含 `"channel"` 字段，用于服务端 workspace 路由。

**实际:** `send()` 方法在第 262-274 行：

```python
channel = self._active_channel or "lobby"
payload = json.dumps({
    "type": "message",
    "from_name": self._bot_name,
    "agent_id": self._agent_id,
    "from": self._bot_name,
    "from_agent": self._agent_id,
    "content": content,
    "channel": channel,  # R7: workspace channel routing
    "ts": time.time(),
})
```

**判定:** ✅ 通过 — `channel` 字段已包含在出站消息中，值为 `self._active_channel`，fallback 为 `"lobby"`。

---

### 2. auth_ok 读取 active_channel

**预期:** 初始连接时从 `auth_ok` 响应的 `active_channel` 字段读取并设置 `_active_channel`。

**实际:** `connect()` 方法在第 207-216 行：

```python
if resp.get("type") == "auth_ok":
    self._auth_ok = True
    # R7: read active channel from server
    channel = resp.get("active_channel")
    if channel:
        self._active_channel = channel
    logger.warning(
        "[WSBridge] Auth OK — role=%s agent_id=%s channel=%s",
        resp.get("role"), resp.get("agent_id", "")[:20], self._active_channel,
    )
```

初始值在第 124 行：`self._active_channel: str = "lobby"`

**判定:** ✅ 通过 — 连接时读取 `active_channel`；若不返回，保持初始 `"lobby"`。

---

### 3. broadcast 频道记录逻辑

**预期:** 收到 `broadcast` 消息时，提取 `channel` 字段并更新 `_active_channel`，确保后续回复路由到正确频道。

**实际:** `_handle_ws_message()` 方法在第 348-351 行：

```python
# R7: record broadcast channel for member context routing
broadcast_channel = msg.get("channel", "lobby")
if broadcast_channel and broadcast_channel != "lobby":
    self._active_channel = broadcast_channel
```

**判定:** ✅ 通过 — 非 lobby 的 broadcast 消息会更新 `_active_channel`，使 bot 的回复自动路由回该 workspace。

---

### 4. channel_updated 处理

**预期:** 服务端发送 `channel_updated` 消息时，客户端更新 `_active_channel`。

**实际:** `_handle_ws_message()` 方法在第 394-398 行：

```python
elif msg_type == "channel_updated":
    # R7: server confirms active channel change
    new_channel = msg.get("active_channel") or msg.get("channel", "lobby")
    self._active_channel = new_channel
    logger.warning("[WSBridge] Active channel updated to '%s'", new_channel)
```

**判定:** ✅ 通过 — 双字段兼容（`active_channel` 或 `channel`），fallback 至 `"lobby"`。

---

### 5. workspace_closing 重置

**预期:** 收到 `workspace_closing` 消息时，将 `_active_channel` 重置为 `"lobby"`。

**实际:** `_handle_ws_message()` 方法在第 400-403 行：

```python
elif msg_type == "workspace_closing":
    # R7: workspace closing — reset active channel
    self._active_channel = "lobby"
    logger.warning("[WSBridge] Workspace closing, channel reset to lobby")
```

**判定:** ✅ 通过 — workspace 关闭后自动回到 lobby，防止消息发往已关闭的频道。

---

### 6. 编译完整性 py_compile

**命令:**
```bash
python3 -m py_compile plugins/platforms/ws_bridge/ws_bridge_adapter.py
python3 -m py_compile plugins/platforms/ws_bridge/__init__.py
```

**结果:** 两文件均无语法错误。

**判定:** ✅ 通过

---

### 7. 向后兼容（旧服务端不返回 channel → 默认 lobby）

**场景:** 旧版 ws-bridge 服务端不会在 `auth_ok` 响应中包含 `active_channel`，也不会在 `broadcast` 中包含 `channel`。

**验证:**

1. **send() 兼容性** — 第 264 行 `channel = self._active_channel or "lobby"` — 初始值为 `"lobby"`，确保出站消息默认路由到大厅。

2. **auth_ok 兼容性** — 第 210-211 行 `channel = resp.get("active_channel")` → `if channel` → 当旧服务端没有该字段时，`channel` 为 `None`，`_active_channel` 保持初始值 `"lobby"`。

3. **broadcast 兼容性** — 第 349 行 `msg.get("channel", "lobby")` — 旧 broadcast 无 `channel` 字段时，默认取 `"lobby"`，且条件 `broadcast_channel != "lobby"` 阻止更新（保持原位）。

4. **配置自动检测** — 初始值在 `__init__` 第 124 行设为 `"lobby"`，无需服务端配合即可工作。

**判定:** ✅ 通过 — 所有路径均有 fallback，旧服务端无 `channel`/`active_channel` 字段时表现与升级前一致。

---

## 结论

**全部 7 项测试通过，R7 ws_bridge_adapter 升级回归测试 100% 通过。**

| 指标 | 数值 |
|------|------|
| 测试总数 | 7 |
| 通过 | 7 |
| 失败 | 0 |
| 通过率 | 100% |

升级内容（自 9bb22d6c4 基础版本）:
- +34 行新增（`_active_channel` 状态管理）
- -2 行移除（旧的 logger 格式）
- 覆盖 4 种消息类型：`broadcast`, `auth_ok`, `channel_updated`, `workspace_closing`
- 完整向后兼容旧服务端
- 编译无错误

**建议:** 可合并至 dev 分支投入生产使用。
