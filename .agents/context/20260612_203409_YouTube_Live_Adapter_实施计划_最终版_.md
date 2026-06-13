# YouTube Live Adapter 实施计划（最终版）

> Created: 2026-06-12 20:34:09

# YouTube Live Adapter 实施计划（最终版）

> Created: 2026-06-12
> Status: Ready for implementation

## 概述

为 Neo-MoFox 平台开发一个 YouTube Live 适配器插件，作为独立出站的 adapter，能够：
1. 启动时自动连接 YouTube Live API（Inner Tube 轮询模式）
2. 接入 YouTube 直播间所有消息类型（弹幕、Super Chat、Super Sticker、会员、点赞等）
3. 正确解析为 `MessageEnvelope` 格式，通过 `core_sink` 送入 MoFox-Bus
4. 作为 `platform = "live"` 的 adapter，与 anima_chatter 等 chatter 协同工作
5. 只入站不出站（`_send_platform_message` 为 no-op）

## 核心接口确认

### BaseAdapter (src.core.components.base.adapter.BaseAdapter)
- 继承自 `mofox_wire.AdapterBase`
- 构造函数: `__init__(self, core_sink: CoreSink, plugin: BasePlugin | None = None, **kwargs)`
- 关键属性: `adapter_name`, `adapter_version`, `adapter_description`, `platform`
- 生命周期: `on_adapter_loaded()`, `on_adapter_unloaded()`
- 健康检查: `health_check() -> bool`
- 消息转换: `from_platform_message(raw: Any) -> MessageEnvelope | None`
- 发送: `_send_platform_message(envelope: MessageEnvelope) -> None`
- **重要**: BaseAdapter.__init__ 接受 `transport` 参数（WebSocketAdapterOptions/HttpAdapterOptions/None），YouTube adapter 不使用 WebSocket/HTTP transport，传 `None` 即可
- **重要**: BaseAdapter.start() 会调用 `on_adapter_loaded()` 和 `super().start()`，以及启动健康检查循环
- **重要**: BaseAdapter 有 `reconnect()` 方法，内部使用指数退避重连

### MessageEnvelope (mofox_wire.types.MessageEnvelope)
TypedDict 结构:
```python
{
    "direction": "incoming" | "outgoing",
    "message_info": {
        "platform": str,           # "live"
        "message_id": str,
        "time": float,
        "group_info": {
            "platform": str,       # "live"
            "group_id": str,       # "live_room"
            "group_name": str,     # "YouTube Live {video_id}"
        },
        "user_info": {
            "platform": str,       # "live"
            "role": UserRole,      # MEMBER / OWNER / OTHER
            "user_id": str,        # YouTube channel ID
            "user_nickname": str,  # display name
        },
        "additional_config": {
            "source_platform": "youtube_live",
            "source_room_id": str,  # video_id
            "super_chat_amount": str,  # Super Chat 金额（如有）
            "super_chat_currency": str,
        },
    },
    "message_segment": {
        "type": "text" | "notice",
        "data": str,               # 消息文本内容
        "translated_data": str,    # 可选翻译
    },
    "raw_message": Any,            # YouTube 原始 action 数据
    "platform": "live",
    "message_id": str,
}
```

### BasePlugin (src.core.components.base.plugin.BasePlugin)
- 属性: `plugin_name`, `plugin_description`, `plugin_version`, `configs`, `dependent_components`
- 方法: `get_components() -> list[type]`, `on_plugin_loaded()`, `on_plugin_unloaded()`
- 使用 `@register_plugin` 装饰器注册

### BaseConfig (src.core.components.base.config.BaseConfig)
- 使用 `@config_section` 装饰器定义配置节
- 使用 `Field()` 定义配置字段
- `config_name: ClassVar[str]` 指定配置文件名
- 每个配置节需要声明为类属性: `section_name: SectionClass = Field(default_factory=SectionClass)`

## YouTube Live API 技术方案

### 选择：YouTube Inner Tube API 轮询方式

原因：
- 无需 OAuth2 复杂认证流程
- 可获取所有消息类型
- 社区广泛验证，稳定性好
- 与 B站直播适配器架构一致（轮询模式）

### Inner Tube API 端点
- 获取 continuation token: 解析直播页面的 `ytInitialData`
- 轮询消息: `POST https://www.youtube.com/youtubei/v1/live_chat/get_live_chat`
- 请求头需包含: `X-YouTube-Client-Name`, `X-YouTube-Client-Version` 等

### 消息类型映射

