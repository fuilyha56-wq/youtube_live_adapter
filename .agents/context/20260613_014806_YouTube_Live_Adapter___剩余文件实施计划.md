# YouTube Live Adapter — 剩余文件实施计划

> Created: 2026-06-13 01:48:06

# YouTube Live Adapter — 剩余文件实施计划

> Created: 2026-06-13
> 状态：Step 5 & Step 6（src/client.py + plugin.py）

## 已完成文件

- `__init__.py` ✅
- `manifest.json` ✅
- `config.py` ✅
- `src/__init__.py` ✅
- `src/api.py` ✅
- `src/dispatcher.py` ✅

## 待实现文件

### 1. `src/client.py` — YouTubePollClient

**职责：** 管理 HTTP 轮询循环、自适应间隔、Token 过期重获取。

**类设计：**

```python
class YouTubePollClient:
    """YouTube Live Chat HTTP 轮询客户端。
    
    管理轮询循环，自适应调整轮询间隔：
    - 有消息时保持基础间隔
    - 无消息时逐步增大间隔至上限
    
    Token 过期时自动重新获取，HTTP 错误时抛出异常由上层 session_loop 处理重连。
    """
    
    def __init__(
        self,
        *,
        api: YouTubeInnerTubeAPI,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
        video_id: str,
        poll_interval: float = 2.5,
        max_poll_interval: float = 60.0,
    ) -> None:
        self._api = api
        self._on_event = on_event  # 回调：adapter.on_platform_message
        self._video_id = video_id
        self._poll_interval = poll_interval
        self._max_poll_interval = max_poll_interval
        self._continuation_token: str | None = None
        self._current_interval = poll_interval
        self._running = False
        self._healthy = False
    
    async def run(self) -> None:
        """主轮询循环，阻塞直到 stop() 或不可恢复错误。
        
        流程：
        1. 获取初始 continuation token
        2. 循环轮询消息
        3. 有消息 → 逐条回调 on_event → 恢复基础间隔
        4. 无消息 → 逐步增大间隔
        5. TokenExpiredError → 重新获取 token
        6. 其他异常 → 向上抛出
        """
    
    async def stop(self) -> None:
        """停止轮询循环。"""
    
    @property
    def is_healthy(self) -> bool:
        """连接健康状态。"""
        return self._healthy and self._running
```

**关键实现细节：**

1. `run()` 方法：
   - 首先调用 `self._api.get_initial_continuation(self._video_id)` 获取初始 token
   - 设置 `self._healthy = True`
   - 进入 while self._running 循环
   - 调用 `self._api.get_live_chat_messages(self._continuation_token)` 获取 actions 和 new_token
   - 更新 `self._continuation_token = new_token`
   - 如果有 actions，逐条调用 `await self._on_event(action)`，重置 `self._current_interval = self._poll_interval`
   - 如果无 actions，`self._current_interval = min(self._current_interval * 1.2, self._max_poll_interval)`
   - `await asyncio.sleep(self._current_interval)`
   - 捕获 `TokenExpiredError`：重新获取 token，继续循环
   - 捕获 `httpx.HTTPStatusError`：设置 `self._healthy = False`，向上抛出
   - 捕获 `asyncio.CancelledError`：设置 `self._healthy = False`，向上抛出

2. `stop()` 方法：
   - 设置 `self._running = False`
   - 设置 `self._healthy = False`

3. 导入：
   - `from __future__ import annotations`
   - `import asyncio`
   - `from typing import Any, Awaitable, Callable`
   - `import httpx`
   - `from .api import YouTubeInnerTubeAPI, TokenExpiredError`
   - `from src.kernel.logger import get_logger`

### 2. `plugin.py` — YouTubeLiveAdapterPlugin + YouTubeLiveAdapter

**职责：** 插件注册 + Adapter 生命周期管理。

**导入路径（与 onebot_adapter 保持一致）：**

