# YouTube Live Adapter

YouTube 直播间弹幕 / Super Chat / 会员消息接入适配器——通过 YouTube Inner Tube API 轮询获取直播间消息，并投递至 Neo-MoFox 消息总线。支持通过 YouTube Data API v3 发送 AI 回复到直播间。

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🎙️ 弹幕接收 | 实时接收直播间普通弹幕 |
| 💰 Super Chat | 提取 SC 金额、货币、文本，支持自动转换为 CNY |
| 🎨 Super Sticker | 识别付费贴纸及金额 |
| 🎁 会员礼物 | 支持礼物购买、兑换、公告事件 |
| 🏅 会员里程碑 | 接收会员里程碑消息 |
| 💳 付费消息 | 支持 liveChatPaidMessageRenderer 类型的付费消息 |
| 📤 出站消息 | 通过 YouTube Data API v3 发送 AI 回复到直播间 |
| 🔄 自适应轮询 | 有消息时保持快速轮询，无消息时逐步增大间隔，节省资源 |
| 🔁 自动重连 | 指数退避 + 随机抖动重连策略，避免雪崩 |
| 🌐 代理支持 | HTTP / SOCKS5 代理，中国大陆用户需配置代理 |
| 🧹 消息去重 | 基于滑动窗口的消息 ID 去重，避免重复投递 |
| ✂️ 消息过滤 | 可选过滤 emoji、移除 hashtag、截断超长消息 |
| 👥 观众人数 | 实时获取直播间在线人数，注入 AI 的 system_reminder |

---

## 📋 前置要求

| 项目 | 要求 |
|------|------|
| **Neo-MoFox** | ≥ `1.2.0-alpha` |
| **Python** | ≥ `3.11` |
| **依赖** | `httpx>=0.27.0` |
| **网络** | 需能访问 YouTube（中国大陆需配置代理） |
| **Google 账号** | 用于创建 OAuth2 凭证（出站消息功能必需） |

---

## 📦 安装

### 1. 放置插件文件

将本插件目录放入 Neo-MoFox 的插件目录中，确保目录结构如下：

```
plugins/
└── YouTube-adapter/
    ├── manifest.json
    ├── plugin.py
    ├── config.py
    ├── __init__.py
    ├── pyproject.toml
    ├── src/
    │   ├── __init__.py
    │   ├── api.py
    │   ├── client.py
    │   ├── currency.py
    │   ├── sender.py
    │   └── dispatcher.py
    └── tests/
```

### 2. 安装 Python 依赖

```bash
pip install httpx>=0.27.0
```

> 如果 Neo-MoFox 使用虚拟环境，请在其虚拟环境中执行上述命令。

---

## 🚀 快速开始

### Step 1：获取直播 video_id

打开浏览器，进入 YouTube 直播间，地址栏 URL 格式如下：

```
https://www.youtube.com/watch?v=dQw4w9WgXcQ
                               ─────────────
                               ↑ 这就是 video_id
```

或：

```
https://www.youtube.com/live/dQw4w9WgXcQ
                          ─────────────
                          ↑ 这也是 video_id
```

- URL 中 `v=` 后面的部分就是 `video_id`
- `video_id` 通常为 11 位字母数字组合

### Step 2：配置代理（如需要）

