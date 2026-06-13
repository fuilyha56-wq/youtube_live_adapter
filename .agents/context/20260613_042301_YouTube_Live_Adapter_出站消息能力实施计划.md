# YouTube Live Adapter 出站消息能力实施计划

> Created: 2026-06-13 04:23:01

# YouTube Live Adapter 出站消息能力实施计划

## 目标

为 YouTube Live Adapter 补充出站消息能力，使 AI 模型处理后的回复能够通过该插件发送回 YouTube 直播间。

## 背景

- 当前适配器为纯入站，`_send_platform_message` 是空操作
- `mofox_wire.AdapterBase` 已有出站路由机制：`_on_outgoing_from_core` → `_send_platform_message`
- YouTube 发送消息需使用 **YouTube Data API v3**（非 Inner Tube API），需 OAuth2 认证
- YouTube Live Chat 仅支持发送纯文本消息

## 架构设计

```
AI 回复 → MessageEnvelope(outgoing) → CoreSink._on_outgoing_from_core
  → platform 匹配 "live" → YouTubeLiveAdapter._send_platform_message
  → YouTubeLiveChatSender.send_text_message(text)
  → YouTube Data API v3: POST /liveChat/messages
```

## 实施步骤

### Step 1: 新建 `src/sender.py` — YouTube Live Chat 消息发送器

**类**: `YouTubeLiveChatSender`

**职责**:
- OAuth2 token 管理（refresh token 流程）
- 获取 `live_chat_id`（从 `video_id` 解析）
- 发送文本消息到直播间

**公共接口**:
```python
class YouTubeLiveChatSender:
    def __init__(self, *, client_id, client_secret, refresh_token, proxy="", timeout=15.0)
    async def start(self, video_id: str) -> None  # 获取 live_chat_id
    async def send_text_message(self, text: str) -> dict[str, Any]
    async def aclose(self) -> None
```

**OAuth2 流程**:
1. 使用 `client_id` + `client_secret` + `refresh_token` 获取 access_token
2. 缓存 access_token，过期前自动刷新
3. access_token 用于所有 Data API v3 请求

**live_chat_id 获取**:
- 调用 `GET https://www.googleapis.com/youtube/v3/videos?id={video_id}&part=liveStreamingDetails`
- 从响应中提取 `liveStreamingDetails.activeLiveChatId`

**发送消息**:
- `POST https://www.googleapis.com/youtube/v3/liveChat/messages`
- Body: `{"snippet": {"liveChatId": "...", "type": "textMessageEvent", "textMessageDetails": {"messageText": "..."}}}`
- Header: `Authorization: Bearer {access_token}`

**验收标准**:
- [x] OAuth2 token 刷新正常工作
- [x] live_chat_id 自动获取成功
- [x] 文本消息发送成功
- [x] 网络错误时优雅降级（日志警告，不崩溃）
- [x] access_token 过期自动刷新

### Step 2: 修改 `config.py` — 添加 `outbound` 配置分区

**新增**: `OutboundSection`

```python
@config_section("outbound", title="出站消息")
class OutboundSection(SectionBase):
    enabled: bool = Field(
        default=True,
        description="是否启用出站消息（发送消息到直播间），出入站必须同时启用",
    )
    client_id: str = Field(
        default="",
        description="OAuth2 Client ID（必填，从 Google Cloud Console 获取）",
    )
    client_secret: str = Field(
        default="",
        description="OAuth2 Client Secret（必填，从 Google Cloud Console 获取）",
    )
    refresh_token: str = Field(
        default="",
        description="OAuth2 Refresh Token（必填，通过 OAuth2 授权流程获取）",
    )
    live_chat_id: str = Field(
        default="",
        description="Live Chat ID（留空则自动从 video_id 获取）",
    )
```

**验收标准**:
- [x] 配置项完整，默认值合理
- [x] outbound.enabled 默认为 True（出入站绑定）
- [x] 启动时校验：outbound.enabled + OAuth2 凭证缺一不可，否则拒绝启动
- [x] 启动时校验：outbound.enabled = False 时适配器整体不启动

### Step 3: 修改 `plugin.py` — 实现出站消息处理

**修改点**:

1. `on_adapter_loaded`:
   - 如果 `outbound.enabled`，创建 `YouTubeLiveChatSender` 实例
   - 调用 `sender.start(video_id)` 获取 live_chat_id