```python
from __future__ import annotations

import asyncio
import random
from typing import Any, cast

from mofox_wire import CoreSink, MessageEnvelope

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseAdapter, BasePlugin
from src.core.components.loader import register_plugin
from src.kernel.concurrency import get_task_manager

from config import YouTubeLiveAdapterConfig
from src.api import YouTubeInnerTubeAPI
from src.client import YouTubePollClient
from src.dispatcher import YouTubeMessageDispatcher, PLATFORM, SOURCE_PLATFORM, LIVE_VIRTUAL_GROUP_ID
```

**YouTubeLiveAdapterPlugin：**

```python
@register_plugin
class YouTubeLiveAdapterPlugin(BasePlugin):
    """YouTube Live Adapter 插件。"""
    
    plugin_name = "youtube_live_adapter"
    plugin_version = "0.1.0"
    plugin_author = "MoFox Team"
    plugin_description = "YouTube 直播间弹幕/SC/会员消息接入适配器"
    configs = [YouTubeLiveAdapterConfig]
    
    def get_components(self) -> list[type]:
        return [YouTubeLiveAdapter]
```

**YouTubeLiveAdapter（BaseAdapter 子类）：**

类属性：
```python
adapter_name = "youtube_live_adapter"
adapter_version = "0.1.0"
adapter_author = "MoFox Team"
adapter_description = "YouTube 直播间弹幕接入适配器"
platform = "live"                    # 与 bilibili 共享
source_platform = "youtube_live"     # 唯一标识
run_in_subprocess = False
```

构造函数：
```python
def __init__(self, core_sink: CoreSink, plugin: YouTubeLiveAdapterPlugin | None = None, **kwargs):
    super().__init__(core_sink, plugin=plugin, **kwargs)
    # 不传 transport（非 WebSocket）
    self._api: YouTubeInnerTubeAPI | None = None
    self._client: YouTubePollClient | None = None
    self._dispatcher: YouTubeMessageDispatcher | None = None
    self._poll_task_info: Any | None = None
    self._stopping = False
    self._reconnect_delay = 0.0
```

关键方法：

| 方法 | 实现 |
|---|---|
| `on_adapter_loaded()` | 验证配置（video_id 必填），创建 API/Dispatcher 实例，不启动轮询 |
| `on_adapter_unloaded()` | 停止轮询，关闭 API 客户端 |
| `start()` | 调用 `super().start()`，检查 enabled，用 `get_task_manager().create_task()` 启动 `_session_loop()` |
| `stop()` | 设置 `_stopping=True`，取消任务，调用 `super().stop()` |
| `from_platform_message(raw)` | 调用 `self._dispatcher.dispatch(raw)` 返回 `MessageEnvelope | None` |
| `_send_platform_message(envelope)` | no-op（仅入站），记录 debug 日志 |
| `health_check()` | 返回 `_client.is_healthy if _client else False` |
| `reconnect()` | no-op，由 session_loop 内部处理 |
| `get_bot_info()` | 返回 `{"bot_id": video_id, "bot_name": "YouTube Live", "platform": "live"}` |

**`_session_loop()` 核心流程：**

```python
async def _session_loop(self) -> None:
    while not self._stopping:
        try:
            await self._run_one_session()
            if self._stopping:
                break
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"会话异常: {exc}")
        
        if self._stopping:
            break
        if not config.connection.auto_reconnect:
            break
        await self._sleep_with_backoff()
```

**`_run_one_session()` 流程：**

```python
async def _run_one_session(self) -> None:
    # 1. 通过 API 获取初始 continuation token（由 client.run 内部处理）
    # 2. 创建 YouTubePollClient
    self._client = YouTubePollClient(
        api=self._api,
        on_event=self.on_platform_message,
        video_id=config.youtube.video_id,
        poll_interval=config.connection.poll_interval,
        max_poll_interval=config.connection.max_poll_interval,
    )
    # 3. await client.run()  # 阻塞直到断开
    try:
        await self._client.run()
    finally:
        self._client.stop()
        self._client = None
```

**`_sleep_with_backoff()` 指数退避 + 随机抖动：**

