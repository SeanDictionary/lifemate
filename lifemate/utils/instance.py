"""单实例锁：OS 级文件锁，进程死亡自动释放（无需清锁）。

双开进程会同时写 memory.db 与 sessions/，静默损坏——所以入口必须持锁。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO


def acquire(data_dir: str | Path) -> IO:
    """获取 {data_dir}/lifemate.lock 的独占锁；失败即退出进程。"""
    path = Path(data_dir) / "lifemate.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        f.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        sys.exit(f"另一个实例正在运行（锁文件：{path}；持锁进程见文件内容）")
    try:
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()}\n")
        f.flush()
    except OSError:
        pass
    return f


def release(f: IO | None) -> None:
    if f is None:
        return
    try:
        f.close()  # 关闭即释放（进程死亡时 OS 自动回收）
    except OSError:
        pass
