"""开发控制台：stdin 读取 + /命令 + 调试信封转发（宿主的对话面，非器官）。

为什么不器官化：命令面（/quit /reload）要操纵宿主进程的生命周期与配置，
器官契约里没有这条特权通道；且剥夺模式（零器官）下它是唯一输入通道，
必须随宿主常驻。感知部分（普通文字→信封 notify）与器官同构。

- start()：起 stdin 守护线程（阻塞读隔离在线程里，不堵事件循环）
- run(stop)：消费输入直到停止；返回退出原因（signal/quit/stdin_eof）
"""
from __future__ import annotations

import asyncio
import sys
import threading
from typing import TYPE_CHECKING

from .kernel.contracts import Envelope

if TYPE_CHECKING:
    from .kernel.mind import SelfKernel
    from .utils.config import Config
    from .utils.recorder import Recorder


class Console:
    """宿主的开发控制台：stdin → 命令（控制面）/ 信封（感知面）。"""

    def __init__(self, kernel: "SelfKernel", cfg: "Config",
                 recorder: "Recorder") -> None:
        self._kernel = kernel
        self._cfg = cfg
        self._recorder = recorder
        self._queue: asyncio.Queue = asyncio.Queue()

    def start(self) -> None:
        """启动 stdin 守护线程（EOF 时投递 None，不阻塞事件循环）。"""
        loop = asyncio.get_running_loop()

        def _reader() -> None:
            while True:
                try:
                    line = sys.stdin.readline()
                except (OSError, ValueError):
                    line = ""
                if not line:
                    loop.call_soon_threadsafe(self._queue.put_nowait, None)
                    return
                loop.call_soon_threadsafe(self._queue.put_nowait, line)

        threading.Thread(target=_reader, name="stdin", daemon=True).start()

    async def run(self, stop: asyncio.Event) -> str:
        """消费输入直到停止。返回退出原因：signal / quit / stdin_eof。"""
        while not stop.is_set():
            get = asyncio.create_task(self._queue.get())
            sig = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait(
                {get, sig}, return_when=asyncio.FIRST_COMPLETED)
            if sig in done:
                get.cancel()
                return "signal"
            sig.cancel()
            line = get.result()
            if line is None:
                return "stdin_eof"
            text = line.strip()
            if not text:
                continue
            if text == "/quit":
                return "quit"
            if text == "/reload":
                changed = self._cfg.reload()
                if changed:
                    self._recorder.event("config_reload", changed=changed)
                print(f"↻ 配置重载：{', '.join(changed) or '无变化'}")
                continue
            self._kernel.notify(Envelope(text=text))
        return "signal"