| YouTube 消息类型 | 内部处理 |
|---|---|
| `textMessageEvent` | 弹幕 → text segment |
| `superChatEvent` | Super Chat → `[SC ¥xx]` 前缀 text segment + additional_config |
| `superStickerEvent` | Super Sticker → `[Super Sticker ¥xx]` 前缀 text segment |
| `memberMilestoneChatEvent` | 会员里程碑 → `[会员里程碑]` 前缀 text segment |
| `newSponsorEvent` | 新会员 → notice segment |
| `messageDeletedEvent` | 消息删除 → 忽略/日志 |
| `userBannedEvent` | 用户封禁 → 忽略/日志 |
| `tickerShowEvent` | 置顶消息 → 忽略 |

## 文件结构

```
E:\plugins\YouTube-adapter\
├── __init__.py              # 插件包初始化
├── plugin.py                # 插件入口：YouTubeLiveAdapter + YouTubeLiveAdapterPlugin
├── config.py                # 配置定义
├── manifest.json            # 插件清单
├── README.md                # 文档
├── .gitignore               # Git 忽略
└── src/
    ├── __init__.py
    ├── api.py               # YouTube Inner Tube API 客户端
    ├── client.py            # 轮询客户端（心跳、重连、消息拉取）
    └── dispatcher.py        # 消息分发器（YouTube 消息 → MessageEnvelope）
```

## 详细设计

### 1. config.py - 配置

```python
"""YouTube Live 适配器配置定义"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class YouTubeLiveAdapterConfig(BaseConfig):
    """YouTube Live 适配器配置"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "YouTube Live 适配器配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件基本设置"""

        enabled: bool = Field(
            default=True,
            description="是否启用 YouTube Live 适配器",
            label="启用适配器",
            tag="plugin",
        )
        config_version: str = Field(
            default="1.0.0",
            description="配置文件版本",
            label="配置版本",
            disabled=True,
            tag="general",
        )

    @config_section("youtube", title="YouTube 设置", tag="youtube")
    class YouTubeSection(SectionBase):
        """YouTube 直播间设置"""

        video_id: str = Field(
            default="",
            description="直播视频 ID（从 URL 提取）",
            label="视频 ID",
            placeholder="输入 YouTube 直播视频 ID",
            tag="youtube",
        )
        language: str = Field(
            default="zh",
            description="UI 语言",
            label="语言",
            tag="youtube",
        )

    @config_section("connection", title="连接设置", tag="network")
    class ConnectionSection(SectionBase):
        """连接和轮询设置"""

        poll_interval: float = Field(
            default=1.0,
            description="轮询间隔（秒）",
            label="轮询间隔",
            ge=0.5,
            le=10.0,
            step=0.5,
            input_type="slider",
            tag="timer",
        )
        auto_reconnect: bool = Field(
            default=True,
            description="是否自动重连",
            label="自动重连",
            tag="network",
        )
        reconnect_initial_delay: float = Field(
            default=2.0,
            description="初始重连延迟（秒）",
            label="初始重连延迟",
            ge=1.0,
            le=30.0,
            tag="timer",
        )
        reconnect_max_delay: float = Field(
            default=60.0,
            description="最大重连延迟（秒）",
            label="最大重连延迟",
            ge=10.0,
            le=300.0,
            tag="timer",
        )
        request_timeout: float = Field(
            default=15.0,
            description="请求超时（秒）",
            label="请求超时",
            ge=5.0,
            le=60.0,
            tag="timer",
        )

    # 声明配置节实例
    plugin: PluginSection = Field(default_factory=PluginSection)
    youtube: YouTubeSection = Field(default_factory=YouTubeSection)
    connection: ConnectionSection = Field(default_factory=ConnectionSection)
```

### 2. api.py - YouTube Inner Tube API 客户端

- 使用 `httpx.AsyncClient` 发送请求
- 实现 `async get_live_chat_continuation(video_id: str) -> str`：获取 continuation token
  - 请求直播页面 HTML
  - 解析 `ytInitialData` 中的 `liveChatContinuation`
- 实现 `async get_live_chat_messages(continuation_token: str) -> tuple[list[dict], str]`：轮询消息
  - POST 到 `youtubei/v1/live_chat/get_live_chat`
  - 请求头包含 Inner Tube 所需的客户端标识
  - 返回包含 `actions` 数组和新的 `continuation` token 的响应
- Inner Tube 请求头常量：
  ```python
  INNER_TUBE_HEADERS = {
      "Content-Type": "application/json",
      "User-Agent": "com.google.android.apps.youtube.music/17.36.4 (Linux; U; Android 12; GB) gzip",
      "X-YouTube-Client-Name": "21",
      "X-YouTube-Client-Version": "17.36.4",
  }
  INNER_TUBE_PARAMS = {
      "context": {
          "client": {
              "clientName": "WEB_MUSIC",
              "clientVersion": "1.20230101",
              "hl": "zh",
              "gl": "JP",
          }
      }
  }
  ```

### 3. client.py - 轮询客户端

