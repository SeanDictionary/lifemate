"""QQ 插件配置声明（独立于 __init__：配置面与逻辑面分离，便于维护）。

装载器约定：模块导出 CONFIG = QQConfig（首启预设写盘 + schema 登记）。
"""

from __future__ import annotations

from .. import Field, PluginConfig


class QQConfig(PluginConfig):
    """QQ 插件配置（声明即文档；访问时读，热生效项见各读取点）。"""

    _namespace = "qq"
    enabled = Field(False, description="启用 QQ 插件（改后需重启）", restart=True)
    ws_url = Field("ws://127.0.0.1:3001", description="NapCat 正向 WS 地址（改后需重启）", restart=True)
    access_token = Field("", description="NapCat 访问令牌（改后需重启）", secret=True, restart=True)
    bot_uin = Field(0, description="机器人 QQ 号（热生效）")
    owner_uin = Field(0, description="主人 QQ 号（消息优先处理，热生效）")
    listen_groups = Field([], description="监听群号白名单（空=全部群，热生效）")
    chatlog_retention_days = Field(0, description="聊天记录保留天数（0=永久）")
    send_min_interval_s = Field(1.0, description="发送最小间隔秒（风控钳制）")
    send_max_per_min = Field(20, description="每分钟发送上限（风控钳制）")


CONFIG = QQConfig  # 装载器约定：首启预设写盘（enabled=false）
