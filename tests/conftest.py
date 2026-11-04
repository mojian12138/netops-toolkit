"""pytest 配置：默认使用离屏后端，CI / 无显示器环境也能跑 GUI 冒烟测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
