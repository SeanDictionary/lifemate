"""示例插件：演示自动发现与生命周期（默认关闭）。

启用方式：config.json 的 plugins.example.enabled = true
新增插件 = 复制本目录改写：create(ctx) 返回 Plugin（None=未启用），
SKILL.md 会被 persona 自动并入技能索引。
"""
from ...kernel.contracts import Tool
from .. import Plugin, PluginContext

NAME = "example"


def create(ctx: PluginContext) -> Plugin | None:
    if not ctx.cfg.plugins.get("example", {}).get("enabled"):
        return None

    async def _hello(args: dict) -> str:
        import json
        return json.dumps(
            {"ok": True, "msg": "hello from example plugin",
             "note": "我是一个样板插件，用来验证自动发现装载"},
            ensure_ascii=False)

    tool = Tool(schema={
        "name": "example_hello",
        "description": "样板插件的示例工具：验证插件体系工作正常。",
        "inputSchema": {"type": "object", "properties": {}},
    }, func=_hello)

    return Plugin(name=NAME, tools=[tool],
                  skills=[NAME])
