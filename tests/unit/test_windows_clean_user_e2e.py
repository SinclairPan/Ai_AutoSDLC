"""Windows 普通用户 E2E 驱动的纯文本断言测试。"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_driver_module(monkeypatch):
    fake_winpty = types.ModuleType("winpty")
    fake_winpty.PtyProcess = object
    fake_enums = types.ModuleType("winpty.enums")
    fake_enums.Backend = types.SimpleNamespace(ConPTY=0)
    monkeypatch.setitem(sys.modules, "winpty", fake_winpty)
    monkeypatch.setitem(sys.modules, "winpty.enums", fake_enums)

    driver_path = Path(__file__).resolve().parents[2] / "scripts" / "windows_clean_user_e2e.py"
    spec = importlib.util.spec_from_file_location("windows_clean_user_e2e", driver_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_assert_contains_treats_console_line_wrap_as_whitespace(monkeypatch) -> None:
    driver = _load_driver_module(monkeypatch)

    driver._assert_contains(
        "recommended_theme_choice: definePreset(Aura) + #1770e6 +\n"
        "darkModeSelector=false",
        "definePreset(Aura) + #1770e6 + darkModeSelector=false",
    )
