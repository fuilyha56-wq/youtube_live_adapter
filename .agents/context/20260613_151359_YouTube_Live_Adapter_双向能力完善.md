# YouTube Live Adapter 双向能力完善

> Created: 2026-06-13 15:13:59

# YouTube Live Adapter 双向能力完善

## 目标
学习 bilibili_live_adapter 的架构，完善 YouTube 适配器的双向通信能力。出站为强制要求——未配齐则插件不启动。

## 改动范围

### 1. config.py — 出站默认开启 + 描述完善
- `OutboundSection.enabled` 默认值保持 `True`（出站强制）
- 更新 `enabled` 字段描述：明确说明出站是必需功能，关闭将导致插件无法启动
- 更新 `OutboundSection` 类文档字符串：说明出站为必需配置

### 2. plugin.py — on_adapter_loaded() 验证逻辑优化
- 保留 outbound.enabled = False 时的阻止启动逻辑
- 保留 OAuth2 凭证缺失时的阻止启动逻辑
- 优化错误信息，更清晰地说明出站是必需的
- 参考 bilibili：在 on_adapter_loaded 中初始化所有组件，在 start() 中启动会话

### 3. plugin.py — 添加 system_reminder 机制（学习 bilibili）
- bilibili 有 `_likes_reminder_loop` 定期将直播间点赞数推送到 prompt
- YouTube 对应：添加 `_viewer_count_reminder_loop`，定期将直播间在线人数推送到 prompt
  - 使用 YouTubeInnerTubeAPI 获取直播间状态（如果 API 支持）
  - 通过 `prompt_api.add_system_reminder` 注入到 SystemReminderBucket.ACTOR
  - 在 stop() 和 on_adapter_unloaded() 中清理 reminder
- 如果 InnerTube API 不方便获取在线人数，则跳过此功能，仅添加框架预留

### 4. plugin.py — _send_platform_message() 增强
- 当前实现已基本正确，但参考 bilibili 的日志风格，增加更详细的 debug 日志
- 当 sender 初始化失败时打 error 而非静默返回

### 5. plugin.py — get_bot_info() 完善
- 参考 bilibili 返回主播信息，YouTube 版本返回 video_id 和频道信息

### 6. plugin.py — 会话管理健壮性
- 参考 bilibili 的 `_consecutive_failures` 计数和指数退避
- 当前 YouTube 已有 `_sleep_with_backoff`，检查是否需要增加连续失败计数

## 不需要改动的文件
- `src/dispatcher.py` — 已覆盖所有主要消息类型
- `src/client.py` — 轮询逻辑无需改动
- `src/api.py` — API 层无需改动
- `src/sender.py` — 发送器逻辑无需改动
- `src/currency.py` — 货币转换无需改动

## 验收标准
1. outbound.enabled = False 或 OAuth2 凭证不完整时，插件拒绝启动
2. 所有配置完整时，插件正常启动，入站+出站均工作
3. _send_platform_message 正确将 chatter 响应发送到 YouTube 直播间
4. system_reminder 机制（如实现）能定期推送直播间状态到 prompt
5. 会话重连逻辑健壮，指数退避正常工作
