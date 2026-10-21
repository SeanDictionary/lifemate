"""共享配置组件：JSON 文件为事实源 + env 种子 + mtime 热重载。

加载链：内置默认值 ← {data_dir}/config.json ← env 种子（env 显式设置时最强）
热重载：reload() 检测 mtime 变化后**原地更新字段**——引用方读属性即热生效，
不做冻结拷贝（LLM 客户端按签名热重建、阈值类参数用时读取）。

- data_dir 由 env LIFEMATE_DATA_DIR 引导（config.json 住在里面，鸡生蛋问题）
- 业务配置一律进 config.json 白名单；未知键告警忽略；密钥有掩码导出
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("utils.config")

# JSON 白名单（即文档：键名即配置项）
_JSON_FIELDS: dict[str, Any] = {
    # LLM
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "",
    # 内核
    "max_turns": 24,                    # 单个唤醒周期内最大模型轮数（超过强制休息）
    "turns_rest_nudge": 16,             # 连续轮数提醒阈值（先提示后强制）
    "tool_timeout_s": 120.0,            # 单工具执行超时（wait 除外）
    "compact_threshold_tokens": 24_000, # 超过触发压缩（估算）
    "keep_recent_tokens": 8_000,        # 压缩保留最近消息的估算 token
    # 路径（相对宿主 CWD）
    "soul_dir": "soul",
    "skills_dir": "skills", 
    # LLM 健壮性（借签 pi 自动重试语义）
    "llm_max_retries": 3,               # 瞬态错误重试次数
    "llm_retry_backoff_s": 2.0,         # 退避基数（指数：2/4/8s）
    "llm_fail_alert_streak": 5,         # 连续失败告警阈值（回调触发）
    # 系统日志
    "log_max_bytes": 10_485_760,        # 单份 .log 上限（10MB）
    "log_backup_count": 5,              # 轮转保留份数
    "log_level": "info",                # 日志等级 debug/info/warning/error/critical（CLI -l 覆盖）
    # 排队背压（永续回合下仅插话队列）
    "queue_max_steer": 16,              # 插话队列上限（溢出丢最旧并存证）
    # watchdog 自愈
    "watchdog_interval_s": 30.0,        # 健康检查间隔
    "max_run_s": 3600.0,                # 单次运行最大时长（超时中止并自愈）
    # 磁盘治理
    "observe_retention_days": 90,       # 观测档案保留天数（0=永久）
    "backup_interval_s": 86400.0,       # memory.db 备份间隔
    "backup_keep": 7,                   # 备份保留份数
    # 永续回合
    "reminder_poll_s": 15.0,            # 到期提醒轮询间隔（闹钟兑现周期）
    # 元配置
    "config_poll_s": 30.0,              # 热重载轮询间隔
    # 网络服务层（宿主侧，API + 前端静态托管；始终启动，无开关）
    "server_host": "127.0.0.1",       # 绑定地址（暴露局域网是显式行为）
    "server_port": 8300,              # 端口（绑定期读，改后需重启）
    "server_token": "",               # 非空则全端点校验 ?token=（每请求读，热生效）
    "server_static_dir": "web/out",   # 前端静态导出目录（相对 CWD）
    # 插件命名空间（自由子对象，插件私有键不进主白名单）
    "plugins": {},
    # 观测与档案
    "observe_max_field_chars": 65_536,  # 观测行单字段截断上限
    "session_keep_rotated": 2,          # 轮换后保留的旧会话文件数（档案在 observe）
}

# env 种子：显式设置时覆盖 JSON（容器部署用）
_ENV_SEED = {
    "llm_base_url": "LIFEMATE_LLM_BASE_URL",
    "llm_api_key": "LIFEMATE_LLM_API_KEY",
    "llm_model": "LIFEMATE_LLM_MODEL",
}

_SECRET_KEYS = {"llm_api_key"}


def coerce(value: Any, default: Any, key: str = "") -> Any:
    """按默认值类型矫正值（主白名单与插件配置共用）。"""
    try:
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, (list, dict)):
            return value if isinstance(value, type(default)) else default
        return str(value)
    except (TypeError, ValueError):
        log.warning("配置 %s=%r 类型非法（期望 %s），用默认值",
                    key or "?", value, type(default).__name__)
        return default


def _coerce(key: str, value: Any) -> Any:
    """按主白名单默认值类型矫正 JSON 读入的值。"""
    return coerce(value, _JSON_FIELDS[key], key)


class Config:
    """活配置对象：字段被热重载原地更新，引用方始终看到最新值。"""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = str(data_dir or os.environ.get("LIFEMATE_DATA_DIR", "data"))
        self.path = Path(self.data_dir) / "config.json"
        self._stamp: tuple[int, int] = (-1, -1)  # (mtime_ns, size)：纳秒级避免浮点精度丢失
        for k, v in _JSON_FIELDS.items():
            setattr(self, k, v)
        self.plugins: dict = {}  # 插件命名空间（config.json 的 plugins 子对象）
        self.reload(create_if_missing=True)

    # ── 加载/重载 ───────────────────────────────────────
    def reload(self, create_if_missing: bool = False) -> list[str]:
        """重读配置文件。返回发生变化的键名列表（空=无变化/无文件）。

        原地更新：已持有的 Config 引用立即看到新值。
        """
        try:
            st = self.path.stat()
        except OSError:
            if not create_if_missing:
                return []
            self._write_default()
            try:
                st = self.path.stat()
            except OSError:
                return []
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == self._stamp:
            return []
        self._stamp = stamp
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.error("配置文件解析失败（保持旧值）：%s", e)
            return []
        if not isinstance(data, dict):
            log.error("配置文件根节点必须是对象（保持旧值）")
            return []

        changed: list[str] = []
        # 插件命名空间：自由子对象，整体替换（热重载即时可见）
        plug = data.pop("plugins", None)
        if not isinstance(plug, dict):
            plug = {}
        if plug != self.plugins:
            self.plugins = plug
            changed.append("plugins")
        for key, raw in data.items():
            if key not in _JSON_FIELDS:
                log.warning("未知配置键 %r（白名单外，忽略）", key)
                continue
            value = _coerce(key, raw)
            env_var = _ENV_SEED.get(key)
            if env_var and os.environ.get(env_var):
                value = _coerce(key, os.environ[env_var])  # env 种子最强
            if getattr(self, key) != value:
                setattr(self, key, value)
                changed.append(key)
        # env 种子对未出现在 JSON 中的键也生效
        for key, env_var in _ENV_SEED.items():
            env_val = os.environ.get(env_var)
            if env_val and key not in data and getattr(self, key) != env_val:
                setattr(self, key, _coerce(key, env_val))
                if key not in changed:
                    changed.append(key)
        if changed:
            log.info("配置重载生效：%s", ", ".join(
                f"{k}=***" if k in _SECRET_KEYS else
                f"{k}={getattr(self, k)}" for k in changed))
        return changed

    def _write_default(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(dict(_JSON_FIELDS), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        log.info("生成默认配置：%s", self.path)

    def save(self) -> Path:
        """把当前白名单键 + plugins 命名空间原子写回 config.json。

        临时文件 + replace（防半写）；写后同步 _stamp（防下次 reload 重复
        解析）。热生效由字段原地更新保证，save 只负责持久化。"""
        data: dict[str, Any] = {k: getattr(self, k) for k in _JSON_FIELDS if k != "plugins"}
        data["plugins"] = self.plugins
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        tmp.replace(self.path)
        try:
            st = self.path.stat()
            self._stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass
        log.info("配置已保存：%s", self.path)
        return self.path

    def apply_updates(self, updates: dict[str, Any]) -> dict[str, Any]:
        """部分键更新（面板保存用）：白名单外拒绝、密钥掩码形态跳过
        （未改）、coerce 后原地生效（热生效）+ save() 落盘。

        返回 {applied, skipped_masked, unknown}。plugins 命名空间不接受
        （留给各插件面板将来的专属编辑页）。env 种子键可改可存，但重启
        后仍被 env 覆盖——由调用方提示（env_locked）。"""
        applied: list[str] = []
        skipped: list[str] = []
        unknown: list[str] = []
        for k, v in (updates or {}).items():
            if k not in _JSON_FIELDS or k == "plugins":
                unknown.append(k)
                continue
            if (k in _SECRET_KEYS and isinstance(v, str)
                    and v.startswith("***")):
                skipped.append(k)  # 掩码回传 = 未修改
                continue
            setattr(self, k, _coerce(k, v))
            applied.append(k)
        if applied:
            self.save()
        return {"applied": applied, "skipped_masked": skipped,
                "unknown": unknown}

    def write_plugin_defaults(self, name: str, section: dict) -> bool:
        """把插件默认配置段写入 config.json（仅当该段不存在时）。

        首启预设：插件声明字段以 enabled=false 落盘，用户直接可见可改；
        已有配置段（含用户改动）不碰。返回是否写入。
        """
        if name in self.plugins:
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
            if name in (data.get("plugins") or {}):
                return False  # 文件里有但内存没有（异常态）：以文件为准
            data.setdefault("plugins", {})[name] = section
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except (OSError, ValueError) as e:
            log.warning("插件 %s 默认配置写入失败：%s", name, e)
            return False
        # 同步内存并刷新戳，避免热重载误报变更
        self.plugins = dict(self.plugins)
        self.plugins[name] = dict(section)
        try:
            st = self.path.stat()
            self._stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass
        log.info("插件配置预设已写入：%s（enabled=false）", name)
        return True

    # ── 展示 ────────────────────────────────────────────
    def masked(self) -> dict[str, Any]:
        """掩码导出（日志/档案展示用，密钥只露尾 4 位）。

        主白名单键按 _SECRET_KEYS 掩码；plugins 命名空间深度掩码——
        键名含 token/secret/password/key 的字符串值只露尾 4 位
        （否则插件的 access_token 会明文进 observe boot 档案）。
        """
        out: dict[str, Any] = {}
        for k in _JSON_FIELDS:
            v = getattr(self, k)
            if k in _SECRET_KEYS and isinstance(v, str) and len(v) > 8:
                v = "***" + v[-4:]
            if k == "plugins" and isinstance(v, dict):
                v = {name: _mask_secrets(sub) for name, sub in v.items()
                     if isinstance(sub, dict)}
            out[k] = v
        return out


_SECRET_HINT = ("token", "secret", "password", "key")


def _mask_secrets(d: dict) -> dict:
    """插件子对象深度掩码：键名含敏感词的字符串值只露尾 4 位。"""
    out = {}
    for k, v in d.items():
        if (isinstance(v, str) and len(v) > 8
                and any(h in str(k).lower() for h in _SECRET_HINT)):
            out[k] = "***" + v[-4:]
        else:
            out[k] = v
    return out
