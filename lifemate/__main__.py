"""入口：python -m lifemate（剥夺模式）。

-s / --silence：静默终端日志（输入输出照常）。
"""
import os
import sys

from .host import main


def _silence_requested() -> bool:
    """-s/--silence 或 env LIFEMATE_SILENT 任一命中即静默（守护部署走 env）。"""
    if os.environ.get("LIFEMATE_SILENT", "").strip().lower() in ("1", "true", "yes"):
        return True
    return any(a in ("-s", "--silence") for a in sys.argv[1:])


main(silence=_silence_requested())
