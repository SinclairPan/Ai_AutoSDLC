"""在 sandbox 已建立后切换为 reviewer disposable 环境。"""

from __future__ import annotations

CHILD_WRAPPER_PROGRAM = r"""
import json
import os
import sys
from pathlib import Path

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
argv = spec["argv"]
child = spec["environment"]
environment = dict(os.environ)
environment.update(child)
os.execvpe(argv[0], argv, environment)
"""

__all__ = ["CHILD_WRAPPER_PROGRAM"]
