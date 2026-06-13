# YouTube Live Adapter 实施计划

> Created: 2026-06-12 21:57:43

# YouTube Live Adapter 实施计划

## 一、架构总览

基于对 `bilibili_live_adapter`、`onebot_adapter`、`anima_chatter` 三个参考项目的源码分析，YouTube Live Adapter 需严格遵循 MoFox 插件架构：

### 核心架构模式（从 bilibili_live_adapter 提取）

| 组件 | 模式 | 说明 |
|---|---|---|
| **BaseAdapter** | `__init__(core_sink, plugin=None, **kwargs)` | 构造时传入 CoreSink，不传 transport（非 WebSocket） |
| **消息流** | client → `on_platform_message(raw)` → `from_platform_message(raw)` → `dispatcher.dispatch(raw)` → `MessageEnvelope` → `core_sink.send(envelope)` | 与 bilibili 一致 |
| **MessageBuilder** | `MessageBuilder().direction("incoming").platform(PLATFORM).text(...).from_user(...).from_group(...).build()` | 从 mofox_wire 导入 |
| **Config** | `BaseConfig` + `@config_section` + `Field` | 与 bilibili 一致 |
| **Task 管理** | `get_task_manager().create_task(..., daemon=True)` | 与 bilibili 一致 |
| **Logger** | `from src.kernel.logger import get_logger` | 与 bilibili 一致 |

### 关键常量

```python
PLATFORM = "live"                    # 与 bilibili 共享，anima_chatter 依赖此值
SOURCE_PLATFORM = "youtube_live"     # 唯一标识，写入 additional_config
LIVE_VIRTUAL_GROUP_ID = "live_room"  # 与 bilibili 共享 group_id 前缀
```

---

## 二、文件结构

```
E:\plugins\YouTube-adapter\
├── __init__.py           # 版本与作者信息
├── plugin.py             # BasePlugin + BaseAdapter 实现
├── config.py             # BaseConfig 配置定义
├── manifest.json         # 插件元信息
├── README.md             # 使用文档
├── .gitignore
└── src\
    ├── __init__.py
    ├── api.py            # YouTube Inner Tube API 客户端
    ├── client.py         # HTTP 轮询客户端（含重连、自适应间隔）
    └── dispatcher.py     # 消息分发器（action → MessageEnvelope）
```

---

## 三、各文件详细实现

### 3.1 `__init__.py`

```python
"""YouTube Live Adapter - YouTube 直播间弹幕接入适配器

将 YouTube Live Chat（Inner Tube API）的弹幕/SC/会员消息
转换为 MessageEnvelope 投递至 Neo-MoFox 消息总线。
"""

__version__ = "0.1.0"
__author__ = "MoFox Team"
```

### 3.2 `manifest.json`

```json
{
    "name": "youtube_live_adapter",
    "display_name": "YouTube Live Adapter",
    "version": "0.1.0",
    "description": "YouTube 直播间弹幕/SC/会员消息接入适配器，通过 Inner Tube API 轮询获取直播间消息并投递至 Neo-MoFox 消息总线",
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
    "min_core_version": "1.2.0-alpha",
    "python_dependencies": [
        "httpx>=0.27.0"
    ],
    "categories": ["chat"],
    "tags": ["youtube", "live", "danmaku", "superchat", "adapter"]
}
```

### 3.3 `config.py`

严格遵循 `BaseConfig` + `@config_section` + `Field` 模式，与 bilibili_live_adapter 的 config.py 结构一致。

**配置分区：**

| Section | 字段 | 说明 |
|---|---|---|
| `plugin` | `enabled`, `debug_mode` | 插件开关与调试 |
| `youtube` | `video_id`, `language`, `proxy_url`, `client_name` | YouTube 连接配置 |
| `connection` | `poll_interval`, `max_poll_interval`, `auto_reconnect`, `reconnect_initial_delay`, `reconnect_max_delay`, `reconnect_backoff_multiplier`, `request_timeout` | 轮询与重连 |
| `filter` | `filter_emoji`, `remove_hashtags`, `max_message_length`, `ignored_message_types` | 消息过滤 |
| `superchat` | `enable_currency_conversion`, `exchange_rate_api_url` | SC 货币转换 |

