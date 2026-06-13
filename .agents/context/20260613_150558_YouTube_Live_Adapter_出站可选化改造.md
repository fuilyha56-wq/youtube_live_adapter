# YouTube Live Adapter 出站可选化改造

> Created: 2026-06-13 15:05:58

# YouTube Live Adapter 出站可选化改造

## 目标
让 YouTube 适配器支持**纯入站模式**（不需要 OAuth2 凭证即可启动），同时保留出站能力（配置 OAuth2 后可发送消息到直播间）。对齐 bilibili_live_adapter 的设计模式。

## 改动范围

### 1. config.py — 出站默认关闭
- `OutboundSection.enabled` 默认值从 `True` 改为 `False`
- 更新 `enabled` 字段描述：明确说明出站是可选的，入站始终工作
- 更新 `OutboundSection` 类文档字符串

### 2. plugin.py — on_adapter_loaded() 出站条件化
- **删除** `outbound.enabled = False` 时的 `raise ValueError`
- **删除** OAuth2 凭证缺失时的 `raise ValueError`
- 改为：仅当 `outbound.enabled = True` 且 OAuth2 凭证完整时，才初始化 `YouTubeLiveChatSender`
- 入站组件（API、Dispatcher、CurrencyConverter）始终初始化，不受出站配置影响
- 当 `outbound.enabled = True` 但凭证不完整时，打 warning 日志并跳过出站初始化（不阻止启动）

### 3. plugin.py — _send_platform_message() no-op 降级
- 当 `self._sender is None` 时（出站未启用），打 debug 日志并 return（与 bilibili 行为一致）
- 当 sender 存在时，保留现有发送逻辑不变

### 4. plugin.py — on_adapter_unloaded() 安全清理
- 仅在 sender 存在时才调用 `aclose()`
- 其他清理逻辑不变

### 5. plugin.py — start() 启动逻辑
- 移除对 `outbound.enabled` 的强制检查
- 入站轮询始终启动

## 不需要改动的文件
- `src/dispatcher.py` — 已覆盖所有主要消息类型
- `src/client.py` — 轮询逻辑无需改动
- `src/api.py` — API 层无需改动
- `src/sender.py` — 发送器逻辑无需改动
- `src/currency.py` — 货币转换无需改动

## 验收标准
1. `outbound.enabled = False` 时适配器正常启动，能接收直播间消息
2. `outbound.enabled = True` 且 OAuth2 凭证完整时，适配器能收发消息
3. `outbound.enabled = True` 但凭证不完整时，适配器仍能启动（入站工作），出站打 warning
4. `_send_platform_message` 在出站未启用时为 no-op，不打 error 日志
5. 现有出站功能不受影响
