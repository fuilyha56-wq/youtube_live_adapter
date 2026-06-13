# YouTube Live Adapter 双向能力完善 — 剩余项实施

> Created: 2026-06-13 18:19:39

# YouTube Live Adapter 双向能力完善 — 剩余项实施

> Created: 2026-06-13

## 背景

基于之前的计划文档，以下6项改动尚未实现，本计划完成它们。参考了 bilibili_live_adapter 的 system_reminder 机制和连续失败计数实现。

## 改动清单

### 1. api.py — get_live_chat_messages 返回 viewer count

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
- `get_live_chat_messages` 返回类型从 `tuple[list[dict[str, Any]], str]` 改为 `tuple[list[dict[str, Any]], str, int | None]`
- 在解析响应时，提取 `viewerCountRenderer.viewCount`，解析为 int（去除逗号等格式化字符）
- 返回 `(actions, new_token, viewer_count)`
- 更新 docstring 和 Usage 示例

**提取逻辑**（在 `get_live_chat_messages` 中，提取 `new_token` 之后）：
```python
# 提取观众人数
viewer_count: int | None = None
viewer_count_str = live_chat_continuation.get("viewerCountRenderer", {}).get("viewCount")
if viewer_count_str:
    try:
        viewer_count = int(str(viewer_count_str).replace(",", ""))
    except (ValueError, AttributeError):
        viewer_count = None
```

### 2. client.py — 新增 on_viewer_count 回调

**改动**：
- `__init__` 新增参数：`on_viewer_count: Callable[[int], Awaitable[None]] | None = None`
- 保存为 `self._on_viewer_count`
- `run()` 中，调用 `get_live_chat_messages` 后解包三个返回值：`actions, new_token, viewer_count = await self._api.get_live_chat_messages(...)`
- 如果 `viewer_count is not None` 且 `self._on_viewer_count is not None`，调用 `await self._on_viewer_count(viewer_count)`
- 更新 docstring

### 3. plugin.py — 添加 system_reminder 机制（观众人数推送）

**目标**：定期将 YouTube 直播间在线人数推送到 prompt，让 AI 感知直播间状态。

**新增 import**：
```python
from src.app.plugin_system.api import prompt_api
from src.core.prompt import SystemReminderBucket, SystemReminderInsertType
```

**新增模块级常量**：
```python
# system_reminder 名称（观众人数状态）
_VIEWER_COUNT_REMINDER_NAME = "youtube_live_room_status"

# 观众人数刷新间隔（秒）
_VIEWER_COUNT_REFRESH_INTERVAL = 5.0
```

**新增实例变量**（在 `__init__` 中）：
```python
self._viewer_count_reminder_task_info: Any | None = None
self._last_published_viewer_count: int = -1
self._current_viewer_count: int = 0
```

**新增方法**：

1. `_viewer_count_reminder_loop()` — 异步循环，每 `_VIEWER_COUNT_REFRESH_INTERVAL` 秒调用 `_publish_viewer_count_reminder()`
   - 参考bilibili的 `_likes_reminder_loop` 实现
   - 使用 `asyncio.sleep` + `self._stopping` 检查

2. `_publish_viewer_count_reminder()` — 构建并发布 reminder：
   - 仅当 viewer count 变化时更新（避免无意义写入）
   - 调用 `prompt_api.add_system_reminder(bucket=SystemReminderBucket.ACTOR, name=_VIEWER_COUNT_REMINDER_NAME, content=..., insert_type=SystemReminderInsertType.DYNAMIC)`
   - content 格式：`YouTube 直播间在线人数: {count}`
   - 异常时 logger.debug 记录

3. `_cancel_viewer_count_reminder_task()` — 取消 reminder task
   - 参考bilibili的 `_cancel_likes_reminder_task`

4. `_clear_viewer_count_reminder()` — 从 store 中移除 reminder
   - 调用 `get_system_reminder_store().delete(SystemReminderBucket.ACTOR, _VIEWER_COUNT_REMINDER_NAME)`
   - 参考bilibili的 `_clear_likes_reminder`

5. `_get_system_reminder_store()` — 静态方法，获取 store
   - `from src.core.prompt import get_system_reminder_store; return get_system_reminder_store()`

6. `_on_viewer_count(count: int)` — 回调方法，更新 `self._current_viewer_count`

**集成点**：
- `start()` 中：在启动 session_loop 后，启动 reminder task
  ```python
  self._viewer_count_reminder_task_info = tm.create_task(
      self._viewer_count_reminder_loop(),
      name="youtube_live_adapter.viewer_count_reminder",
      daemon=True,
  )
  ```
- `stop()` 中：调用 `_cancel_viewer_count_reminder_task()` 和 `_clear_viewer_count_reminder()`
- `on_adapter_unloaded()` 中：同上清理
- `_run_one_session()` 中：创建 client 时传入 `on_viewer_count=self._on_viewer_count`

### 4. plugin.py — 添加连续失败计数

**目标**：用 `_consecutive_failures` 替代 `_reconnect_attempts`，语义更清晰，与 bilibili 对齐。

**改动**：
- `__init__` 中：将 `self._reconnect_delay = 0.0` 改为 `self._consecutive_failures: int = 0`
- `start()` 中：将 `self._reconnect_attempts = 0` 改为 `self._consecutive_failures = 0`
- `_run_one_session()` 中：成功时（`client.run()` 正常返回后，在 try 块内、finally 之前）重置 `self._consecutive_failures = 0`
- `_sleep_with_backoff()` 中：
  - 将 `self._reconnect_attempts` 替换为 `self._consecutive_failures`
  - 在方法开头递增 `self._consecutive_failures += 1`
  - 退避公式改为：`delay = min(base * (multiplier ** (self._consecutive_failures - 1)), max_delay)`（与bilibili一致，首次失败 delay=base）
  - 移除末尾的 `self._reconnect_attempts += 1`
- `_session_loop()` 中：异常时不需要额外递增（已在 _sleep_with_backoff 中递增）

### 5. plugin.py — 改进 _send_platform_message 日志

**改动**：
- 发送前：在 `await self._sender.send_text_message(text)` 之前添加 `logger.debug(f"出站消息发送中: {text[:30]}...")`
- 发送后：在 `await self._sender.send_text_message(text)` 之后添加 `logger.debug("出站消息发送成功")`

### 6. dispatcher.py — 添加 liveChatPaidMessageRenderer 处理

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
1. 在 `_dispatch_item` 中添加路由（在 `liveChatPaidStickerRenderer` 之后）：
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
   - 格式化显示：`[付费消息 ¥10] 消息内容` 或 `[付费消息 ¥10 ≈ ¥72 CNY] 消息内容`
3. 更新模块文档字符串，添加 `liveChatPaidMessageRenderer → 付费消息`

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
6. api.py 的 get_live_chat_messages 正确返回三元素元组
7. client.py 正确解包三元素元组并回调 on_viewer_count
8. 所有改动通过 ruff 和 pyright 静态检查
