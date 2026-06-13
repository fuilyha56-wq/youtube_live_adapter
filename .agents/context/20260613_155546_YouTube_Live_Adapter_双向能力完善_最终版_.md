# YouTube Live Adapter 双向能力完善（最终版）

> Created: 2026-06-13 15:55:46

# YouTube Live Adapter 双向能力完善

## 背景
学习 bilibili_live_adapter 架构，完善 YouTube 适配器的双向通信能力。
当前 YouTube adapter 已具备入站+出站文字的基本能力，但缺少以下 bilibili 具备的特性：
- system_reminder 机制（将直播间状态推送到 prompt）
- 连续失败计数与更健壮的重连
- dispatcher 对更多消息类型的处理
- 更完善的 _send_platform_message 日志

## 消息流确认
- **入站**: YouTubePollClient → on_platform_message → dispatcher.dispatch → MessageEnvelope → core_sink.send → stream_manager → chatter
- **出站-文字**: chatter → send_api.send_text → transport → _send_platform_message → sender.send_text_message → YouTube Live Chat
- **出站-语音/VTS**: anima_chatter 的 SayAndPerformAction（platform="live" → mode="vtb_live"）→ TTS + VTube Studio + send_api.send_text
- 以上流程均已打通，无需修改架构

## 改动清单

### 1. plugin.py — 添加 system_reminder 机制（学习 bilibili）
bilibili 有 `_likes_reminder_loop` 定期将直播间点赞数推送到 prompt。
YouTube 对应：添加 `_viewer_count_reminder_loop`，定期将直播间在线人数推送到 prompt。

实现方式：
- 在 `_run_one_session` 中，每次 poll 到消息时，从 YouTubeInnerTubeAPI 的响应中提取 viewer count（YouTube live chat 响应的 action 对象中有时包含 viewCountRenderer）
- 如果无法从 poll 响应中获取，则使用独立的 API 调用获取
- 通过 `prompt_api.add_system_reminder` 注入到 `SystemReminderBucket.ACTOR`
- reminder name: `"youtube_live_room_status"`
- refresh interval: 5.0 秒
- 在 `stop()` 和 `on_adapter_unloaded()` 中清理 reminder 和取消 task

需要新增的 import：
```python
from src.app.plugin_system.api import prompt_api
from src.core.prompt import SystemReminderBucket, SystemReminderInsertType
```

需要新增的实例变量：
```python
self._viewer_count_reminder_task_info: Any | None = None
self._last_published_viewer_count: int = -1
self._current_viewer_count: int = 0
```

需要新增的方法：
- `_viewer_count_reminder_loop()` — 定期推送 reminder
- `_publish_viewer_count_reminder()` — 构建并发布 reminder
- `_cancel_viewer_count_reminder_task()` — 取消 task
- `_clear_viewer_count_reminder()` — 清理 reminder store
- `_get_system_reminder_store()` — 获取 store（静态方法，与 bilibili 一致）

### 2. plugin.py — 添加连续失败计数（学习 bilibili）
bilibili 有 `_consecutive_failures` 计数器，用于指数退避计算。

实现方式：
- 添加 `self._consecutive_failures: int = 0`
- 在 `_run_one_session` 成功时重置为 0
- 在 `_sleep_with_backoff` 中使用 `_consecutive_failures` 计算延迟
- 当前 YouTube 的 `_sleep_with_backoff` 使用 `_reconnect_attempts`，改为使用 `_consecutive_failures` 保持与 bilibili 一致

### 3. plugin.py — 改进 _send_platform_message 日志
参考 bilibili 的日志风格，增加更详细的 debug 日志：
- 在发送前记录消息片段（前30字符）
- 在发送成功后记录确认

### 4. plugin.py — 改进 get_bot_info
参考 bilibili 返回主播信息，YouTube 版本返回：
```python
{
    "bot_id": config.youtube.video_id,
    "bot_name": "YouTube Live",
    "platform": PLATFORM,
}
```
当前实现已基本正确，无需改动。

### 5. dispatcher.py — 添加 liveChatPaidMessageRenderer 处理
YouTube 直播间中存在付费消息（非 SC 的付费消息），renderer key 为 `liveChatPaidMessageRenderer`。
添加 `_build_paid_message_envelope` 方法处理此类型。

### 6. config.py — 改进描述
- `OutboundSection.enabled` 描述更新：明确说明出站是必需功能
- `OutboundSection` 类文档字符串更新

### 7. plugin.py — on_adapter_loaded 验证逻辑
- 保留 outbound.enabled = False 时的阻止启动
- 保留 OAuth2 凭证缺失时的阻止启动
- 优化错误信息，更清晰

## 不需要改动的文件
- `src/client.py` — 轮询逻辑无需改动
- `src/api.py` — API 层无需改动
- `src/sender.py` — 发送器逻辑无需改动
- `src/currency.py` — 货币转换无需改动

## 验收标准
1. outbound.enabled = False 或 OAuth2 凭证不完整时，插件拒绝启动
2. 所有配置完整时，插件正常启动，入站+出站均工作
3. _send_platform_message 正确将 chatter 响应发送到 YouTube 直播间
4. system_reminder 机制能定期推送直播间在线人数到 prompt
5. 会话重连逻辑健壮，连续失败计数和指数退避正常工作
6. dispatcher 能处理 liveChatPaidMessageRenderer 类型消息
7. anima_chatter 的 vtb_live 模式正常工作（platform="live" 已确保）
