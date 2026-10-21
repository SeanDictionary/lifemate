"""全局系统日志：项目级 .log 存储（独立于 observe 心智档案）。

- 文件：{data_dir}/logs/lifemate.log，RotatingFileHandler（大小/份数可配）
- setup_logging() 由 host 启动时调用一次；各模块 logging.getLogger 即可
- 未捕获异常兜底：sys.excepthook + asyncio loop 异常处理器——
  7×24 守护进程化后，任何漏网异常都进日志文件而不是消失在控制台
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

_FORMAT = "%(asctime)s - %(name)s [%(levelname)s]: %(message)s"


def setup_logging(data_dir: str | Path, *, max_bytes: int = 10 * 1024 * 1024,
                  backup_count: int = 5,
                  level: int = logging.INFO,
                  console: bool = True) -> Path:
    """配置根日志（文件轮转 + 控制台；console=False 仅文件——
    -s/--silence 静默终端用，不影响文件日志）。幂等：重复调用不叠加 handler。"""
    logs_dir = Path(data_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "lifemate.log"

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_lifemate", False):
            root.removeHandler(h)
    fmt = logging.Formatter(_FORMAT)
    fh = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    handlers: list[logging.Handler] = [fh]
    if console:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
        h._lifemate = True  # type: ignore[attr-defined]
        root.addHandler(h)
    root.setLevel(level)
    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def install_excepthooks() -> None:
    """主线程未捕获异常 → 日志文件（然后走默认行为退出）。"""
    crash = logging.getLogger("lifemate.crash")

    def _hook(tp: type, val: BaseException, tb: Any) -> None:
        crash.critical("未捕获异常（进程将退出）", exc_info=(tp, val, tb))
        sys.__excepthook__(tp, val, tb)
    sys.excepthook = _hook


def install_loop_handler(loop: Any) -> None:
    """asyncio 未捕获任务异常 → 日志文件（不退出）。"""
    crash = logging.getLogger("lifemate.crash")

    def _handler(loop: Any, context: dict) -> None:
        exc = context.get("exception")
        crash.error("asyncio 未捕获：%s %s", context.get("message"),
                    exc, exc_info=exc is not None and exc or None)
    loop.set_exception_handler(_handler)
