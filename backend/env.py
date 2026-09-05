"""讀取 .env.local。

讓 backend/ 的工具在任何一台機器上都能直接跑，
不必先手動 export 一堆環境變數。
"""

from __future__ import annotations

import io
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env.local"


def load(path: Path | None = None) -> int:
    """把 .env.local 的內容載進環境變數。已存在的不覆蓋。"""
    target = path or ENV_FILE
    if not target.exists():
        return 0

    loaded = 0
    for line in io.open(target, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def require(*names: str) -> None:
    """確認需要的變數都有值，缺了就講清楚該怎麼補。"""
    load()
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "缺少設定：" + ", ".join(missing) + "\n"
            f"請確認 {ENV_FILE} 存在且填好（範本見 .env.local.example）"
        )