- 管理 continuation token 的获取和更新
- 轮询循环：每次请求获取新消息，更新 continuation token
- 自动重连退避（指数退避）
- 心跳/超时检测
- 回调 `on_event(payload)` 将原始消息传递给 dispatcher
- 使用 `src.kernel.concurrency.get_task_manager()` 管理异步任务
- 关键方法：
  - `async start()` - 启动轮询
  - `async stop()` - 停止轮询
  - `async _poll_loop()` - 主轮询循环
  - `async _reconnect()` - 重连逻辑
  - `is_connected() -> bool` - 连接状态

### 4. dispatcher.py - 消息分发器

- 解析 YouTube Live Chat 的 `actions` 数组
- 将每种消息类型转换为 `MessageEnvelope`
- 关键映射：
  - `platform = "live"`（与 bilibili_live_adapter 一致）
  - `source_platform = "youtube_live"`（区分来源，放入 additional_config）
  - `group_id = "live_room"`
  - `group_name = "YouTube Live {video_id}"`
  - 用户信息：`user_id` = YouTube channel ID, `user_nickname` = display name
  - Super Chat 金额信息放入 `additional_config`
- 关键方法：
  - `dispatch(action: dict) -> MessageEnvelope | None` - 分发单条消息
  - `_parse_text_message(action) -> MessageEnvelope | None`
  - `_parse_super_chat(action) -> MessageEnvelope | None`
  - `_parse_super_sticker(action) -> MessageEnvelope | None`
  - `_parse_member_milestone(action) -> MessageEnvelope | None`
  - `_parse_new_sponsor(action) -> MessageEnvelope | None`

### 5. plugin.py - 插件入口

```python
"""YouTube Live 适配器插件入口

功能:
1. YouTube Inner Tube API 轮询 → 获取直播间消息
2. from_platform_message: YouTube action dict → MessageEnvelope
3. CoreSink → 送入 MoFox-Bus
4. 只入站不出站: _send_platform_message 为 no-op
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from mofox_wire import CoreSink, MessageEnvelope

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseAdapter, BasePlugin
from src.core.components.loader import register_plugin
from src.kernel.concurrency import get_task_manager

from .config import YouTubeLiveAdapterConfig
from .src.api import YouTubeInnerTubeAPI
from .src.client import YouTubeLiveClient
from .src.dispatcher import YouTubeLiveDispatcher

logger = get_logger("youtube_live_adapter")


@register_plugin
class YouTubeLiveAdapterPlugin(BasePlugin):
    """YouTube Live 适配器插件"""

    plugin_name = "youtube_live_adapter"
    plugin_description = "YouTube Live 直播间适配器"
    plugin_version = "1.0.0"

    configs = [YouTubeLiveAdapterConfig]

    dependent_components: list[str] = []

    def __init__(self, config: YouTubeLiveAdapterConfig | None = None):
        super().__init__(config)
        self._adapter_cls = YouTubeLiveAdapter

    def get_components(self) -> list[type]:
        return [YouTubeLiveAdapter]


class YouTubeLiveAdapter(BaseAdapter):
    """YouTube Live 适配器 - 轮询模式接入 mofox-wire"""

    adapter_name = "youtube_live_adapter"
    adapter_version = "1.0.0"
    adapter_description = "YouTube Live 直播间适配器"
    platform = "live"

    run_in_subprocess = False

    def __init__(self, core_sink: CoreSink, plugin: YouTubeLiveAdapterPlugin | None = None, **kwargs):
        # 不使用 transport（轮询模式，非 WebSocket/HTTP）
        super().__init__(core_sink, transport=None, plugin=plugin, **kwargs)
        self._api: YouTubeInnerTubeAPI | None = None
        self._client: YouTubeLiveClient | None = None
        self._dispatcher: YouTubeLiveDispatcher | None = None

    async def on_adapter_loaded(self) -> None:
        """适配器加载时初始化"""
        logger.info("YouTube Live 适配器正在启动...")

        if not self.plugin or not self.plugin.config:
            raise RuntimeError("YouTube Live 适配器缺少插件配置")

        config = cast(YouTubeLiveAdapterConfig, self.plugin.config)

        if not config.youtube.enabled:
            logger.info("YouTube Live 适配器已禁用")
            return

        if not config.youtube.video_id:
            raise RuntimeError("YouTube Live 适配器缺少 video_id 配置")

        # 初始化组件
        self._api = YouTubeInnerTubeAPI(
            language=config.youtube.language,
            timeout=config.connection.request_timeout,
        )
        self._dispatcher = YouTubeLiveDispatcher(
            video_id=config.youtube.video_id,
        )
        self._client = YouTubeLiveClient(
            api=self._api,
            video_id=config.youtube.video_id,
            poll_interval=config.connection.poll_interval,
            auto_reconnect=config.connection.auto_reconnect,
            reconnect_initial_delay=config.connection.reconnect_initial_delay,
            reconnect_max_delay=config.connection.reconnect_max_delay,
            on_event=self._on_event,
        )

        # 启动轮询客户端
        tm = get_task_manager()
        tm.create_task(
            self._client.start(),
            name="youtube_live_poll",
            daemon=True,
        )

        logger.info("YouTube Live 适配器已启动")

    async def on_adapter_unloaded(self) -> None:
        """适配器卸载时清理"""
        logger.info("YouTube Live 适配器正在停止...")
        if self._client:
            await self._client.stop()
            self._client = None
        self._api = None
        self._dispatcher = None
        logger.info("YouTube Live 适配器已停止")

    async def _on_event(self, action: dict) -> None:
        """处理从客户端收到的原始事件"""
        if not self._dispatcher:
            return
        envelope = self._dispatcher.dispatch(action)
        if envelope:
            await self.core_sink.send(envelope)

    async def from_platform_message(self, raw: Any) -> MessageEnvelope | None:
        """由 dispatcher 处理转换（实际在 _on_event 中直接调用）"""
        if not self._dispatcher:
            return None
        return self._dispatcher.dispatch(raw)

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:
        """no-op（只入站不出站）"""
        pass

    async def health_check(self) -> bool:
        """检查轮询客户端连接状态"""
        if not self._client:
            return False
        return self._client.is_connected()
```

