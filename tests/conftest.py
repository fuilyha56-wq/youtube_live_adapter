"""YouTube Live Adapter 测试套件的公共 fixtures 和 mock。

由于项目依赖外部框架（mofox_wire、src.kernel 等），
测试中通过 mock 替代这些依赖，确保测试可独立运行。
"""

from __future__ import annotations

import sys
import types
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

# ──────────────────────────────────────────────
# Mock 外部依赖
# ──────────────────────────────────────────────


def _setup_mock_modules() -> None:
    """在 sys.modules 中注册 mock 模块，替代外部框架依赖。"""

    # --- mofox_wire ---
    mofox_wire = types.ModuleType("mofox_wire")
    mofox_wire_types = types.ModuleType("mofox_wire.types")

    # UserRole 枚举
    user_role_enum = type("UserRole", (), {
        "OWNER": "owner",
        "ADMIN": "admin",
        "OPERATOR": "operator",
        "MEMBER": "member",
        "GUEST": "guest",
    })

    mofox_wire_types.UserRole = user_role_enum  # type: ignore[attr-defined]

    # MessageBuilder：链式调用 mock
    class MockMessageBuilder:
        """模拟 MessageBuilder 的链式调用。"""

        def __init__(self) -> None:
            self._data: dict[str, Any] = {}

        def direction(self, v: str) -> MockMessageBuilder:
            self._data["direction"] = v
            return self

        def platform(self, v: str) -> MockMessageBuilder:
            self._data["platform"] = v
            return self

        def text(self, v: str) -> MockMessageBuilder:
            self._data["text"] = v
            return self

        def message_id(self, v: str) -> MockMessageBuilder:
            self._data["message_id"] = v
            return self

        def timestamp_ms(self, v: int) -> MockMessageBuilder:
            self._data["timestamp_ms"] = v
            return self

        def from_user(self, **kwargs: Any) -> MockMessageBuilder:
            self._data["from_user"] = kwargs
            return self

        def from_group(self, **kwargs: Any) -> MockMessageBuilder:
            self._data["from_group"] = kwargs
            return self

        def build(self) -> dict[str, Any]:
            """构建 MessageEnvelope（简化为 dict）。"""
            result: dict[str, Any] = dict(self._data)
            # 模拟 get() 方法
            result["get"] = lambda key, default=None: result.get(key, default)  # type: ignore[assignment]
            # 模拟 message_info 字段
            if "message_info" not in result:
                result["message_info"] = {"extra": {}}
            return result

    mofox_wire.MessageBuilder = MockMessageBuilder  # type: ignore[attr-defined]
    mofox_wire.CoreSink = MagicMock  # type: ignore[attr-defined]
    mofox_wire.MessageEnvelope = dict  # type: ignore[attr-defined]

    sys.modules["mofox_wire"] = mofox_wire
    sys.modules["mofox_wire.types"] = mofox_wire_types

    # --- src.kernel.logger ---
    kernel_logger = types.ModuleType("src.kernel.logger")
    mock_logger = MagicMock()
    kernel_logger.get_logger = lambda name: mock_logger  # type: ignore[attr-defined]

    # 确保 src.kernel 存在
    if "src.kernel" not in sys.modules:
        sys.modules["src.kernel"] = types.ModuleType("src.kernel")
    sys.modules["src.kernel.logger"] = kernel_logger

    # --- src.app.plugin_system.api.log_api ---
    log_api = types.ModuleType("src.app.plugin_system.api.log_api")
    log_api.get_logger = lambda name: mock_logger  # type: ignore[attr-defined]

    for mod_name in [
        "src.app",
        "src.app.plugin_system",
        "src.app.plugin_system.api",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)
    sys.modules["src.app.plugin_system.api.log_api"] = log_api

    # --- src.core.components.base ---
    core_base = types.ModuleType("src.core.components.base")

    class MockBasePlugin:
        plugin_name: str = ""
        plugin_version: str = ""
        plugin_author: str = ""
        plugin_description: str = ""
        configs: ClassVar[list[type]] = []

        def get_components(self) -> list[type]:
            return []

    class MockBaseAdapter:
        adapter_name: str = ""
        adapter_version: str = ""
        adapter_author: str = ""
        adapter_description: str = ""
        platform: str = ""
        source_platform: str = ""
        run_in_subprocess: bool = False

        def __init__(self, core_sink: Any = None, **kwargs: Any) -> None:
            self.core_sink = core_sink
            self.plugin = kwargs.get("plugin")

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    core_base.BasePlugin = MockBasePlugin  # type: ignore[attr-defined]
    core_base.BaseAdapter = MockBaseAdapter  # type: ignore[attr-defined]

    for mod_name in [
        "src.core",
        "src.core.components",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)
    sys.modules["src.core.components.base"] = core_base

    # --- src.core.components.base.config ---
    base_config = types.ModuleType("src.core.components.base.config")

    class MockField:
        """模拟 Field。"""
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class MockSectionBase:
        """模拟 SectionBase。"""
        pass

    class MockBaseConfig:
        """模拟 BaseConfig。"""
        pass

    def mock_config_section(name: str, **kwargs: Any) -> type:
        """模拟 config_section 装饰器。"""
        def decorator(cls: type) -> type:
            return cls
        return decorator

    base_config.Field = MockField  # type: ignore[attr-defined]
    base_config.SectionBase = MockSectionBase  # type: ignore[attr-defined]
    base_config.BaseConfig = MockBaseConfig  # type: ignore[attr-defined]
    base_config.config_section = mock_config_section  # type: ignore[attr-defined]

    sys.modules["src.core.components.base.config"] = base_config

    # --- src.core.components.loader ---
    loader = types.ModuleType("src.core.components.loader")

    def mock_register_plugin(cls: type) -> type:
        return cls

    loader.register_plugin = mock_register_plugin  # type: ignore[attr-defined]
    sys.modules["src.core.components.loader"] = loader

    # --- src.kernel.concurrency ---
    concurrency = types.ModuleType("src.kernel.concurrency")
    mock_tm = MagicMock()
    concurrency.get_task_manager = lambda: mock_tm  # type: ignore[attr-defined]
    sys.modules["src.kernel.concurrency"] = concurrency

    # --- src.app.plugin_system.api.prompt_api ---
    prompt_api_mod = types.ModuleType("src.app.plugin_system.api.prompt_api")
    prompt_api_mod.prompt_api = MagicMock()  # type: ignore[attr-defined]
    sys.modules["src.app.plugin_system.api.prompt_api"] = prompt_api_mod

    # --- src.core.prompt ---
    core_prompt = types.ModuleType("src.core.prompt")

    # SystemReminderBucket 枚举
    class MockSystemReminderBucket:
        ACTOR = "actor"
        SYSTEM = "system"
        CONTEXT = "context"

    # SystemReminderInsertType 枚举
    class MockSystemReminderInsertType:
        DYNAMIC = "dynamic"
        STATIC = "static"

    # get_system_reminder_store 返回的 mock store
    mock_reminder_store = MagicMock()

    core_prompt.SystemReminderBucket = MockSystemReminderBucket  # type: ignore[attr-defined]
    core_prompt.SystemReminderInsertType = MockSystemReminderInsertType  # type: ignore[attr-defined]
    core_prompt.get_system_reminder_store = lambda: mock_reminder_store  # type: ignore[attr-defined]

    if "src.core" not in sys.modules:
        sys.modules["src.core"] = types.ModuleType("src.core")
    sys.modules["src.core.prompt"] = core_prompt


# 在模块加载时设置 mock
_setup_mock_modules()


@pytest.fixture
def mock_logger() -> MagicMock:
    """返回 mock logger 实例。"""
    return MagicMock()
