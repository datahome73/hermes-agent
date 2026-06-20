# R19 双环境测试指南

**目标：** 验证 ws_bridge adapter 多连接支持（方案B）

## 测试前提

- Hermes Gateway 已 build/install 含 R19 变更
- 两个可用的 ws-bridge 环境（正式 + dev）
- 每个环境有独立的 agent_id

## 配置模板

### 多连接模式

```yaml
gateway:
  platforms:
    ws_bridge:
      enabled: true
      extra:
        connections:
          - name: production
            ws_url: wss://www.meyo123.com/ws/bot
            agent_id: "你的正式ID"
            bot_name: "泰虾"
          - name: dev
            ws_url: wss://ws-im-dev.datahome73.com/ws
            agent_id: "你的测试ID"
            bot_name: "泰虾-test"
      home_channel:
        platform: ws_bridge
        chat_id: lobby
```

### 单连接模式（向后兼容验证）

```yaml
gateway:
  platforms:
    ws_bridge:
      enabled: true
      extra:
        ws_url: wss://www.meyo123.com/ws/bot
        agent_id: "你的正式ID"
        bot_name: "泰虾"
      home_channel:
        platform: ws_bridge
        chat_id: lobby
```

## 测试用例

### TC1: 向后兼容
- 使用单连接配置启动 Gateway
- 验证 auth_ok
- 验证 lobby 消息收发正常

### TC2: 双连接在线
- 使用多连接配置（production + dev）
- 启动 Gateway
- 日志应有：`[WSBridge: production] Auth OK` + `[WSBridge: dev] Auth OK`

### TC3: conn_name:channel 路由
- 发送到 `production:lobby` 和 `dev:工作室`
- 消息到达正确环境

### TC4: 连接隔离
- 断开 dev 连接，验证 production 正常工作
- 重连 dev，production 不受影响

### TC5: 配置错误处理
- 配置缺少 agent_id 的连接
- `validate_config` 应返回 False 并警告

## 报告格式

完成后提交 `docs/R19/test-report-r19.md`

| TC | 用例 | 结果 | 备注 |
|:--|:----|:----:|:----:|
| 1 | 向后兼容 | ✅/❌ | |
| 2 | 双连接在线 | ✅/❌ | |
| 3 | 路由 | ✅/❌ | |
| 4 | 连接隔离 | ✅/❌ | |
| 5 | 配置错误 | ✅/❌ | |