2. `_send_platform_message(envelope)`:
   - 如果 sender 未初始化，记录警告并返回
   - 从 `envelope["message_segment"]` 提取文本内容
   - 处理 `text` / `seglist` / `command` 等不同 segment 类型
   - 调用 `sender.send_text_message(text)`

3. `on_adapter_unloaded`:
   - 关闭 sender

**message_segment 解析逻辑**（参考 OneBot SendHandler）:
- `{"type": "text", "data": "hello"}` → 直接发送 "hello"
- `{"type": "seglist", "data": [...]}` → 遍历提取所有 text segment，拼接发送
- `{"type": "command", ...}` → 忽略（YouTube 不支持命令）
- `{"type": "image", ...}` → 忽略并记录警告（YouTube 不支持图片）
- 其他类型 → 忽略并记录调试信息

**验收标准**:
- [x] 出站消息正确从 MessageEnvelope 提取文本
- [x] 文本消息成功发送到 YouTube 直播间
- [x] 不支持的 segment 类型优雅跳过
- [x] sender 未初始化时（不应发生，因出入站绑定），记录错误

### Step 4: 修改 `src/__init__.py` — 导出新模块

添加 `YouTubeLiveChatSender` 到 `__all__`

### Step 5: 新建 `tests/test_sender.py` — 发送器测试

**测试用例**:
- `TestInit`: 初始化参数验证
- `TestTokenRefresh`: OAuth2 token 刷新流程
- `TestGetLiveChatId`: live_chat_id 获取（成功/失败）
- `TestSendMessage`: 消息发送（成功/失败/网络错误）
- `TestAclose`: 资源释放

### Step 6: 修改 `tests/conftest.py` — 补充 mock

如需为 sender 测试添加新的 mock 模块

### Step 7: 修改 `tests/test_dispatcher.py` — 补充出站相关测试

- 测试 `_send_platform_message` 的各种 segment 类型处理

### Step 8: 更新 `README.md` — 文档

- 添加出站消息配置说明
- 添加 OAuth2 凭证获取指南
- 更新架构图

## 强制约束：出入站绑定

**出入站消息必须同时配置完整才可启用适配器，缺一不可。**

- 如果 `outbound.enabled = True` 但 OAuth2 凭证（client_id / client_secret / refresh_token）任一为空 → 启动时报错，拒绝启动
- 如果 `outbound.enabled = False` → 适配器整体不启动（包括入站），日志提示需完整配置出站
- 即：适配器只有在入站（video_id）+ 出站（OAuth2 凭证）都配置完整时才运行
- 这确保了 AI 回复一定能发回直播间，避免"只收不回"的半残状态

### 启动校验逻辑

```
on_adapter_loaded:
  1. 检查 plugin.enabled
  2. 检查 youtube.video_id 非空
  3. 检查 outbound.enabled == True
  4. 检查 outbound.client_id / client_secret / refresh_token 非空
  5. 任一不满足 → raise ValueError，适配器不启动
```

## 技术细节

### OAuth2 Token 刷新

```
POST https://oauth2.googleapis.com/token
Content-Type: application/x-www-form-urlencoded

client_id={client_id}&client_secret={client_secret}&refresh_token={refresh_token}&grant_type=refresh_token
```

响应: `{"access_token": "...", "expires_in": 3599, "token_type": "Bearer"}`

### YouTube Data API v3 配额

- 每日配额约 10,000 单位
- `liveChatMessages.insert` 消耗约 50 单位/次
- 理论上每天可发送约 200 条消息
- 需在文档中提醒用户注意配额

### 错误处理策略

| 错误类型 | 处理方式 |
|---------|---------|
| access_token 过期 | 自动刷新，重试一次 |
| live_chat_id 获取失败 | 日志错误，出站功能标记不可用 |
| 发送失败 (HTTP 4xx) | 日志警告，不重试 |
| 发送失败 (HTTP 5xx) | 日志警告，不重试 |
| 网络超时 | 日志警告，不重试 |
| 配额耗尽 (403) | 日志错误，停止发送 |

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/sender.py` | 新建 | YouTube Live Chat 消息发送器 |
| `config.py` | 修改 | 添加 outbound 配置分区 |
| `plugin.py` | 修改 | 实现出站消息处理 |
| `src/__init__.py` | 修改 | 导出新模块 |
| `tests/test_sender.py` | 新建 | 发送器测试 |
| `tests/conftest.py` | 修改 | 补充 mock（如需要） |
| `README.md` | 修改 | 文档更新 |
