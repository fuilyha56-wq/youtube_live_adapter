# YouTube Live Adapter 双向能力完善 — 剩余项实施

> Created: 2026-06-13 17:44:53

# YouTube Live Adapter 双向能力完善 — 剩余项实施计划

> Created: 2026-06-13 17:37

## 背景

基于 `20260613_155546_YouTube_Live_Adapter_双向能力完善_最终版_.md` 计划，以下4项尚未实现，本计划完成它们。

## 改动清单

### 1. plugin.py — 添加 system_reminder 机制（观众人数推送）

**目标**：定期将 YouTube 直播间在线人数推送到 prompt，让 AI 感知直播间状态。

**新增 import**：
```python
from src.app.plugin_system.api import prompt_api
from src.core.prompt import SystemReminderBucket, SystemReminderInsertType
```

**新增实例变量**（在 `__init__` 中）：
```python
self._viewer_count_reminder_task_info: Any | None = None
self._last_published_viewer_count: int = -1
self._current_viewer_count: int = 0
```

**新增方法**：

1. `_viewer_count_reminder_loop()` — 异步循环，每 5 秒调用 `_publish_viewer_count_reminder()`
2. `_publish_viewer_count_reminder()` — 构建并发布 reminder：
   - 仅当 viewer count 变化时更新（避免无意义写入）
   - 调用 `prompt_api.add_system_reminder(bucket=SystemReminderBucket.ACTOR, name="youtube_live_room_status", content=..., insert_type=SystemReminderInsertType.DYNAMIC)`
   - content 格式：`YouTube 直播间在线人数: {count}`
3. `_cancel_viewer_count_reminder_task()` — 取消 reminder task
4. `_clear_viewer_count_reminder()` — 从 store 中移除 reminder（调用 `get_system_reminder_store().delete("actor", "youtube_live_room_status")`）

**需要确认**：`SystemReminderStore` 有 `delete(bucket, name)` 方法，可用于清理。

**集成点**：
- `start()` 中：在启动 session_loop 后，启动 reminder task
- `stop()` 中：调用 `_cancel_viewer_count_reminder_task()` 和 `_clear_viewer_count_reminder()`
- `on_adapter_unloaded()` 中：同上清理
- `_run_one_session()` 中：从 poll 响应中提取 viewer count，更新 `self._current_viewer_count`

**viewer count 提取方式**：
YouTube Inner Tube API 的 live chat 响应中，`continuationContents.liveChatContinuation` 下可能有 `viewerCountRenderer`。需要从 `client.py` 的 poll 响应中传递回来。

**对 client.py 的改动**：
- `YouTubePollClient` 的 `on_event` 回调目前只传递 action dict
- 需要新增一个 `on_viewer_count` 回调，或者将 viewer count 信息作为特殊 action 传递
- **推荐方案**：在 `client.py` 的 `_poll_once` 方法中，从响应提取 viewer count，通过新回调 `on_viewer_count: Callable[[int], Awaitable[None]] | None` 传递给 adapter

**对 client.py 的具体改动**：
```python
# YouTubePollClient.__init__ 新增参数
on_viewer_count: Callable[[int], Awaitable[None]] | None = None

# _poll_once 中，解析响应后提取 viewer count
# 路径: response.contuationContents.liveChatContinuation.viewerCountRenderer.viewCount
# 如果存在，调用 await self._on_viewer_count(count)
```

**对 plugin.py 的集成**：
```python
# _run_one_session 中创建 client 时传入回调
client = YouTubePollClient(
    api=self._api,
    on_event=self.on_platform_message,
    on_viewer_count=self._on_viewer_count,  # 新增
    ...
)

# 新增回调方法
async def _on_viewer_count(self, count: int) -> None:
    self._current_viewer_count = count
```

### 2. plugin.py — 添加连续失败计数

**目标**：用 `_consecutive_failures` 替代 `_reconnect_attempts`，语义更清晰，与 bilibili 对齐。