### 3.4 `plugin.py`

**YouTubeLiveAdapterPlugin（BasePlugin 子类）：**
- 使用 `@register_plugin` 装饰器
- `plugin_name = "youtube_live_adapter"`
- `config_file_name = "youtube_live_adapter"`（对应 config.toml）

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
    # 初始化 _api, _client, _dispatcher 为 None
    # 初始化 _poll_task_info, _stopping 等状态变量
```

关键方法实现：

| 方法 | 实现要点 |
|---|---|
| `on_adapter_loaded()` | 验证配置（video_id 必填），创建 API/Dispatcher 实例，不启动轮询 |
| `on_adapter_unloaded()` | 停止轮询，关闭 API 客户端 |
| `start()` | 调用 `super().start()`，检查 enabled，用 `get_task_manager().create_task()` 启动 `_session_loop()` |
| `stop()` | 设置 `_stopping=True`，取消任务，调用 `super().stop()` |
| `from_platform_message(raw)` | 调用 `self._dispatcher.dispatch(raw)` 返回 `MessageEnvelope \| None` |
| `_send_platform_message(envelope)` | no-op（仅入站），记录 debug 日志 |
| `health_check()` | 返回 `_client.is_healthy if _client else False` |
| `reconnect()` | no-op，由 session_loop 内部处理 |
| `get_bot_info()` | 返回 `{"bot_id": video_id, "bot_name": "YouTube Live", "platform": "live"}` |

**`_session_loop()` 核心流程：**

```
while not _stopping:
    try:
        await _run_one_session()
        if _stopping: break
    except CancelledError: break
    except Exception: logger.error(...)
    
    if _stopping: break
    if not auto_reconnect: break
    await _sleep_with_backoff()  # 指数退避 + 随机抖动
```

**`_run_one_session()` 流程：**

```
1. 通过 API 获取初始 continuation token
2. 创建 YouTubePollClient（传入 on_platform_message 回调）
3. await client.run()  # 阻塞直到断开
4. finally: client.stop()
```

### 3.5 `src/api.py` — YouTubeInnerTubeAPI

**职责：** 封装 YouTube Inner Tube API 的 HTTP 请求细节。

**关键设计：**

1. **多客户端 Fallback**：准备 WEB / iOS / TV 三套客户端配置，WEB 优先但可能需要 PO Token，iOS/TV 作为 fallback。

2. **获取初始 Continuation Token**：
   - 首先尝试通过 `/youtubei/v1/live_chat/get_live_chat` 获取（传入 videoId）
   - 如果失败，回退到抓取视频页面 HTML 解析 `ytInitialData`

3. **轮询消息**：
   - `POST /youtubei/v1/live_chat/get_live_chat`
   - 请求体：`{"continuation": token, "context": {...}}`
   - 返回 `(actions: list, new_token: str)`

4. **客户端配置**：

```python
CLIENT_CONFIGS = {
    "WEB": {
        "client_name": "WEB",
        "client_version": "2.20250612.00.00",
        "client_name_header": "1",
    },
    "IOS": {
        "client_name": "IOS",
        "client_version": "19.29.1",
        "client_name_header": "5",
    },
    "TV": {
        "client_name": "TVHTML5_SIMPLY",
        "client_version": "7.20250612.00.00",
        "client_name_header": "7",
    },
}
```

5. **代理支持**：httpx.AsyncClient 的 `proxy` 参数。

6. **错误处理**：
   - `YouTubeApiError`：API 返回错误
   - `TokenExpiredError`：continuation token 失效，需要重新获取

### 3.6 `src/client.py` — YouTubePollClient

**职责：** 管理轮询循环、自适应间隔、重连逻辑。

**核心设计：**

```python
class YouTubePollClient:
    def __init__(self, *, api, on_event, video_id, poll_interval, max_poll_interval, ...):
        self._api = api
        self._on_event = on_event          # 回调：adapter.on_platform_message
        self._continuation_token = None
        self._current_interval = poll_interval
        self._running = False
        self._healthy = False
    
    async def run(self):
        """主轮询循环，阻塞直到 stop() 或不可恢复错误"""
        self._running = True
        # 1. 获取初始 continuation token
        self._continuation_token = await self._api.get_initial_continuation(self._video_id)
        self._healthy = True
        
        while self._running:
            try:
                actions, new_token = await self._api.get_live_chat_messages(self._continuation_token)
                self._continuation_token = new_token
                
                if actions:
                    for action in actions:
                        await self._on_event(action)  # 逐条分发
                    self._current_interval = self._poll_interval  # 有消息时恢复常规间隔
                else:
                    # 无消息，逐步增大间隔
                    self._current_interval = min(self._current_interval * 1.2, self._max_poll_interval)
                
                await asyncio.sleep(self._current_interval)
                
            except TokenExpiredError:
                # Token 过期，重新获取
                self._continuation_token = await self._api.get_initial_continuation(self._video_id)
            except httpx.HTTPStatusError as e:
                self._healthy = False
                raise  # 交给 session_loop 处理重连
    
    async def stop(self):
        self._running = False
        self._healthy = False
    
    @property
    def is_healthy(self) -> bool:
        return self._healthy and self._running
