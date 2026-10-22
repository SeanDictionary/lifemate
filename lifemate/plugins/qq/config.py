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
    wake_aliases = Field([], description="唤醒别名：消息文本含任一别名即视为强刺激（叫名字不@也能唤醒；回应与否心智自判）")
    conversation_window_s = Field(600.0, description="对话延续窗口秒：你发言后 N 秒内该会话的下一条消息升格为强刺激（0=关）")
    ambient_wake_threshold = Field(8, description="弱刺激攒批唤醒阈值：未读群消息攒 N 条即唤醒瞟一眼（0=关，纯背景听觉）")
    ambient_wake_gap_s = Field(600.0, description="弱刺激兑底唤醒秒：距上次唤醒超 N 秒且有≥2条未读即唤醒（防慢速闲聊攒不满）")
    ambient_wake_cooldown_s = Field(180.0, description="弱刺激唤醒冷却秒：两次攒批唤醒的最小间隔（防刷屏风暴叫醒心智）")
    chatlog_retention_days = Field(0, description="聊天记录保留天数（0=永久）")
    envelope_context_n = Field(4, description="跨会话切换时信封附带的该会话最近消息条数（0=关）")
    send_min_interval_s = Field(1.0, description="发送最小间隔秒（风控钳制）")
    send_max_per_min = Field(20, description="每分钟发送上限（风控钳制）")


CONFIG = QQConfig  # 装载器约定：首启预设写盘（enabled=false）