**改动**：
- `__init__` 中：将 `self._reconnect_delay = 0.0` 改为 `self._consecutive_failures: int = 0`
- `start()` 中：将 `self._reconnect_attempts = 0` 改为 `self._consecutive_failures = 0`
- `_run_one_session()` 中：成功时（`client.run()` 正常返回后）重置 `self._consecutive_failures = 0`
- `_sleep_with_backoff()` 中：将 `self._reconnect_attempts` 替换为 `self._consecutive_failures`
- `_session_loop()` 中：异常时 `self._consecutive_failures += 1`

### 3. plugin.py — 改进 _send_platform_message 日志

**改动**：
- 发送前：`logger.debug(f"出站消息发送中: {text[:30]}...")` （截取前30字符）
- 发送后：`logger.debug("出站消息发送成功")`
- 发送异常时：已有 error 级别日志（sender 内部），无需额外添加

### 4. dispatcher.py — 添加 liveChatPaidMessageRenderer 处理

**目标**：处理 YouTube 直播间的付费消息（非 SC 的付费消息）。

**renderer 结构**（参考 YouTube Inner Tube API）：
```json
{
  "liveChatPaidMessageRenderer": {
    "id": "...",
    "timestampUsec": "...",
    "authorExternalChannelId": "...",
    "authorName": { "simpleText": "..." },
    "message": { "runs": [...] },
    "amountDisplayString": "¥10",
    "amountMicros": "10000000",
    "currency": "CNY",
    "headerOverlayColor": {...},
    "bodyBackgroundColor": ...
  }
}
```

**改动**：
1. 在 `_dispatch_item` 中添加路由：
   ```python
   if "liveChatPaidMessageRenderer" in item:
       return await self._build_paid_message_envelope(item["liveChatPaidMessageRenderer"])
   ```
2. 新增 `_build_paid_message_envelope` 方法：
   - 结构类似 `_build_super_chat_envelope`
   - 提取 amountMicros、amountDisplayString、currency
   - 支持货币转换（与 SC 相同逻辑）
   - `original_type = "paidMessageEvent"`
   - `is_sc = True`（付费消息同样映射为 OPERATOR 角色）
3. 更新模块文档字符串，添加 `liveChatPaidMessageRenderer → 付费消息`

## 需要额外改动的文件
- `src/api.py` — `get_live_chat_messages` 需要额外返回 viewer count
- `src/client.py` — 需要新增 `on_viewer_count` 回调

### 5. api.py — get_live_chat_messages 返回 viewer count

**目标**：从 YouTube Inner Tube API 的轮询响应中提取观众人数。

**YouTube 响应结构**：
```json
{
  "continuationContents": {
    "liveChatContinuation": {
      "actions": [...],
      "continuations": [...],
      "viewerCountRenderer": {
        "viewCount": "1,234"
      }
    }
  }
}
```

**改动**：
- `get_live_chat_messages` 返回类型从 `tuple[list[dict], str]` 改为 `tuple[list[dict], str, int | None]`
- 在解析响应时，提取 `viewerCountRenderer.viewCount`，解析为 int（去除逗号等格式化字符）
- 返回 `(actions, new_token, viewer_count)`

**提取逻辑**：
```python
viewer_count: int | None = None
viewer_count_str = live_chat_continuation.get("viewerCountRenderer", {}).get("viewCount")
if viewer_count_str:
    try:
        viewer_count = int(viewer_count_str.replace(",", ""))
    except (ValueError, AttributeError):
        viewer_count = None
```

### 6. client.py — 新增 on_viewer_count 回调

**改动**：
- `__init__` 新增参数：`on_viewer_count: Callable[[int], Awaitable[None]] | None = None`
- `run()` 中，调用 `get_live_chat_messages` 后解包三个返回值
- 如果 `viewer_count is not None` 且 `self._on_viewer_count` 不为 None，调用回调
- 更新文档字符串

## 不需要改动的文件
- `src/sender.py` — 发送器无需改动
- `src/currency.py` — 货币转换无需改动
- `config.py` — 配置无需改动

## 验收标准
1. system_reminder 能定期推送直播间在线人数到 prompt（仅变化时更新）
2. 停止/卸载时正确清理 reminder task 和 store
3. 连续失败计数和指数退避正常工作，成功时重置
4. _send_platform_message 有发送前后的 debug 日志
5. dispatcher 能处理 liveChatPaidMessageRenderer 类型消息
6. 所有改动通过 ruff 和 pyright 静态检查
