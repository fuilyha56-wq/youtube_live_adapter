# YouTube Live Adapter 修复与完善计划

> Created: 2026-06-13 02:38:25

# YouTube Live Adapter 修复与完善计划

## 修复项

### 1. 修复 `src/__init__.py` — 无效 Python 代码
- **问题**：当前内容是纯文本 `YouTube Live Adapter 内部模块`，不是有效 Python 代码，会导致 `import src` 报错
- **修复**：改为标准 Python 包 docstring

### 2. 实现货币转换功能
- **问题**：`config.py` 中有 `enable_currency_conversion` 和 `exchange_rate_api_url` 配置项，但 `dispatcher.py` 中完全没有实现转换逻辑
- **修复**：
  - 在 `src/` 下新建 `currency.py`，实现 `CurrencyConverter` 类
  - 使用 `httpx.AsyncClient` 调用汇率 API 获取汇率
  - 简单缓存机制（TTL 1小时）
  - 在 `dispatcher.py` 的 `_build_super_chat_envelope` 中集成转换
  - 在 `plugin.py` 的 `on_adapter_loaded` 中创建 `CurrencyConverter` 实例并注入 dispatcher

### 3. 补充测试
- **问题**：零测试文件
- **修复**：创建 `tests/` 目录，编写以下测试：
  - `tests/test_dispatcher.py`：消息分发器的单元测试（各种消息类型的解析、去重、过滤）
  - `tests/test_api.py`：API 客户端的单元测试（token 提取、payload 构建）
  - `tests/test_client.py`：轮询客户端的单元测试（自适应间隔、token 过期重获取）
  - `tests/test_currency.py`：货币转换器的单元测试

### 4. 静态类型检查
- **问题**：未运行过 pyright/ruff
- **修复**：
  - 运行 ruff 检查并修复所有问题
  - 运行 pyright 检查并修复类型问题
  - 确保所有文件通过检查

## 自我完善项

### 5. `src/__init__.py` 补充导出
- 添加 `__all__` 导出关键类，方便外部使用

### 6. `__init__.py` 补充 `__plugin_meta__`
- 当前 `__init__.py` 只有 `__version__` 和 `__author__`，缺少 `__plugin_meta__`
- 参考 bilibili_live_adapter 的做法，确认是否需要添加

### 7. dispatcher.py 中 `_apply_user` 的 UserRole 逻辑优化
- 当前 SC 和 member 都用 `OPERATOR`，逻辑冗余：`role = UserRole.OPERATOR if is_sc else (UserRole.OPERATOR if is_member else UserRole.MEMBER)`
- 应改为：SC → OPERATOR, member → OPERATOR, 其他 → MEMBER（简化条件表达式）

### 8. client.py 中 `_running` 初始化问题
- `plugin.py` 第 312 行直接设置 `client._running = True`，破坏了封装
- 应在 `YouTubePollClient` 中提供 `start()` 方法或在 `__init__` 中默认 `_running = True`

### 9. 添加 pyproject.toml / ruff 配置
- 添加 ruff 配置文件，统一代码风格

## 实施顺序

1. 修复 `src/__init__.py`（简单修复）
2. 修复 `client.py` 的 `_running` 封装问题
3. 修复 `dispatcher.py` 的 UserRole 逻辑
4. 新建 `src/currency.py` 实现货币转换
5. 在 `dispatcher.py` 中集成货币转换
6. 在 `plugin.py` 中注入 CurrencyConverter
7. 运行 ruff 修复代码风格
8. 运行 pyright 修复类型问题
9. 编写测试
10. 运行测试确认通过
