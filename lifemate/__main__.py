"""入口（组合根）：python -m lifemate。

准备全项目共享部分（配置/单实例锁/运行标记/日志/内核装配/停机信号），
编排服务生命周期——当前唯一服务是 host（运行时）；将来 server
（网络 API）在此对等装配，共享同一份上下文，互不感知。

-v / --version：显示版本号并退出。
-l / --log-level：设置日志等级（默认使用config设置）。
-s / --silence：静默终端日志（输入输出照常）。
--no-stdin：服务模式，无控制台，纯信号驱动（后台/容器/服务化）。
"""
import argparse
import asyncio
import logging
import os
import signal
import time
from pathlib import Path

from .__version__ import __version__
from .host import Host, build
from .plugins import PluginContext, load_plugins, preset_plugin_defaults
from .server import Server
from .utils import instance
from .utils.config import Config
from .utils.log import (get_logger, install_excepthooks, install_loop_handler,
                        level_from_name, setup_logging)

log = get_logger("main")

ARGS = [
    {
        "flags": ("-v", "--version"),
        "params": {
            "action": "version",
            "version": __version__
        }
    },
    {
        "flags": ("-l", "--log-level"),
        "params": {
            "choices": ("debug", "info", "warning", "error", "critical"),
            "help": "Set log level (default from config)"
        }
    },
    {
        "flags": ("-s", "--silence"),
        "params": {
            "action": "store_true",
            "help": "Silence terminal logs (input/output still works)"
        }
    },
    {
        "flags": ("--no-stdin",),
        "params": {
            "action": "store_true",
            "help": "Service mode: no stdin console, signal-driven only "
                    "(for background/containers/services)"
        }
    }
]


def add_arguments(parser: argparse.ArgumentParser):
    parser.description = f"Lifemate {__version__}: Organize digital life runtime."
    for arg in ARGS:
        parser.add_argument(*arg['flags'], **arg['params'])


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """SIGINT/SIGTERM → 统一停机事件（所有服务共同订阅）。"""
    for sig in ("SIGINT", "SIGTERM"):
        if hasattr(signal, sig):
            def _on_signal(s, _f, _loop=asyncio.get_event_loop()):
                _loop.call_soon_threadsafe(stop.set)
            try:
                signal.signal(getattr(signal, sig), _on_signal)
            except (ValueError, OSError):
                pass  # 非主线程/平台不支持时忽略


async def _orchestrate(silence: bool, log_level: str | None,
                       no_stdin: bool) -> None:
    """组合根：共享准备 → 服务运行 → 统一收尾。"""
    lock = None
    run_marker: Path | None = None
    store = recorder = None
    try:
        cfg = Config()
        # 单实例锁先于一切数据访问
        lock = instance.acquire(cfg.data_dir)
        # 运行标记：优雅停机时删除；下次启动若仍在 → 上次是非正常退出
        run_marker = Path(cfg.data_dir) / "running"
        unclean_prev = run_marker.exists()
        if unclean_prev:
            log.warning("检测到上次非正常退出（崩溃/强杀/断电）——"
                        "WAL 残留将在库打开时自动吸收")
        run_marker.write_text(f"{os.getpid()}\n", encoding="utf-8")
        # 出生标记：首次启动判定（不依赖会话内容——首启即崩的场景会话也为空）
        born_marker = Path(cfg.data_dir) / "born"
        first_boot = not born_marker.exists()
        if first_boot:
            born_marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n",
                                   encoding="utf-8")
        level = level_from_name(log_level or cfg.log_level)
        log_path = setup_logging(
            cfg.data_dir, max_bytes=cfg.log_max_bytes,
            backup_count=cfg.log_backup_count,
            level=level, console=not silence)
        install_excepthooks()
        install_loop_handler(asyncio.get_event_loop())
        log.info("日志：%s（level=%s）", log_path, logging.getLevelName(level))

        # 首启预设先于 build()：首启必然未配 LLM，build() 会 SystemExit，
        # 插件默认段必须已落盘（否则首启生成的 config.json 缺插件段）
        preset_plugin_defaults(cfg)
        kernel, store, recorder = build(cfg)

        # 统一停机信号（信号/`/quit`/EOF 都指向它；服务共同订阅）
        stop = asyncio.Event()
        _install_signal_handlers(stop)

        # 插件装载（共享准备：host 与 server 都消费——boot 信封/停机 vs 面板路由）
        plugins = await load_plugins(PluginContext(
            kernel=kernel, store=store, cfg=cfg, recorder=recorder,
            data_dir=Path(cfg.data_dir) / "plugins"))

        # 服务清单：host（运行时）+ server（网络），共享同一套上下文
        host = Host(kernel, store, recorder, cfg, stop, plugins=plugins,
                    no_stdin=no_stdin, first_boot=first_boot,
                    unclean_prev=unclean_prev)
        server = Server(kernel, store, recorder, cfg, stop, plugins=plugins)

        host_task = asyncio.create_task(host.run())
        server_task = asyncio.create_task(server.serve())
        reason = await host_task      # host 主导生命周期（console/信号决定停）
        await server_task             # stop 置位后 server 自行收尾退出

        # 统一收尾（编排权在入口）：host 已中止思维与插件，此处记档关库
        recorder.event("host_stop", reason=reason)
        recorder.close()
        store.close()
        log.info("宿主退出")
    finally:
        instance.release(lock)
        if run_marker is not None:
            try:
                run_marker.unlink(missing_ok=True)  # 优雅停机：移除运行标记
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="lifemate")
    add_arguments(parser)
    args = parser.parse_args()
    try:
        asyncio.run(_orchestrate(args.silence, args.log_level, args.no_stdin))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
