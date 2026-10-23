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

import importlib
import logging
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..kernel.contracts import PluginPack
from ..utils.config import Setting, coerce

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

    kernel: SelfKernel
    store: Store
    cfg: Config
    recorder: Recorder
    data_dir: Path  # data/plugins/<name>/（插件私有数据）


class Plugin(PluginPack):
    """插件基类：工具+skill 的挂载单元 + 感知面生命周期。"""

    async def start(self, ctx: PluginContext) -> None:
        """启动感知面（常驻连接/事件循环）。失败只跳过本插件。"""

    async def stop(self) -> None:
        """停止感知面（宿主停机时调用）。"""


class Field(Setting):
    """插件配置字段声明：默认值即类型与缺省，元数据即文档（继承
    utils.config.Setting——schema 投影/写路径校验共用）；类属性形态 +
    从活配置 plugins 命名空间读取（热重载即时生效）。

    访问时读+按默认值类型矫正；坏值告警并回落默认，
    不让配置错误炸排事件处理。
    """

    __slots__ = ("name",)

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        raw = obj._live.plugins.get(obj._namespace, {}).get(self.name, self.default)
        return coerce(raw, self.default, f"plugins.{obj._namespace}.{self.name}")


class PluginConfig:
    """插件配置基类：声明式字段 + 活配置绑定。

    用法：
        class QQConfig(PluginConfig):
            _namespace = "qq"
            ws_url = Field("ws://127.0.0.1:3001")
            bot_uin = Field(0)

        conf = QQConfig(ctx.cfg)      # 绑定主 Config 活引用
        conf.ws_url                   # 用时读+类型矫正（热生效）

    declared() 枚举全部字段（设置页/文档自动渲染用）。
    """

    _namespace: str = ""

    def __init__(self, cfg) -> None:
        self._live = cfg  # 主 Config 活引用（热重载原地更新 plugins 字典）

    @classmethod
    def declared(cls) -> dict[str, Any]:
        """全部声明字段：{键: 默认值}（首启预设写盘用）。"""
        return {name: f.default for name, f in vars(cls).items() if isinstance(f, Field)}

    @classmethod
    def declared_fields(cls) -> dict[str, Field]:
        """全部声明字段（含元数据）：schema 投影/写路径校验用。"""
        return {name: f for name, f in vars(cls).items() if isinstance(f, Field)}


def preset_plugin_defaults(cfg: Config) -> list[str]:
    """扫描插件目录，把声明式默认配置段（enabled=false）写进 config.json。

    只读 CONFIG 声明、不启动任何插件；已有段不碰（幂等）。
    宿主在 build() 之前调用——首启 LLM 未配置会提前 SystemExit，
    预设必须先行落盘，否则首启生成的 config.json 缺插件段，用户无从下手。
    """
    written: list[str] = []
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
        conf_cls = getattr(module, "CONFIG", None)
        if isinstance(conf_cls, type) and issubclass(conf_cls, PluginConfig) and conf_cls.declared():
            # 声明登记（schema 端点聚合 + plugins.<name>.<key> 写路径校验）
            cfg.register_plugin_schema(sub.name, conf_cls.declared_fields())
            if cfg.write_plugin_defaults(sub.name, conf_cls.declared()):
                written.append(sub.name)
    return written


async def load_plugins(ctx: PluginContext) -> list[Plugin]:
    """扫描 plugins/ 目录，自动发现并装载全部插件。返回已装载列表。"""
    # 首启预设：声明式配置的插件把默认段（enabled=false）写进 config.json，
    # 用户直接可见可改（约定：模块导出 CONFIG = XxxConfig；幂等，宿主已
    # 预写过则跳过——保留此调用使直接调用 load_plugins 的场景同样生效）
    preset_plugin_defaults(ctx.cfg)
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
            ctx.recorder.event("error", where="plugins", error=f"插件 {sub.name} 导入失败", traceback=traceback.format_exc()[-4000:])
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
            ctx.recorder.event("error", where="plugins", error=f"插件 {sub.name} 工厂失败", traceback=traceback.format_exc()[-4000:])
            continue
        if plugin is None:
            log.info("插件未启用：%s", sub.name)
            continue
        try:
            ctx.kernel.register_plugin(plugin)
            await plugin.start(plugin_ctx)
        except Exception:  # noqa: BLE001
            log.exception("插件启动失败：%s（摘除）", sub.name)
            ctx.recorder.event("error", where="plugins", error=f"插件 {sub.name} 启动失败（已摘除）", traceback=traceback.format_exc()[-4000:])
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
