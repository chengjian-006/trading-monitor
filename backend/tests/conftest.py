"""pytest conftest: 注入项目根到 sys.path, 让 `from backend.xxx import` 能跑。"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
