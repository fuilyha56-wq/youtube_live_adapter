# YouTube Live Adapter 实施计划（修订版）

> Created: 2026-06-12 19:30:11

# YouTube Live Adapter 实施计划（修订版）

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

### BaseConfig (src.core.components.base.config.BaseConfig)
- 使用 `@config_section` 装饰器定义配置节
- 使用 `Field()` 定义配置字段
- `config_name: ClassVar[str]` 指定配置文件名

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
class YouTubeLiveAdapterConfig(BaseConfig):
    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "YouTube Live 适配器配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        enabled: bool = Field(default=True, description="是否启用 YouTube Live 适配器")
        config_version: str = Field(default="1.0.0", description="配置文件版本", disabled=True)

    @config_section("youtube", title="YouTube 设置", tag="youtube")
    class YouTubeSection(SectionBase):
        video_id: str = Field(default="", description="直播视频 ID（从 URL 提取）")
        language: str = Field(default="zh", description="UI 语言")
        device_model: str = Field(default="...", description="Inner Tube 设备模型")

    @config_section("connection", title="连接设置", tag="network")
    class ConnectionSection(SectionBase):
        poll_interval: float = Field(default=1.0, description="轮询间隔（秒）", ge=0.5, le=10.0)
        auto_reconnect: bool = Field(default=True, description="是否自动重连")
        reconnect_initial_delay: float = Field(default=2.0, description="初始重连延迟（秒）")
        reconnect_max_delay: float = Field(default=60.0, description="最大重连延迟（秒）")
        request_timeout: float = Field(default=15.0, description="请求超时（秒）")
```

### 2. api.py - YouTube Inner Tube API 客户端

- 使用 `httpx.AsyncClient` 发送请求
- 实现 `get_live_chat_continuation(video_id) → str`：获取 continuation token
  - 请求直播页面 HTML
  - 解析 `ytInitialData` 中的 `liveChatContinuation`
- 实现 `get_live_chat_messages(continuation_token) → dict`：轮询消息
  - POST 到 `youtubei/v1/live_chat/get_live_chat`
  - 请求头包含 Inner Tube 所需的客户端标识
  - 返回包含 `actions` 数组和新的 `continuation` token 的响应

### 3. client.py - 轮询客户端

- 管理 continuation token 的获取和更新
- 轮询循环：每次请求获取新消息，更新 continuation token
- 自动重连退避（指数退避，参考 BaseAdapter 的 reconnect 机制）
- 心跳/超时检测
- 回调 `on_event(payload)` 将原始消息传递给 dispatcher
- 使用 `src.kernel.concurrency.get_task_manager()` 管理异步任务

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

### 5. plugin.py - 插件入口

```python
@register_plugin
class YouTubeLiveAdapterPlugin(BasePlugin):
    plugin_name = "youtube_live_adapter"
    plugin_description = "YouTube Live 直播间适配器"
    plugin_version = "1.0.0"
    configs = [YouTubeLiveAdapterConfig]

    def __init__(self, config: BaseConfig | None = None):
        super().__init__(config)
        self._adapter_cls = YouTubeLiveAdapter

    def get_components(self) -> list[type]:
        return [YouTubeLiveAdapter]


class YouTubeLiveAdapter(BaseAdapter):
    adapter_name = "youtube_live_adapter"
    adapter_version = "1.0.0"
    adapter_description = "YouTube Live 直播间适配器"
    platform = "live"
    run_in_subprocess = False

    def __init__(self, core_sink, plugin=None, **kwargs):
        # 不使用 transport（轮询模式，非 WebSocket）
        super().__init__(core_sink, plugin=plugin, **kwargs)
        self._api = None
        self._client = None
        self._dispatcher = None

    async def on_adapter_loaded(self):
        # 初始化 API、client、dispatcher
        ...

    async def on_adapter_unloaded(self):
        # 清理资源
        ...

    async def from_platform_message(self, raw):
        # 由 dispatcher 处理转换
        ...

    async def _send_platform_message(self, envelope):
        # no-op（只入站不出站）
        pass

    async def health_check(self):
        # 检查轮询客户端连接状态
        ...
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
