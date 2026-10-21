"""入口：python -m lifemate（剥夺模式）。

-v / --version：显示版本号并退出。
-l / --log-level：设置日志等级（默认使用config设置）。
-s / --silence：静默终端日志（输入输出照常）。
"""
import argparse

from .__version__ import __version__
from .host import main as host_main


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="lifemate")
    add_arguments(parser)
    args = parser.parse_args()
    host_main(silence=args.silence, log_level=args.log_level,
              no_stdin=args.no_stdin)


if __name__ == "__main__":
    main()