如果你在中国大陆，需要配置代理才能访问 YouTube。跳到 [代理配置说明](#-代理配置说明) 章节选择合适的代理格式。

如果网络可直接访问 YouTube，跳过此步。

### Step 3：获取 OAuth2 凭证

出站消息（AI 回复到直播间）需要 YouTube Data API v3 的 OAuth2 凭证。**本适配器要求出入站同时启用**，因此 OAuth2 凭证是必须的。

详细教程见 👉 [🔑 OAuth2 凭证获取完整教程](#-oauth2-凭证获取完整教程)

### Step 4：编辑配置文件

打开 `config/youtube_live_adapter.toml`，填入你的配置：

```toml
[plugin]
enabled = true
debug_mode = false

[youtube]
video_id = "dQw4w9WgXcQ"                    # ← 替换为你的直播 video_id
language = "zh"
proxy_url = "http://127.0.0.1:7890"          # ← 如需代理，填入代理地址；不需要则留空
client_name = "IOS"

[connection]
poll_interval = 2.5
max_poll_interval = 60.0
auto_reconnect = true
reconnect_initial_delay = 2.0
reconnect_max_delay = 60.0
reconnect_backoff_multiplier = 2.0
request_timeout = 15.0

[filter]
filter_emoji = false
remove_hashtags = false
max_message_length = 500
ignored_message_types = ["messageDeletedEvent", "userBannedEvent"]

[superchat]
enable_currency_conversion = false
exchange_rate_api_url = "https://api.exchangerate-api.com/v4/latest/USD"

[outbound]
enabled = true
client_id = "你的_client_id"                  # ← 从 Google Cloud Console 获取
client_secret = "你的_client_secret"           # ← 从 Google Cloud Console 获取
refresh_token = "你的_refresh_token"           # ← 通过 OAuth2 授权流程获取
live_chat_id = ""                              # ← 留空则自动从 video_id 获取
```

### Step 5：启动插件

启动 Neo-MoFox，插件会自动加载。如果配置正确，日志中应出现：

```
YouTube Live Adapter 加载完成 (video_id=dQw4w9WgXcQ)
轮询客户端启动 (video_id=dQw4w9WgXcQ)
YouTube Live Adapter 已启动
```

### Step 6：验证是否正常工作

- 在直播间发送弹幕，观察 Neo-MoFox 是否收到消息
- 如果开启 `debug_mode`，日志中会输出未处理的消息类型和原始数据
- 如果没有收到消息，参考 [故障排查](#-故障排查) 章节

---

## 🔑 OAuth2 凭证获取完整教程

出站消息功能需要 YouTube Data API v3 的 OAuth2 凭证。本适配器要求出入站同时启用，因此 OAuth2 凭证是**必须**的。

你需要获取三个值：`client_id`、`client_secret`、`refresh_token`。

### 第一步：创建 Google Cloud 项目

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 点击顶部的项目选择器 → **新建项目**
3. 输入项目名称（如 `YouTube Live Bot`），点击 **创建**
4. 等待项目创建完成，确保顶部项目选择器已切换到新项目

> 💡 也可以使用已有项目，但建议创建新项目以便管理。

### 第二步：启用 YouTube Data API v3

1. 在 Google Cloud Console 左侧菜单中，点击 **API 和服务 → 库**
2. 在搜索框中输入 `YouTube Data API v3`
3. 点击搜索结果中的 **YouTube Data API v3**
4. 点击 **启用** 按钮

> ⚠️ 如果不启用此 API，后续的 OAuth2 授权会失败，报 `access_denied` 错误。

### 第三步：配置 OAuth2 同意屏幕

1. 在左侧菜单中，点击 **API 和服务 → OAuth 同意屏幕**
2. 选择用户类型：
   - 如果你有 Google Workspace 账号，可以选择 **内部**（仅组织内可用）
   - 普通用户选择 **外部**
3. 填写应用信息：
   - **应用名称**：随意填写（如 `YouTube Live Bot`）
   - **用户支持电子邮件**：选择你的邮箱
   - **开发者联系信息**：填入你的邮箱
4. 点击 **保存并继续**
5. **作用域**页面：点击 **添加或移除作用域**
   - 搜索 `youtube.force-ssl`
   - 勾选 `https://www.googleapis.com/auth/youtube.force-ssl`
   - 点击 **更新** → **保存并继续**
6. **测试用户**页面（仅外部模式需要）：
   - 点击 **添加用户**
   - 填入你要用于发送消息的 Google 账号邮箱
   - 点击 **添加** → **保存并继续**
7. 确认信息无误，点击 **返回信息中心**

> ⚠️ 在外部模式下，应用处于"测试"状态时只有测试用户可以授权。如果你不想每次授权都受限，可以发布应用（但 Google 会审核）。对于个人使用，保持测试状态 + 添加测试用户即可。

### 第四步：创建 OAuth2 客户端凭证

1. 在左侧菜单中，点击 **API 和服务 → 凭证**
2. 点击顶部的 **创建凭证 → OAuth 客户端 ID**
3. 应用类型选择 **网页应用**
4. 名称随意填写（如 `YouTube Live Bot Client`）
5. **已获授权的重定向 URI**：
   - 点击 **添加 URI**
   - 填入 `http://localhost:8080`
   - （这个 URI 不需要真实存在，只是用来接收授权码）
6. 点击 **创建**
7. 创建成功后会弹出一个窗口，显示 **客户端 ID** 和 **客户端密钥**
   - 👉 这就是你的 `client_id` 和 `client_secret`
   - 也可以在凭证列表中随时查看

### 第五步：获取 Refresh Token

这是最关键的一步。你需要通过 OAuth2 授权流程获取 `refresh_token`。下面提供三种方法，选择最适合你的。

---

#### 方法 A：浏览器手动获取（最简单，推荐新手）

**1. 构造授权 URL**

将以下 URL 中的 `YOUR_CLIENT_ID` 替换为你的 `client_id`，然后在浏览器中打开：

```
https://accounts.google.com/o/oauth2/v2/auth?client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:8080&response_type=code&scope=https://www.googleapis.com/auth/youtube.force-ssl&access_type=offline&prompt=consent
```

**2. 授权**

- 浏览器会跳转到 Google 登录页面
- 使用你要用于发送消息的 Google 账号登录
- 如果出现"此应用未经验证"的警告，点击 **高级** → **前往 [你的应用名]（不安全）**
- 点击 **继续** 授权

**3. 获取授权码**

- 授权后，浏览器会尝试跳转到 `http://localhost:8080?code=4/0AXXXX...&scope=...`
- 由于本地没有运行服务器，页面会显示**无法访问**，这是正常的
- 从浏览器地址栏中复制 `code=` 后面的值（到 `&` 之前），这就是**授权码**
- 授权码格式通常为 `4/0AXXXX...`

**4. 用授权码换取 refresh_token**

使用 `curl`（或任何 HTTP 工具）发送 POST 请求：

```bash
curl -X POST https://oauth2.googleapis.com/token \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "code=AUTHORIZATION_CODE" \
  -d "redirect_uri=http://localhost:8080" \
  -d "grant_type=authorization_code"
```

将 `YOUR_CLIENT_ID`、`YOUR_CLIENT_SECRET`、`AUTHORIZATION_CODE` 替换为实际值。

成功响应示例：

```json
{
  "access_token": "ya29.a0AXXXX...",
  "expires_in": 3599,
  "refresh_token": "1//0gXXXX...",
  "scope": "https://www.googleapis.com/auth/youtube.force-ssl",
  "token_type": "Bearer"
}
```

👉 `refresh_token` 字段的值就是你需要填入配置的 `refresh_token`。

> ⚠️ `refresh_token` 只在首次授权时返回。如果你之前已经授权过同一个应用，再次授权可能不会返回 `refresh_token`。解决方法：在 Google 账号设置中移除该应用的访问权限，然后重新授权。

---

#### 方法 B：使用 Python 脚本获取（推荐开发者）

创建一个 Python 脚本 `get_refresh_token.py`：

```python
"""获取 YouTube OAuth2 Refresh Token 的辅助脚本"""

import http.server
import json
import threading
import urllib.parse
import urllib.request

# ====== 在这里填入你的凭证 ======
CLIENT_ID = "你的_client_id"
CLIENT_SECRET = "你的_client_secret"
REDIRECT_URI = "http://localhost:8080"
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
# =================================

auth_url = (
    f"https://accounts.google.com/o/oauth2/v2/auth?"
    f"client_id={CLIENT_ID}&"
    f"redirect_uri={urllib.parse.quote(REDIRECT_URI)}&"
    f"response_type=code&"
    f"scope={urllib.parse.quote(SCOPE)}&"
    f"access_type=offline&"
    f"prompt=consent"
)

print("=" * 60)
print("请在浏览器中打开以下 URL 进行授权：")
print(auth_url)
print("=" * 60)

# 等待授权码
auth_code = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """处理 OAuth2 回调的 HTTP Handler"""

    def do_GET(self):
        global auth_code
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ 授权成功！你可以关闭此页面了。".encode("utf-8"))
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            error = params.get("error", ["unknown"])[0]
            self.wfile.write(f"❌ 授权失败: {error}".encode("utf-8"))

    def log_message(self, format, *args):
        pass  # 静默日志


# 启动本地服务器等待回调
server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
thread = threading.Thread(target=server.handle_request, daemon=True)
thread.start()

print("等待授权回调中...")
thread.join(timeout=300)
server.server_close()

if not auth_code:
    print("❌ 未收到授权码，请重试")
    exit(1)

print(f"✅ 收到授权码: {auth_code[:20]}...")

# 用授权码换取 refresh_token
token_url = "https://oauth2.googleapis.com/token"
data = urllib.parse.urlencode({
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": auth_code,
    "redirect_uri": REDIRECT_URI,
    "grant_type": "authorization_code",
}).encode()

req = urllib.request.Request(token_url, data=data)
try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode())
        print("\n" + "=" * 60)
        print("🎉 Refresh Token 获取成功！")
        print("=" * 60)
        print(f"refresh_token: {result['refresh_token']}")
        print(f"access_token:  {result['access_token'][:30]}...")
        print(f"expires_in:    {result['expires_in']}s")
        print("=" * 60)
        print("\n请将以下值填入配置文件：")
        print(f"  outbound.client_id     = {CLIENT_ID}")
        print(f"  outbound.client_secret = {CLIENT_SECRET}")
        print(f"  outbound.refresh_token = {result['refresh_token']}")
except urllib.error.HTTPError as e:
    error_body = json.loads(e.read().decode())
    print(f"❌ 获取 token 失败: {error_body}")
```

使用方法：

```bash
# 1. 填入你的 CLIENT_ID 和 CLIENT_SECRET
# 2. 运行脚本
python get_refresh_token.py
# 3. 在浏览器中打开脚本输出的 URL
# 4. 授权后，脚本会自动接收回调并输出 refresh_token
```

> 💡 此脚本使用 Python 标准库，无需安装额外依赖。它会启动一个本地 HTTP 服务器自动接收授权回调，比手动复制授权码更方便。

---

#### 方法 C：使用 curl 一键获取（Linux / macOS 用户）

如果你熟悉命令行，可以直接用 curl 完成整个流程：

```bash
# 1. 设置变量
CLIENT_ID="你的_client_id"
CLIENT_SECRET="你的_client_secret"
REDIRECT_URI="http://localhost:8080"

# 2. 构造授权 URL 并在浏览器中打开
echo "请在浏览器中打开以下 URL："
echo "https://accounts.google.com/o/oauth2/v2/auth?client_id=${CLIENT_ID}&redirect_uri=${REDIRECT_URI}&response_type=code&scope=https://www.googleapis.com/auth/youtube.force-ssl&access_type=offline&prompt=consent"

# 3. 授权后，从浏览器地址栏复制 code= 后面的值
read -p "请输入授权码 (code): " AUTH_CODE

# 4. 换取 refresh_token
curl -s -X POST https://oauth2.googleapis.com/token \
  -d "client_id=${CLIENT_ID}" \
  -d "client_secret=${CLIENT_SECRET}" \
  -d "code=${AUTH_CODE}" \
  -d "redirect_uri=${REDIRECT_URI}" \
  -d "grant_type=authorization_code" | python3 -m json.tool
```

---

### 第六步：填入配置

将获取到的三个值填入 `config/youtube_live_adapter.toml`：

```toml
[outbound]
enabled = true
client_id = "xxxxxxxxxxxx.apps.googleusercontent.com"
client_secret = "GOCSPX-xxxxxxxxxxxxxxxxx"
refresh_token = "1//0gxxxxxxxxxxxxxxxxx"
live_chat_id = ""   # 留空则自动从 video_id 获取
```

### OAuth2 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `access_denied` | 未启用 YouTube Data API v3 | 回到第二步，确保 API 已启用 |
| `redirect_uri_mismatch` | 重定向 URI 不匹配 | 确保凭证中的重定向 URI 为 `http://localhost:8080` |
| 响应中没有 `refresh_token` | 之前已授权过，Google 不再返回 | 在 [Google 账号安全设置](https://myaccount.google.com/permissions) 中移除该应用，重新授权 |
| `invalid_grant` | 授权码已过期或已使用 | 授权码只能使用一次，且有效期约 10 分钟，请重新获取 |
| "此应用未经验证" 警告 | 应用处于测试模式 | 点击 **高级** → **前往 [应用名]（不安全）** 即可 |
| HTTP 403 发送消息被拒绝 | 配额耗尽或权限不足 | 检查 API 配额，确保授权的账号有发送消息的权限 |

---

## ⚙️ 配置详解

配置文件路径：`config/youtube_live_adapter.toml`

### `[plugin]` — 插件开关与调试

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用 YouTube Live Adapter。设为 `false` 则跳过启动。 |
| `debug_mode` | `bool` | `false` | 调试模式。开启后会输出未处理的消息类型和原始数据，用于排查问题。 |

```toml
[plugin]
enabled = true
debug_mode = false
```

### `[youtube]` — YouTube 连接配置

| 字段 | 类型 | 默认值 | 取值范围 | 说明 |
|------|------|--------|----------|------|
| `video_id` | `str` | `""` | — | **必填**。直播视频 ID，从 YouTube 直播间 URL 中提取。 |
| `language` | `str` | `"zh"` | YouTube 语言代码 | 界面语言代码，影响 API 返回的部分本地化字段。 |
| `proxy_url` | `str` | `""` | — | HTTP 代理地址，留空则直连。格式：`http://host:port` 或 `socks5://host:port`。 |
| `client_name` | `str` | `"IOS"` | `WEB` / `IOS` / `TV` | Inner Tube API 客户端标识，不同客户端的风控等级不同（详见 [client_name 选择指南](#-client_name-选择指南)）。 |

```toml
[youtube]
video_id = "dQw4w9WgXcQ"
language = "zh"
proxy_url = "http://127.0.0.1:7890"
client_name = "IOS"
```

### `[connection]` — 轮询与重连参数

| 字段 | 类型 | 默认值 | 取值范围 | 说明 |
|------|------|--------|----------|------|
| `poll_interval` | `float` | `2.5` | `1.0` ~ `10.0` | 基础轮询间隔（秒）。有新消息时保持此间隔。 |
| `max_poll_interval` | `float` | `60.0` | `10.0` ~ `120.0` | 最大轮询间隔（秒）。无消息时逐步增大至此上限。 |
| `auto_reconnect` | `bool` | `true` | — | 连接断开后是否自动重连。 |
| `reconnect_initial_delay` | `float` | `2.0` | `1.0` ~ `30.0` | 重连初始延迟（秒）。首次重连等待此时间。 |
| `reconnect_max_delay` | `float` | `60.0` | `10.0` ~ `300.0` | 重连最大延迟（秒）。退避延迟不会超过此值。 |
| `reconnect_backoff_multiplier` | `float` | `2.0` | `1.5` ~ `5.0` | 重连退避倍数。每次失败延迟乘以此值。 |
| `request_timeout` | `float` | `15.0` | `5.0` ~ `60.0` | HTTP 请求超时（秒）。 |

**轮询机制说明：**

- 有消息时：保持 `poll_interval` 间隔快速轮询
- 无消息时：间隔逐步增大（每次 ×1.2），最大不超过 `max_poll_interval`
- 这样既保证了消息的实时性，又避免了空闲时频繁请求浪费资源

**重连策略说明：**

采用指数退避 + 随机抖动策略：

```
delay = min(initial_delay × multiplier^attempts, max_delay)
actual_delay = delay × (0.75 + random() × 0.5)
```

例如 `initial_delay=2.0, multiplier=2.0, max_delay=60.0` 时：

| 重连次数 | 基础延迟 | 实际延迟（含抖动） |
|----------|----------|---------------------|
| 第 1 次 | 2.0s | 1.5s ~ 2.5s |
| 第 2 次 | 4.0s | 3.0s ~ 5.0s |
| 第 3 次 | 8.0s | 6.0s ~ 10.0s |
| 第 4 次 | 16.0s | 12.0s ~ 20.0s |
| 第 5 次 | 32.0s | 24.0s ~ 40.0s |
| 第 6 次+ | 60.0s | 45.0s ~ 75.0s |

```toml
[connection]
poll_interval = 2.5
max_poll_interval = 60.0
auto_reconnect = true
reconnect_initial_delay = 2.0
reconnect_max_delay = 60.0
reconnect_backoff_multiplier = 2.0
request_timeout = 15.0
```

### `[filter]` — 消息过滤规则

| 字段 | 类型 | 默认值 | 取值范围 | 说明 |
|------|------|--------|----------|------|
| `filter_emoji` | `bool` | `false` | — | 是否过滤消息中的 emoji 字符。 |
| `remove_hashtags` | `bool` | `false` | — | 是否移除消息中的 `#` 标签。 |
| `max_message_length` | `int` | `500` | `50` ~ `5000` | 消息最大长度，超过则截断并追加 `...`。 |
| `ignored_message_types` | `list[str]` | `["messageDeletedEvent", "userBannedEvent"]` | — | 忽略的消息类型列表。 |

```toml
[filter]
filter_emoji = false
remove_hashtags = true
max_message_length = 500
ignored_message_types = ["messageDeletedEvent", "userBannedEvent"]
```

### `[superchat]` — Super Chat 货币转换

| 字段 | 类型 | 默认值 | 取值范围 | 说明 |
|------|------|--------|----------|------|
| `enable_currency_conversion` | `bool` | `false` | — | 是否启用 SC 金额货币转换（转为 CNY）。 |
| `exchange_rate_api_url` | `str` | `"https://api.exchangerate-api.com/v4/latest/USD"` | — | 汇率 API 地址，启用货币转换时使用。 |

```toml
[superchat]
enable_currency_conversion = true
exchange_rate_api_url = "https://api.exchangerate-api.com/v4/latest/USD"
```

> 详见 [SC 货币转换](#-sc-货币转换) 章节。

### `[outbound]` — 出站消息配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用出站消息。**必须为 `true`**，因为适配器要求出入站同时启用。 |
| `client_id` | `str` | `""` | OAuth2 Client ID（必填，从 Google Cloud Console 获取） |
| `client_secret` | `str` | `""` | OAuth2 Client Secret（必填，从 Google Cloud Console 获取） |
| `refresh_token` | `str` | `""` | OAuth2 Refresh Token（必填，通过 OAuth2 授权流程获取） |
| `live_chat_id` | `str` | `""` | Live Chat ID（留空则自动从 video_id 获取） |

```toml
[outbound]
enabled = true
client_id = "xxxxxxxxxxxx.apps.googleusercontent.com"
client_secret = "GOCSPX-xxxxxxxxxxxxxxxxx"
refresh_token = "1//0gxxxxxxxxxxxxxxxxx"
live_chat_id = ""
```

> ⚠️ **出入站绑定约束**：适配器必须在入站（`video_id`）+ 出站（OAuth2 凭证）都配置完整时才运行。`outbound.enabled = False` 时适配器整体不启动；`outbound.enabled = True` 但 OAuth2 凭证任一为空时，启动报错。

---

## 📨 支持的消息类型

### 入站消息（YouTube → Neo-MoFox）

| 消息类型 | YouTube Renderer | 说明 | 输出格式示例 |
|----------|------------------|------|-------------|
| 普通弹幕 | `liveChatTextMessageRenderer` | 观众发送的普通文字消息 | `你好！` |
| Super Chat | `liveChatSuperChatRenderer` | 付费醒目留言，含金额和货币 | `[SC ¥100] 消息内容` |
| Super Sticker | `liveChatSuperStickerRenderer` | 付费贴纸 | `[Super Sticker ¥50] Sticker名称` |
| 付费消息 | `liveChatPaidMessageRenderer` | 另一种付费消息形式 | `[付费消息 ¥100] 消息内容` |
| 会员礼物购买 | `liveChatMembershipGiftPurchaseRenderer` | 观众购买会员礼物赠送 | `[会员礼物] 用户名 购买了会员礼物` |
| 会员礼物兑换 | `liveChatSponsorshipsGiftRedemptionRenderer` | 观众兑换收到的会员礼物 | `[会员兑换] 用户名 兑换了会员礼物` |
| 会员礼物公告 | `liveChatSponsorshipsGiftPurchaseAnnouncementRenderer` | 会员礼物购买的系统公告 | `[会员礼物] 用户名 购买了会员礼物` |
| 会员里程碑 | `liveChatMemberMilestoneChatRenderer` | 会员连续订阅里程碑 | `[会员里程碑] 消息内容` |
| 付费贴纸 | `liveChatPaidStickerRenderer` | 另一种付费贴纸形式 | `[Paid Sticker ¥50]` |

> 启用 `debug_mode` 后，无法识别的消息类型会在日志中输出，方便反馈和适配。

### 出站消息（Neo-MoFox → YouTube）

| message_segment 类型 | 处理方式 |
|---------------------|---------|
| `text` | 直接发送文本内容 |
| `seglist` | 遍历提取所有 text segment，拼接后发送 |
| `image` | 忽略（YouTube 不支持图片消息） |
| `command` | 忽略（YouTube 不支持命令） |

### 用户角色映射

| YouTube 身份 | Neo-MoFox UserRole | 说明 |
|-------------|-------------------|------|
| 普通观众 | `MEMBER` | 普通弹幕发送者 |
| Super Chat 用户 | `OPERATOR` | 付费用户，拥有更高优先级 |
| 会员 | `OPERATOR` | 频道会员 |

---

## 🖥️ client_name 选择指南

适配器通过 YouTube Inner Tube API 获取消息，不同的 `client_name` 对应不同的客户端标识。YouTube 对不同客户端的风控策略不同。

| client_name | 客户端标识 | User-Agent | 特点 |
|-------------|-----------|------------|------|
| **`IOS`** ⭐ 推荐 | `IOS` | iOS YouTube App | 通常不需要 PO Token，风控较宽松，**默认推荐** |
| `TV` | `TVHTML5_SIMPLY` | PlayStation | 通常不需要 PO Token，作为备选 |
| `WEB` | `WEB` | Chrome 浏览器 | 可能需要 PO Token，风控较严格 |

**推荐选择：`IOS`**

- `IOS` 客户端在大多数情况下可以正常获取 continuation token 和消息
- 如果 `IOS` 失败，适配器会自动按 `IOS → TV → WEB` 的顺序 fallback
- 只有在 `IOS` 和 `TV` 都失败时才建议尝试 `WEB`

---

## 🌐 代理配置说明

中国大陆用户需要配置代理才能访问 YouTube。在 `[youtube]` 的 `proxy_url` 字段中填入代理地址。

### HTTP 代理

```toml
[youtube]
proxy_url = "http://127.0.0.1:7890"
```

### SOCKS5 代理

```toml
[youtube]
proxy_url = "socks5://127.0.0.1:1080"
```

### 常见代理软件配置示例

| 代理软件 | 默认 HTTP 端口 | 默认 SOCKS5 端口 | 配置示例 |
|----------|---------------|-----------------|----------|
| Clash | 7890 | 7891 | `http://127.0.0.1:7890` |
| V2RayN | 10809 | 10808 | `http://127.0.0.1:10809` |
| Shadowsocks | — | 1080 | `socks5://127.0.0.1:1080` |

> **注意：** 请确保代理软件已开启"允许局域网连接"（Allow LAN）选项，且代理服务正在运行。

---

## 💱 SC 货币转换

YouTube Super Chat 支持多种货币，适配器可将外币金额自动转换为 CNY（人民币）。

### 启用方式

```toml
[superchat]
enable_currency_conversion = true
```

启用后，SC 消息的显示格式会从：

```
[SC ¥100] 消息内容
```

变为（当原始货币非 CNY 时）：

```
[SC $15 ≈ ¥108 CNY] 消息内容
```

### 汇率 API 说明

默认使用 [ExchangeRate-API](https://www.exchangerate-api.com/) 的免费接口：

```
https://api.exchangerate-api.com/v4/latest/USD
```

- 以 USD 为基准货币
- 返回格式：`{"base": "USD", "rates": {"CNY": 7.25, "JPY": 155.0, ...}}`
- 汇率缓存 1 小时，自动刷新
- 转换失败时不影响主流程，仅回退到原始金额显示

### 自定义汇率 API

如果你有自己的汇率 API，可以修改 `exchange_rate_api_url`：

```toml
[superchat]
enable_currency_conversion = true
exchange_rate_api_url = "https://your-api.example.com/rates"
```

**自定义 API 需满足以下返回格式：**

```json
{
  "base": "USD",
  "rates": {
    "CNY": 7.25,
    "JPY": 155.0,
    "TWD": 32.0
  }
}
```

- `base`：基准货币代码
- `rates`：各货币对基准货币的汇率（基准货币自身汇率为 1.0）
- 转换公式：`CNY金额 = 原始金额 × (rates["CNY"] / rates[原始货币])`

---

## 🔬 技术说明

### Inner Tube API 原理

本适配器使用 YouTube 的 **Inner Tube API**（非公开内部 API）获取直播间消息，而非官方 YouTube Data API。

**优势：**
- 无需 API Key，无配额限制
- 直接获取 live chat 数据，延迟低

**风险：**
- Inner Tube API 为非公开接口，YouTube 可能随时变更请求格式
- 建议定期使用 Chrome DevTools 抓取最新请求格式并更新客户端配置

**工作流程：**

```
1. 获取初始 continuation token
   ├── 策略 1：通过 Inner Tube API 请求（多客户端 fallback：IOS → TV → WEB）
   └── 策略 2：抓取视频页面 HTML 解析 ytInitialData

2. 使用 continuation token 轮询消息
   └── POST https://www.youtube.com/youtubei/v1/live_chat/get_live_chat

3. 解析响应中的 actions → 分发为 MessageEnvelope
```

### 轮询机制

- **自适应间隔**：有消息时保持 `poll_interval`，无消息时每次 ×1.2 增大，最大不超过 `max_poll_interval`
- **Token 自动刷新**：当 continuation token 过期时，自动重新获取，无需手动干预

### 重连策略

采用 **指数退避 + 随机抖动** 策略：

```
delay = min(initial_delay × multiplier^attempts, max_delay)
actual_delay = delay × (0.75 + random() × 0.5)
```

- 随机抖动范围 ±25%，避免多个实例同时重连造成雪崩
- 重连成功后延迟计数器重置

### 消息去重机制

- 基于消息 ID（`renderer.id`）的滑动窗口去重
- 窗口容量 200 条，采用 `collections.deque` 实现
- 重复消息直接丢弃，不投递至消息总线

### 出站消息架构

```
AI 回复 → MessageEnvelope(outgoing) → CoreSink._on_outgoing_from_core
  → platform 匹配 "live" → YouTubeLiveAdapter._send_platform_message
  → YouTubeLiveChatSender.send_text_message(text)
  → YouTube Data API v3: POST /liveChat/messages
```

### 观众人数推送

适配器会定期获取直播间在线人数，并通过 Neo-MoFox 的 `system_reminder` 机制注入到 AI 的 Prompt 中：

- 刷新间隔：5 秒
- 仅当人数变化时更新，避免无意义写入
- 内容格式：`YouTube 直播间在线人数: 1234`

---

## 🔗 完整方案：从弹幕到 AI 回复

本适配器只负责**消息输入**——把 YouTube 直播间的弹幕接进来。要实现完整的「看直播 → AI 回复」链路，需要配合其他组件。

### 消息流转全貌

```
┌─────────────────────────────────────────────────────────────────┐
│                        消息输入层                                │
│                                                                 │
│  YouTube Live Adapter ──→ 消息总线 ←── ASR Adapter（独立插件）   │
│  （弹幕 / SC / 会员）              （主播语音 → 文字）            │
└──────────────┬──────────────────────────────────────────────────┘
               │ 自动路由（基于 platform + group_id 生成 stream_id）
               ▼
┌─────────────────────────────────────────────────────────────────┐
│                        AI 处理层                                 │
│                                                                 │
│  Chatter（default_chatter / neo_fatum_chatter）                 │
│  接收消息 → 构建 Prompt → 调用 LLM → 生成回复                   │
└──────────────┬──────────────────────────────────────────────────┘
               │ Action 输出
               ▼
┌─────────────────────────────────────────────────────────────────┐
│                        输出层                                    │
│                                                                 │
│  send_text ──→ 文字回复（发回 YouTube 直播间）                   │
│  send_to   ──→ 跨平台转发（如转发到 QQ）                        │
│  Live2D    ──→ 表情 / 动作驱动                                   │
│  TTS       ──→ 语音合成输出                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 你需要安装什么

| 组件 | 插件 | 说明 | 是否必须 |
|------|------|------|----------|
| **弹幕输入** | `YouTube-adapter`（本插件） | 接收 YouTube 直播间弹幕 / SC | ✅ 必须 |
| **AI 回复** | `default_chatter` 或 `neo_fatum_chatter` | 处理消息并生成 AI 回复 | ✅ 必须 |
| **文字输出** | `default_chatter`（自带 `send_text` action） | 将 AI 回复发送出去 | ✅ 必须 |
| **跨平台转发** | `send_to` | 将消息转发到其他平台（如 QQ） | ⬜ 可选 |
| **ASR 语音识别** | 独立 ASR 插件 | 将主播语音转为文字，投递到消息总线 | ⬜ 可选 |
| **Live2D** | 独立 Live2D 插件 | AI 回复驱动 Live2D 表情 / 动作 | ⬜ 可选 |
| **TTS 语音合成** | 独立 TTS 插件 | AI 回复转为语音输出 | ⬜ 可选 |

### 自动接入说明

本适配器投递的消息会自动被 Neo-MoFox 消息总线路由到 Chatter，**无需额外配置**。原理：

1. 本适配器将消息投递到 `CoreSink`，附带 `platform="live"` + `group_id="live_room"`
2. `MessageReceiver` 根据这两个字段自动生成 `stream_id`（SHA-256 哈希）
3. `StreamLoopManager` 为该 stream 创建对话循环，驱动 Chatter 处理消息
4. Chatter 生成回复后，通过 Action（如 `send_text`）输出

**只要安装了 Chatter 插件，弹幕进来就会自动触发 AI 回复。**

### ASR 接入

ASR（语音识别）是独立插件，负责将主播的语音转为文字并投递到消息总线。接入后，AI 不仅能「看到」弹幕，还能「听懂」主播在说什么。

ASR 插件通常需要：
- 音频流来源（如 YouTube 直播音频流）
- ASR 引擎（如 Whisper、FunASR 等）
- 将识别结果以 `MessageEnvelope` 格式投递到消息总线

具体配置请参考对应 ASR 插件的文档。

---

## 🔧 故障排查

### 连接不上 / 获取不到 token

**症状：** 日志中出现 `无法获取 video_id=xxx 的 continuation token`

**可能原因及解决方案：**

1. **网络不通** — 检查是否能正常访问 YouTube
   - 浏览器打开 `https://www.youtube.com` 确认网络连通
   - 如在中国大陆，确认代理已正确配置且正在运行

2. **video_id 错误** — 确认 video_id 是否正确
   - 检查 URL 中 `v=` 后的值是否完整复制
   - 确认该视频确实是直播（或首播），普通视频没有 live chat

3. **直播已结束** — 直播结束后无法获取 continuation token
   - 确认直播正在进行中

4. **代理配置问题** — 参考 [代理配置说明](#-代理配置说明)

### 消息收不到

**症状：** 连接成功但收不到弹幕

**可能原因及解决方案：**

1. **直播未开始** — 直播尚未开始时没有消息
2. **直播间聊天被禁用** — 部分直播间的聊天功能被关闭
3. **消息被过滤** — 检查 `[filter]` 配置
   - `ignored_message_types` 是否过滤了过多类型
   - `max_message_length` 是否过小
4. **轮询间隔过大** — 尝试减小 `poll_interval`

### Token 频繁过期

**症状：** 日志中频繁出现 `Continuation token 已失效，重新获取...`

**可能原因及解决方案：**

1. **client_name 不合适** — 尝试更换 `client_name`
   - 推荐使用 `IOS`，如仍频繁过期可尝试 `TV`
2. **YouTube 风控** — YouTube 可能对频繁请求进行限制
   - 适当增大 `poll_interval`（如 3.0 ~ 5.0）
   - 确保代理 IP 没有被 YouTube 标记

### 代理配置问题

**症状：** 配置代理后仍无法连接

**排查步骤：**

1. 确认代理软件正在运行
2. 确认代理端口与配置一致
3. 确认代理软件已开启"允许局域网连接"
4. 尝试在浏览器中通过代理访问 YouTube
5. 检查代理格式是否正确：
   - HTTP 代理：`http://host:port`
   - SOCKS5 代理：`socks5://host:port`
   - 不要遗漏协议前缀 `http://` 或 `socks5://`

### 出站消息发送失败

**症状：** AI 生成了回复但直播间看不到

**可能原因及解决方案：**

1. **OAuth2 凭证无效** — 检查 `client_id`、`client_secret`、`refresh_token` 是否正确
2. **API 配额耗尽** — YouTube Data API v3 每日配额约 10,000 单位
   - `liveChatMessages.insert` 消耗约 50 单位/次
   - 理论上每天可发送约 200 条消息
   - 在 [Google Cloud Console API 信息中心](https://console.cloud.google.com/apis/api/youtube.googleapis.com) 查看配额使用情况
3. **权限不足** — 确保授权的 Google 账号有发送消息的权限
   - 账号需要是直播间的版主或所有者
4. **live_chat_id 错误** — 如果手动填入了 `live_chat_id`，确认其正确性
   - 建议留空，让适配器自动从 `video_id` 获取
5. **refresh_token 过期** — Google 可能撤销长期未使用的 refresh_token
   - 重新执行 OAuth2 授权流程获取新的 refresh_token

---

## 📁 项目结构

```
YouTube-adapter/
├── manifest.json          # 插件清单（名称、版本、依赖、入口点）
├── plugin.py              # 插件入口（注册 + 适配器生命周期管理）
├── config.py              # 配置定义（6 个 section，所有字段声明）
├── __init__.py            # 包初始化
├── pyproject.toml         # 项目元数据与工具配置
├── src/
│   ├── __init__.py        # src 包初始化
│   ├── api.py             # Inner Tube API 客户端（多客户端 fallback、代理、token 获取）
│   ├── client.py          # HTTP 轮询客户端（自适应间隔、token 刷新）
│   ├── currency.py        # 货币转换器（汇率 API、TTL 缓存、CNY 转换）
│   ├── sender.py          # 出站消息发送器（OAuth2 认证、YouTube Data API v3）
│   └── dispatcher.py      # 消息分发器（9 种 renderer → MessageEnvelope、去重、过滤）
└── tests/                 # 单元测试
    ├── conftest.py
    ├── test_api.py
    ├── test_client.py
    ├── test_currency.py
    └── test_dispatcher.py
```

---

## ⚠️ 注意事项

- 本适配器使用 YouTube 非公开 Inner Tube API（入站），该 API 可能随时变更
- 出站消息使用 YouTube Data API v3（官方 API），需 OAuth2 认证
- **出入站绑定**：适配器必须在入站（`video_id`）+ 出站（OAuth2 凭证）都配置完整时才运行，不支持"只收不回"
- `video_id` 必须是正在直播（或首播）的视频，普通视频没有 live chat
- 直播结束后适配器会自动尝试重连，直到 `auto_reconnect` 关闭或手动停止
- YouTube Data API v3 每日配额约 10,000 单位，`liveChatMessages.insert` 消耗约 50 单位/次，理论上每天可发送约 200 条消息
- `refresh_token` 只在首次授权时返回，如需重新获取请先在 Google 账号设置中移除应用授权

---

## ❓ 常见问题

### 可以只接收弹幕不发送回复吗？

不可以。本适配器要求出入站同时启用。如果你只需要接收弹幕，仍需配置 OAuth2 凭证，但 AI 可以选择不回复。

### 可以同时监控多个直播间吗？

当前版本不支持。每个适配器实例只能监控一个直播间（一个 `video_id`）。如需监控多个直播间，需要部署多个 Neo-MoFox 实例。

### 直播结束后适配器会怎样？

适配器会检测到连接断开，并根据 `auto_reconnect` 配置决定是否自动重连。如果开启了自动重连，适配器会持续尝试重连，直到直播重新开始或手动停止。

### 为什么选择 Inner Tube API 而不是官方 Data API 接收消息？

Inner Tube API 无需 API Key、无配额限制，且延迟更低。官方 Data API 的 `liveChatMessages.list` 接口需要 API Key 且有配额限制，不适合高频轮询。

### 代理需要同时用于入站和出站吗？

是的。`proxy_url` 配置同时应用于入站（Inner Tube API）和出站（Data API v3）的 HTTP 请求。如果你在中国大陆，两个方向都需要代理。

### 如何查看 API 配额使用情况？

访问 [Google Cloud Console API 信息中心](https://console.cloud.google.com/apis/api/youtube.googleapis.com)，选择你的项目即可查看配额使用情况。