```python
async def _sleep_with_backoff(self) -> None:
    config = self._get_config()
    base = config.connection.reconnect_initial_delay
    max_delay = config.connection.reconnect_max_delay
    multiplier = config.connection.reconnect_backoff_multiplier
    
    delay = min(base * (multiplier ** self._reconnect_attempts), max_delay)
    # 随机抖动：±25%
    jitter = delay * 0.25 * (2 * random.random() - 1)
    actual_delay = max(1.0, delay + jitter)
    
    self._reconnect_attempts += 1
    logger.info(f"重连退避 {actual_delay:.1f}s (第 {self._reconnect_attempts} 次)")
    
    await asyncio.sleep(actual_delay)
```

**`_get_config()` 辅助方法：**

```python
def _get_config(self) -> YouTubeLiveAdapterConfig:
    if not self.plugin or not self.plugin.config:
        raise RuntimeError("YouTube Live Adapter 配置不可用")
    return cast(YouTubeLiveAdapterConfig, self.plugin.config)
```

**`on_platform_message()` 回调方法：**

```python
async def on_platform_message(self, raw: dict[str, Any]) -> None:
    """YouTubePollClient 的回调，接收原始 action dict。"""
    envelope = await self.from_platform_message(raw)
    if envelope:
        await self.core_sink.send(envelope)
```

**`start()` 方法详细实现：**

```python
async def start(self) -> None:
    config = self._get_config()
    if not config.plugin.enabled:
        logger.info("YouTube Live Adapter 已禁用，跳过启动")
        return
    
    # 重置状态
    self._stopping = False
    self._reconnect_attempts = 0
    
    # 调用父类 start（会触发 on_adapter_loaded + health_check_loop）
    await super().start()
    
    # 启动 session_loop
    tm = get_task_manager()
    self._poll_task_info = tm.create_task(
        self._session_loop(),
        name="youtube_live_adapter_session_loop",
        daemon=True,
    )
    logger.info("YouTube Live Adapter 已启动")
```

**`stop()` 方法详细实现：**

```python
async def stop(self) -> None:
    self._stopping = True
    
    # 停止 client
    if self._client:
        self._client.stop()
    
    # 取消 session_loop 任务
    if self._poll_task_info:
        tm = get_task_manager()
        try:
            tm.cancel_task(self._poll_task_info.task_id)
        except Exception:
            pass
        self._poll_task_info = None
    
    # 调用父类 stop
    await super().stop()
    logger.info("YouTube Live Adapter 已停止")
```

**`on_adapter_loaded()` 详细实现：**

```python
async def on_adapter_loaded(self) -> None:
    logger.info("YouTube Live Adapter 加载中...")
    config = self._get_config()
    
    video_id = config.youtube.video_id.strip()
    if not video_id:
        raise ValueError("youtube.video_id 不能为空，请在配置中设置直播视频 ID")
    
    # 创建 API 客户端
    self._api = YouTubeInnerTubeAPI(
        proxy=config.youtube.proxy_url,
        timeout=config.connection.request_timeout,
        language=config.youtube.language,
        client_name=config.youtube.client_name,
    )
    
    # 创建 Dispatcher
    self._dispatcher = YouTubeMessageDispatcher(
        video_id=video_id,
        debug_mode=config.plugin.debug_mode,
        filter_emoji=config.filter.filter_emoji,
        remove_hashtags=config.filter.remove_hashtags,
        max_message_length=config.filter.max_message_length,
        ignored_message_types=config.filter.ignored_message_types,
    )
    
    logger.info(f"YouTube Live Adapter 加载完成 (video_id={video_id})")
```

**`on_adapter_unloaded()` 详细实现：**

```python
async def on_adapter_unloaded(self) -> None:
    logger.info("YouTube Live Adapter 卸载中...")
    if self._api:
        await self._api.aclose()
        self._api = None
    self._dispatcher = None
    logger.info("YouTube Live Adapter 已卸载")
```

## 验收标准

1. `src/client.py` 实现完整的轮询循环、自适应间隔、Token 过期重获取
2. `plugin.py` 实现完整的插件注册 + Adapter 生命周期管理
3. 所有导入路径与 onebot_adapter 保持一致
4. 不传 transport（非 WebSocket 模式）
5. 使用 `get_task_manager().create_task()` 管理异步任务
6. 指数退避 + 随机抖动重连策略
7. 代码风格与现有文件一致（文档字符串、类型注解、注释）