### 6. manifest.json

```json
{
    "name": "youtube_live_adapter",
    "display_name": "YouTube Live 适配器",
    "version": "1.0.0",
    "description": "YouTube Live 直播间弹幕适配器",
    "author": "MoFox Team",
    "dependencies": {
        "plugins": [],
        "components": []
    },
    "include": [
        {
            "component_type": "adapter",
            "component_name": "youtube_live_adapter",
            "dependencies": []
        }
    ],
    "entry_point": "plugin.py",
    "min_core_version": "1.0.0",
    "python_dependencies": [
        "httpx>=0.27.0"
    ],
    "dependencies_required": true
}
```

### 7. __init__.py

```python
"""YouTube Live 适配器插件包"""
```

### 8. src/__init__.py

```python
"""YouTube Live 适配器内部模块"""
```

### 9. .gitignore

```
__pycache__/
*.pyc
*.pyo
.venv/
.env
*.egg-info/
dist/
build/
```

### 10. README.md

简要说明插件功能、配置项、使用方法。

## 与 anima_chatter 的协同

- `platform = "live"` 确保 stream_manager 将 YouTube 直播消息路由到 anima_chatter
- `source_platform = "youtube_live"` 让 anima_chatter 识别为 vtb_live 模式
- `additional_config` 中包含 `source_platform`、`source_room_id` 等元数据

## 依赖

- `httpx>=0.27.0` - HTTP 客户端（轮询用）
- `orjson>=3.9.0` - JSON 解析（主程序已有）

## 实施步骤

1. 创建项目目录结构和基础文件（__init__.py, manifest.json, .gitignore, README.md）
2. 实现 config.py - 配置定义
3. 实现 api.py - YouTube Inner Tube API 客户端
4. 实现 client.py - 轮询客户端
5. 实现 dispatcher.py - 消息分发器
6. 实现 plugin.py - 插件入口和适配器
7. 静态检查和测试

## 验收标准

1. 插件能被 Neo-MoFox 正确加载和注册
2. 配置项完整，支持 video_id、轮询间隔、重连策略等
3. 启动后自动连接 YouTube 直播间
4. 能接收并解析所有消息类型（弹幕、Super Chat、Super Sticker、会员等）
5. 消息正确转换为 MessageEnvelope 格式
6. 断线后自动重连
7. 代码质量符合项目规范（type hint、文档字符串、注释）
8. 通过 ruff 静态检查

## 重要注意事项

1. **BaseAdapter 构造函数**: `super().__init__(core_sink, transport=None, plugin=plugin, **kwargs)` - 必须传 `transport=None`，因为 YouTube adapter 不使用 WebSocket/HTTP transport
2. **配置节声明**: 每个配置节除了用 `@config_section` 装饰器定义外，还需要在类体中声明实例属性：`plugin: PluginSection = Field(default_factory=PluginSection)`
3. **日志**: 使用 `from src.app.plugin_system.api.log_api import get_logger`
4. **异步任务**: 使用 `from src.kernel.concurrency import get_task_manager` 管理异步任务
5. **插件注册**: 使用 `@register_plugin` 装饰器
6. **消息发送**: 通过 `self.core_sink.send(envelope)` 发送消息到 MoFox-Bus
7. **UserRole**: 来自 `mofox_wire.types.UserRole`，有 OWNER, OPERATOR, BOT, MEMBER, OTHER
8. **plugin.py 中的 config 引用**: 通过 `self.plugin.config` 获取配置，需要 cast 为 `YouTubeLiveAdapterConfig`
