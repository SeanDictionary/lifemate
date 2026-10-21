"""插件体系：自动发现 + 装载（一个文件夹 = 一个插件）。

- lifemate/plugins/<name>/__init__.py 必须导出 create(ctx) -> Plugin | None
  （返回 None = 未启用；config 开关判断收在插件内部，装载器零知识）
- 插件目录可带 SKILL.md（自动并入 persona 技能索引）与私有资产
- 配置命名空间：config.json 的 plugins.<name> 子对象——加插件零主代码改动
- 数据目录约定：data/plugins/<name>/（PluginContext.data_dir 按插件隔离）

依赖方向：插件 → kernel.contracts / utils（向下）；kernel/host 永不 import
具体插件——这是自动发现的前提（装载器只认 create 工厂约定）。

注意：插件 __init__.py 里不要做重活（建连接等全放 start()）；
一个插件失败只跳过它自己，不拖垮启动。
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..kernel.contracts import PluginPack, Tool

if TYPE_CHECKING:
    from ..kernel.mind import SelfKernel
    from ..memory.store import Store
    from ..utils.config import Config
    from ..utils.recorder import Recorder

log = logging.getLogger("plugins")

PLUGIN_ROOT = Path(__file__).parent


@dataclass
class PluginContext:
    """插件装配上下文（宿主在启动时构造，每个插件拿到隔离副本）。"""

    kernel: "SelfKernel"
    store: "Store"
    cfg: "Config"
    recorder: "Recorder"
    data_dir: Path  # data/plugins/<name>/（插件私有数据）


class Plugin(PluginPack):
    """插件基类：工具+skill 的挂载单元 + 感知面生命周期。"""

    async def start(self, ctx: PluginContext) -> None:
        """启动感知面（常驻连接/事件循环）。失败只跳过本插件。"""

    async def stop(self) -> None:
        """停止感知面（宿主停机时调用）。"""


async def load_plugins(ctx: PluginContext) -> list[Plugin]:
    """扫描 plugins/ 目录，自动发现并装载全部插件。返回已装载列表。"""
    loaded: list[Plugin] = []
    for sub in sorted(PLUGIN_ROOT.iterdir()):
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        if not (sub / "__init__.py").exists():
            continue
        try:
            module = importlib.import_module(f"lifemate.plugins.{sub.name}")
        except Exception:  # noqa: BLE001
            log.exception("插件导入失败：%s", sub.name)
            continue
        create = getattr(module, "create", None)
        if not callable(create):
            log.warning("插件 %s 缺少 create(ctx) 入口，跳过", sub.name)
            continue
        # 每插件隔离的数据目录与上下文副本
        plugin_ctx = replace(ctx, data_dir=ctx.data_dir / sub.name)
        plugin_ctx.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            plugin = create(plugin_ctx)
        except Exception:  # noqa: BLE001
            log.exception("插件工厂失败：%s", sub.name)
            continue
        if plugin is None:
            log.info("插件未启用：%s", sub.name)
            continue
        try:
            ctx.kernel.register_plugin(plugin)
            await plugin.start(plugin_ctx)
        except Exception:  # noqa: BLE001
            log.exception("插件启动失败：%s（摘除）", sub.name)
            ctx.kernel.unregister_plugin(plugin.name)
            continue
        loaded.append(plugin)
        log.info("插件装载：%s（工具 %d 个）", plugin.name, len(plugin.tools))
    return loaded


async def stop_plugins(plugins: list[Plugin]) -> None:
    """停止全部插件（宿主停机时调用；单个失败不影响其它）。"""
    for p in plugins:
        try:
            await p.stop()
        except Exception:  # noqa: BLE001
            log.exception("插件停止失败：%s", p.name)