```

### 3.7 `src/dispatcher.py` — YouTubeMessageDispatcher

**职责：** 将 YouTube Inner Tube API 的 action dict 转换为 `MessageEnvelope`。

**关键设计：**

1. **使用 MessageBuilder**（与 bilibili dispatcher 一致）：
```python
from mofox_wire import MessageBuilder, MessageEnvelope
from mofox_wire.types import UserRole
```

2. **消息类型映射**：

| YouTube renderer | 处理方法 | MessageBuilder 调用 |
|---|---|---|
| `liveChatTextMessageRenderer` | `_build_text_envelope()` | `.text(msg_text)` |
| `liveChatSuperChatRenderer` | `_build_super_chat_envelope()` | `.text(f"[SC {amount}] {msg}")` |
| `liveChatSuperStickerRenderer` | `_build_super_sticker_envelope()` | `.text(f"[Super Sticker {amount}] {name}")` |
| `liveChatMembershipGiftPurchaseRenderer` | `_build_gift_envelope()` | `.text(f"[会员礼物] ...")` |
| `liveChatSponsorshipsGiftRedemptionRenderer` | `_build_gift_redemption_envelope()` | `.text(f"[会员兑换] ...")` |
| `liveChatMemberMilestoneChatRenderer` | `_build_milestone_envelope()` | `.text(f"[会员里程碑] ...")` |

3. **dispatch 方法签名**：
```python
async def dispatch(self, action: dict[str, Any]) -> MessageEnvelope | None:
```

4. **action 结构解析**：
```python
# YouTube action 格式：
# {"addChatItemAction": {"item": {renderer_type: {renderer_data}}}}
# {"addLiveChatTickerItemAction": {...}}  # 置顶消息，暂忽略
# {"markChatItemsByAuthorAsDeletedAction": {...}}  # 删除，暂忽略
# {"markChatItemAsDeletedAction": {...}}  # 删除，暂忽略
```

5. **MessageBuilder 使用模式**（与 bilibili 一致）：
```python
builder = (
    MessageBuilder()
    .direction("incoming")
    .platform(PLATFORM)
    .text(content)
)
# from_user
builder.from_user(
    user_id=user_id,
    platform=PLATFORM,
    nickname=nickname,
    role=UserRole.MEMBER,  # SC 用户可设为 OPERATOR
)
# from_group
builder.from_group(
    group_id=LIVE_VIRTUAL_GROUP_ID,
    platform=PLATFORM,
    name=f"YouTube Live {video_id}",
)
envelope = builder.build()
# 注入 source_platform / source_room_id
self._inject_source_into_extra(envelope, additional)
```

6. **additional_config 注入**（与 bilibili 一致）：
```python
additional = {
    "source_platform": SOURCE_PLATFORM,
    "source_room_id": video_id,
    "original_type": "textMessageEvent",  # 或 superChatEvent 等
    # SC 特有字段
    "superchat_amount": amount,
    "superchat_currency": currency,
}
```

7. **消息去重**：使用 `message_id`（YouTube 的 `id` 字段）进行去重，维护最近 200 条 ID 的集合。

---

## 四、与 bilibili_live_adapter 的关键差异

| 方面 | bilibili_live_adapter | youtube_live_adapter |
|---|---|---|
| 传输方式 | WebSocket（长连接） | HTTP 轮询（短连接） |
| BaseAdapter 构造 | 传 `transport=WebSocketAdapterOptions(...)` | 不传 transport |
| 认证 | start_app → auth_body → WS auth | continuation token 轮询 |
| 心跳 | WS op=2 + HTTP /v2/app/heartbeat | 无需心跳，轮询即保活 |
| 重连 | WS 断开 → 重新 start_app | Token 失效 → 重新获取 token；HTTP 错误 → 指数退避重连 |
| 消息来源 | WS op=5 body | HTTP response actions 数组 |
| 用户 ID | open_id / uid | authorExternalChannelId |
| 金额 | 电池/金仓（需转换） | amountMicros + currency |
| system_reminder | 点赞数统计 | 暂不实现 |

---

## 五、验收标准

1. **插件加载**：启动 MoFox 后，YouTube Live Adapter 能被正确发现和加载
2. **配置验证**：video_id 为空时，adapter 拒绝启动并输出明确错误日志
3. **连接建立**：给定有效 video_id，能获取 continuation token 并开始轮询
4. **消息接收**：能接收普通弹幕、Super Chat、Super Sticker 等消息
5. **消息格式**：MessageEnvelope 的 platform/source_platform/group_id 等字段正确，anima_chatter 能识别
6. **重连机制**：网络断开后能自动重连（指数退避 + 随机抖动）
7. **自适应间隔**：无消息时逐步增大轮询间隔，有消息时恢复常规间隔
8. **健康检查**：`health_check()` 正确反映连接状态
9. **优雅停止**：`stop()` 能正确取消所有任务并清理资源
10. **代理支持**：配置 proxy_url 后能通过代理访问 YouTube API

---

## 六、实施步骤

### Step 1：创建项目骨架
- 创建目录结构
- 编写 `__init__.py`、`manifest.json`、`.gitignore`、`README.md`

### Step 2：实现 config.py
- 使用 BaseConfig + @config_section 模式
- 包含 plugin / youtube / connection / filter / superchat 五个分区

### Step 3：实现 src/api.py
- YouTubeInnerTubeAPI 类
- 多客户端配置（WEB/IOS/TV）
- get_initial_continuation() — 获取初始 token
- get_live_chat_messages() — 轮询消息
- 代理支持、错误处理

### Step 4：实现 src/dispatcher.py
- YouTubeMessageDispatcher 类
- dispatch() 方法解析 action → MessageEnvelope
- 各消息类型的 _build_*_envelope() 方法
- MessageBuilder 使用、additional_config 注入
- 消息去重

### Step 5：实现 src/client.py
- YouTubePollClient 类
- 轮询循环、自适应间隔
- Token 过期重获取
- is_healthy 属性

### Step 6：实现 plugin.py
- YouTubeLiveAdapterPlugin（@register_plugin）
- YouTubeLiveAdapter（BaseAdapter 子类）
- 完整的 start/stop/session_loop 流程
- from_platform_message / _send_platform_message / health_check / get_bot_info

### Step 7：静态检查与测试
- 运行 pyright / ruff 检查
- 编写基础单元测试（dispatcher 解析逻辑）
