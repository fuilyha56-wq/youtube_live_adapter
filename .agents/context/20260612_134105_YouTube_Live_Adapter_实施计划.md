# YouTube Live Adapter 实施计划

> Created: 2026-06-12 13:41:05

# YouTube Live Adapter 实施计划

## 概述

为 Neo-MoFox 平台开发一个 YouTube Live 适配器插件，作为独立出站的 adapter，能够：
1. 启动时自动连接 YouTube Live API
2. 接入 YouTube 直播间所有消息类型（弹幕、礼物/Super Chat、Super Sticker、会员、点赞等）
3. 正确解析为 `MessageEnvelope` 格式，通过 `core_sink` 送入 MoFox-Bus
4. 作为 `platform = "live"` 的 adapter，与 anima_chatter 等 chatter 协同工作
5. 只入站不出站（`_send_platform_message` 为 no-op）

## YouTube Live API 技术方案

### YouTube Live Streaming API 分析

YouTube 直播弹幕接入有两种方式：

1. **YouTube Data API v3 + Pub/Sub (官方)**：需要 OAuth2 认证，通过 `youtube.liveChatMessages.list` 轮询获取消息，但频率限制严格（每请求消耗配额），不适合实时弹幕场景。

2. **YouTube Live Chat via WebSocket (非官方但广泛使用)**：通过解析 YouTube 直播页面的 `ytInitialData` 获取 continuation token，然后通过 `youtubei/v1/live_chat/get_live_chat` 端点轮询获取消息。这是目前所有第三方 YouTube 弹幕工具（如 pychat, yt-live-chat 等）使用的方式。

**选择方案 2**：使用 YouTube Inner Tube API 轮询方式，原因：
- 无需 OAuth2 复杂认证流程
- 可获取所有消息类型（弹幕、Super Chat、Super Sticker、会员、点赞等）
- 社区广泛验证，稳定性好
- 与 B站直播适配器架构一致（轮询/WebSocket 模式）

### 消息类型映射

| YouTube 消息类型 | 内部处理 |
|---|---|
| `textMessageEvent` | 弹幕 → `MessageEnvelope` text segment |
| `superChatEvent` | Super Chat → `[SC ¥xx]` 前缀 text segment + additional_config |
| `superStickerEvent` | Super Sticker → `[Super Sticker ¥xx]` 前缀 text segment |
| `memberMilestoneChatEvent` | 会员里程碑 → `[会员里程碑]` 前缀 text segment |
| `newSponsorEvent` | 新会员 → `[新会员]` notice segment |
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
    # [plugin] section
    enabled: bool = True
    
    # [youtube] section
    video_id: str = ""           # 直播视频 ID（从 URL 提取）
    api_key: str = ""            # YouTube API Key（可选，用于 Data API）
    language: str = "zh"         # UI 语言
    device_model: str = "..."    # Inner Tube 设备模型
    
    # [connection] section
    poll_interval: float = 1.0   # 轮询间隔（秒）
    auto_reconnect: bool = True
    reconnect_initial_delay: float = 2.0
    reconnect_max_delay: float = 60.0
    request_timeout: float = 15.0
```

### 2. api.py - YouTube Inner Tube API 客户端

- 使用 `httpx.AsyncClient` 发送请求
- 实现 `get_live_chat_continuation(video_id)` → 获取 continuation token
- 实现 `get_live_chat_messages(continuation_token)` → 轮询消息
- Inner Tube API 端点：`https://www.youtube.com/youtubei/v1/live_chat/get_live_chat`
- 请求头包含 Inner Tube 所需的 `X-YouTube-Client-Name`、`X-YouTube-Client-Version` 等

### 3. client.py - 轮询客户端

- 管理 continuation token 的获取和更新
- 轮询循环：每次请求获取新消息，更新 continuation token
- 自动重连逻辑（参考 bilibili_live_adapter）
- 心跳/超时检测
- 回调 `on_event(payload)` 将原始消息传递给 dispatcher

### 4. dispatcher.py - 消息分发器

- 解析 YouTube Live Chat 的 `actions` 数组
- 将每种消息类型转换为 `MessageEnvelope`
- 关键映射：
  - `platform = "live"`（与 bilibili_live_adapter 一致）
  - `source_platform = "youtube_live"`（区分来源）
  - `group_id = "live_room"`（虚拟群组）
  - `group_name = "YouTube Live {video_id}"`
  - 用户信息：`user_id` = YouTube channel ID, `nickname` = display name
  - Super Chat 金额信息放入 `additional_config`

### 5. plugin.py - 插件入口

- `YouTubeLiveAdapterPlugin(BasePlugin)` - 插件注册
- `YouTubeLiveAdapter(BaseAdapter)` - 适配器实现
  - `platform = "live"`
  - `source_platform = "youtube_live"`
  - `on_adapter_loaded()` - 初始化 API、client、dispatcher
  - `on_adapter_unloaded()` - 清理资源
  - `start()` - 启动轮询会话
  - `_send_platform_message()` - no-op（只入站不出站）
  - `health_check()` - 检查连接状态
  - `from_platform_message()` - 解析原始消息

## 与 anima_chatter 的协同

- `platform = "live"` 确保 stream_manager 将 YouTube 直播消息路由到 anima_chatter
- `source_platform = "youtube_live"` 让 anima_chatter 识别为 vtb_live 模式
- `additional_config` 中包含 `source_platform`、`source_room_id` 等元数据

## 依赖

- `httpx>=0.27.0` - HTTP 客户端
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
