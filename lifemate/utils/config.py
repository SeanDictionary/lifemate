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
import secrets
from pathlib import Path
from typing import Any

log = logging.getLogger("utils.config")


class Setting:
    """配置字段声明：默认值即类型与缺省，元数据即文档（声明即文档）。

    description  人类说明（schema 端点原样给前端设置页，不再硬编码）
    secret       密钥（掩码导出/回执打星/前端密码框）
    restart      改后需重启才生效（前端重启角标）
    group        展示分组（设置页按此归组）
    """

    __slots__ = ("default", "description", "secret", "restart", "group")

    def __init__(self, default: Any, *, description: str = "", secret: bool = False, restart: bool = False, group: str = "") -> None:
        self.default = default
        self.description = description
        self.secret = secret
        self.restart = restart
        self.group = group

    def type_name(self) -> str:
        """类型名（按默认值推断，与 coerce 规则同源）。"""
        if isinstance(self.default, bool):
            return "bool"
        if isinstance(self.default, (int, float)):
            return "number"
        if isinstance(self.default, list):
            return "array"
        if isinstance(self.default, dict):
            return "object"
        return "string"

    def schema_entry(self) -> dict[str, Any]:
        """schema 投影（API 契约）：类型/默认/说明/元数据。"""
        e: dict[str, Any] = {"type": self.type_name(), "default": self.default, "description": self.description}
        if self.group:
            e["group"] = self.group
        if self.secret:
            e["secret"] = True
        if self.restart:
            e["restart"] = True
        return e


# JSON 白名单（声明即文档：元数据经 /api/config/schema 投影给前端）
_JSON_FIELDS: dict[str, Setting] = {
    # 模型
    "llm_base_url": Setting("", description="LLM API 基础地址（OpenAI 兼容）", group="模型"),
    "llm_api_key": Setting("", description="LLM API 密钥", secret=True, group="模型"),
    "llm_model": Setting("", description="模型名", group="模型"),
    "llm_vision_model": Setting("", description="看图模型名（未配置=看图能力关闭：look_image 会直接回执告知心智，让其转告主人）", group="模型"),
    "llm_max_retries": Setting(3, description="瞬态错误重试次数", group="模型"),
    "llm_retry_backoff_s": Setting(2.0, description="重试退避基数（指数：2/4/8s）", group="模型"),
    "llm_fail_alert_streak": Setting(5, description="连续失败告警阈值（回调触发）", group="模型"),
    "llm_refuse_receipts": Setting(3, description="结果性失败（永久错误/输出被拒）回执进上下文的连续次数上限：超过后不再重复回执，改退避重试", group="模型"),
    # 内核
    "timezone": Setting("", description="主体时区（IANA 名如 Asia/Shanghai；空=本机时区）。昼夜/作息/社交时间感的事实源，换机器不换人生", group="内核"),
    "max_turns": Setting(60, description="单周期轮数熔断阈值（异常保护：防模型死循环无限烧 token；正常连续思考不受限，生理提醒见 turns_rest_nudge）", group="内核"),
    "turns_rest_nudge": Setting(16, description="连续轮数生理提醒阈值（周期性提醒，休息与否由心智自决）", group="内核"),
    "sleep_review_gap_s": Setting(21600, description="睡前自省最小间隔秒（最小有效值 60）：长睡前身体例行提醒过一遍清单（兴趣/需求/提醒/自我认识），只递到眼前、做不做由心智自决", group="内核"),
    "sleep_review_min_wait_s": Setting(3600, description="算作长睡的最短等待秒（短于它的小憩不触发睡前自省；无限等待算长睡）", group="内核"),
    "tool_timeout_s": Setting(120.0, description="单工具执行超时秒（wait 除外）", group="内核"),
    "compact_threshold_tokens": Setting(24_000, description="超过触发压缩（估算 token）", group="内核"),
    "keep_recent_tokens": Setting(8_000, description="压缩保留最近消息的估算 token", group="内核"),
    "ssrf_exclude_ips": Setting("", description="SSRF排除Fake IP网段", group="内核"),
    # 路径（相对宿主 CWD）
    "soul_dir": Setting("soul", description="人格目录（相对宿主 CWD）", group="路径"),
    "skills_dir": Setting("skills", description="技能目录（相对宿主 CWD）", group="路径"),
    # 系统日志
    "log_max_bytes": Setting(10_485_760, description="单份 .log 上限字节（10MB）", group="系统日志"),
    "log_backup_count": Setting(5, description="轮转保留份数", group="系统日志"),
    "log_level": Setting("info", description="日志等级 debug/info/warning/error/critical（CLI -l 覆盖）", group="系统日志"),
    # 排队背压 / watchdog / 磁盘治理 / 永续回合 / 元配置 → 守护
    "queue_max_steer": Setting(16, description="插话队列上限（溢出丢最旧并存证）", group="守护"),
    "watchdog_interval_s": Setting(30.0, description="健康检查间隔秒", group="守护"),
    "max_run_s": Setting(3600.0, description="单次运行最大时长秒（超时中止并自愈）", group="守护"),
    "backup_interval_s": Setting(86400.0, description="memory.db 备份间隔秒", group="守护"),
    "backup_keep": Setting(7, description="备份保留份数", group="守护"),
    "reminder_poll_s": Setting(15.0, description="到期提醒轮询间隔秒（闹钟兑现周期）", group="守护"),
    "config_poll_s": Setting(30.0, description="热重载轮询间隔秒", group="守护"),
    # 网络服务层（宿主侧，API + 前端静态托管；始终启动，无开关）
    "server_host": Setting("127.0.0.1", description="API 绑定地址（暴露局域网是显式行为；改后需重启）", restart=True, group="网络服务"),
    "server_port": Setting(8300, description="API 端口（改后需重启）", restart=True, group="网络服务"),
    "server_token": Setting("", description="API 访问令牌（空值自动生成随机值，永不裸奔）", secret=True, group="网络服务"),
    "server_static_dir": Setting("web/out", description="前端静态导出目录（相对 CWD）", group="网络服务"),
    # 插件命名空间（自由子对象，按各插件声明校验）
    "plugins": Setting({}, description="插件命名空间：各插件配置段（设置页按插件分组展示与编辑）"),
    # 观测与档案
    "observe_retention_days": Setting(90, description="观测档案保留天数（0=永久）", group="观测与档案"),
    "observe_max_field_chars": Setting(65_536, description="观测行单字段截断上限字符", group="观测与档案"),
    "session_keep_rotated": Setting(2, description="轮换后保留的旧会话文件数（档案在 observe）", group="观测与档案"),
    # 图像（look_image 感知能力）
    "image_max_mb": Setting(10, description="单张图片大小上限 MB（取图/归档/看图共用）", group="图像"),
    "image_cache_retention_days": Setting(30, description="图片缓存保留天数（0=永久；按最后查看时间淘汰，描述档案随之删除）", group="图像"),
    # 网络访问（web_search 检索 / web_fetch 读页）
    "web_search_provider": Setting("tavily", description="网络检索服务商：tavily / bocha（空=检索能力关闭：web_search 会直接回执告知心智，让其转告主人）", group="网络访问"),
    "web_search_api_key": Setting("", description="搜索服务 API 密钥（tavily: docs.tavily.com；bocha: open.bochaai.com）", secret=True, group="网络访问"),
    "web_search_base_url": Setting("", description="搜索端点基础地址（空=官方端点；可指向自建反代，自动拼接各家路径）", group="网络访问"),
    "web_search_max_results": Setting(8, description="单次检索返回来源条数上限", group="网络访问"),
    "web_search_summary_max_chars": Setting(2048, description="单条来源摘要截断字符（截断以 … 标记）", group="网络访问"),
    "web_fetch_max_mb": Setting(5, description="单页下载大小上限 MB（web_fetch）", group="网络访问"),
    "web_fetch_max_chars": Setting(12000, description="单页正文保留字符上限（超出截断并以 … 标记）", group="网络访问"),
}

# env 种子：显式设置时覆盖 JSON（容器部署用）
_ENV_SEED = {
    "llm_base_url": "LIFEMATE_LLM_BASE_URL",
    "llm_api_key": "LIFEMATE_LLM_API_KEY",
    "llm_model": "LIFEMATE_LLM_MODEL",
    "timezone": "LIFEMATE_TIMEZONE",  # 容器默认 UTC，部署时必设
    "web_search_provider": "LIFEMATE_WEB_SEARCH_PROVIDER",
    "web_search_api_key": "LIFEMATE_WEB_SEARCH_API_KEY",
    "server_port": "LIFEMATE_SERVER_PORT",  # CLI -p/--port 走此通道（env 显式设置时最强，热重载不回退）
}

# 密钥集合由声明派生（单一真相源）
_SECRET_KEYS = {k for k, f in _JSON_FIELDS.items() if f.secret}


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
        log.warning("配置 %s=%r 类型非法（期望 %s），用默认值", key or "?", value, type(default).__name__)
        return default


def _coerce(key: str, value: Any) -> Any:
    """按主白名单默认值类型矫正 JSON 读入的值。"""
    return coerce(value, _JSON_FIELDS[key].default, key)


class Config:
    """活配置对象：字段被热重载原地更新，引用方始终看到最新值。"""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = str(data_dir or os.environ.get("LIFEMATE_DATA_DIR", "data"))
        self.path = Path(self.data_dir) / "config.json"
        self._stamp: tuple[int, int] = (-1, -1)  # (mtime_ns, size)：纳秒级避免浮点精度丢失
        for k, f in _JSON_FIELDS.items():
            setattr(self, k, f.default)
        self.plugins: dict = {}  # 插件命名空间（config.json 的 plugins 子对象）
        self._plugin_schemas: dict[str, dict[str, Setting]] = {}  # 插件声明登记
        self.reload(create_if_missing=True)

    # ── 加载/重载 ───────────────────────────────────────
    def reload(self, create_if_missing: bool = False) -> list[str]:
        """重读配置文件。返回发生变化的键名列表（空=无变化/无文件）。

        原地更新：已持有的 Config 引用立即看到新值。
        缺字段（升级/手改）：内存回落默认 + 按默认顺序回写补全。
        """
        try:
            st = self.path.stat()
        except OSError:
            if not create_if_missing:
                return []
            self._write_default()
            self._stamp = (-1, -1)  # 新建文件需完整解析（_atomic_write 已同步戳，防止短路）
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
            log.error("配置文件解析失败（保持旧值，env 种子仍生效）：%s", e)
            return self._recover_from_bad_file()
        if not isinstance(data, dict):
            log.error("配置文件根节点必须是对象（保持旧值，env 种子仍生效）")
            return self._recover_from_bad_file()

        changed: list[str] = []
        # 插件命名空间：自由子对象，整体替换（热重载即时可见）
        plug = data.pop("plugins", None)
        if not isinstance(plug, dict):
            plug = {}
        if plug != self.plugins:
            self.plugins = plug
            changed.append("plugins")
        # 字段补全：升级/手改导致缺键——内存回落默认（env 种子随后仍会
        # 覆盖），并按默认生成的顺序与内容回写文件（config.json 自始至终
        # 是完整自文档；未知键保留在后，不丢用户数据）
        missing = [k for k in _JSON_FIELDS if k != "plugins" and k not in data]
        if missing:
            for k in missing:
                if getattr(self, k) != _JSON_FIELDS[k].default:
                    setattr(self, k, _JSON_FIELDS[k].default)
                    changed.append(k)
            rebuilt: dict[str, Any] = {}
            for k, f in _JSON_FIELDS.items():
                rebuilt[k] = plug if k == "plugins" else data.get(k, f.default)
            for k, v in data.items():  # 未知键附在尾部
                if k not in rebuilt:
                    rebuilt[k] = v
            self._atomic_write(rebuilt)
            log.info("配置补全缺失字段（已按默认顺序回写）：%s", ", ".join(missing))
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
        for k in self._apply_env_seeds(skip=frozenset(data)):
            if k not in changed:
                changed.append(k)
        if changed:
            log.info("配置重载生效：%s", ", ".join(f"{k}=***" if k in _SECRET_KEYS else f"{k}={getattr(self, k)}" for k in changed))
        self._ensure_token()
        return changed

    # ── 插件声明登记与 schema 投影 ─────────────────────
    def register_plugin_schema(self, name: str, fields: dict[str, Setting]) -> None:
        """登记插件配置声明（装载器扫描时调用）。

        schema 端点聚合与 plugins.<name>.<key> 写路径的校验依据；
        顺手缺键补全：声明有而文件缺的键按默认值回写（与主白名单
        reload 补全同一语义——config.json 始终是完整自文档，
        用户已改的值不碰；整段缺失仍由 write_plugin_defaults 处理）。
        """
        self._plugin_schemas[name] = fields
        self._fill_plugin_section(name, fields)

    def _fill_plugin_section(self, name: str, fields: dict[str, Setting]) -> None:
        """插件段缺键补全：插件新版本声明的字段落盘（启动时触发）。"""
        section = self.plugins.get(name)
        if not isinstance(section, dict):
            return  # 整段缺失 → write_plugin_defaults（首启预设）
        missing = [k for k, f in fields.items() if k not in section]
        if not missing:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("插件 %s 缺键补全读取失败：%s", name, e)
            return
        if not isinstance(data, dict):
            return
        on_disk = (data.get("plugins") or {}).get(name)
        if not isinstance(on_disk, dict):
            return  # 文件与内存不一致（异常态）：以文件为准，不补
        merged = dict(on_disk)
        for k in missing:
            merged[k] = fields[k].default
        data.setdefault("plugins", {})[name] = merged
        self._atomic_write(data)
        # 同步内存（_atomic_write 已同步戳，热重载不会重复解析）
        self.plugins = dict(self.plugins)
        self.plugins[name] = merged
        log.info("插件 %s 补全缺失字段：%s", name, ", ".join(missing))

    def schema_dict(self) -> dict[str, Any]:
        """配置说明书（声明投影）：主配置字段 + 已登记插件声明。

        前端设置页据此数据驱动渲染（类型/默认/说明/密钥/重启/分组），
        env_var 附加标注（是否真的被 env 占用由调用方按 os.environ 判）。"""
        fields: dict[str, Any] = {}
        for k, f in _JSON_FIELDS.items():
            if k == "plugins":
                fields[k] = {"type": "object", "default": {}, "description": "插件命名空间：各插件配置段（见 plugins 段，按插件分组）"}
                continue
            e = f.schema_entry()
            env_var = _ENV_SEED.get(k)
            if env_var:
                e["env_var"] = env_var
            fields[k] = e
        plugins = {name: {fk: f.schema_entry() for fk, f in fs.items()} for name, fs in self._plugin_schemas.items()}
        return {"fields": fields, "plugins": plugins}

    def _apply_env_seeds(self, skip: frozenset[str] = frozenset()) -> list[str]:
        """env 种子生效（最强覆盖；容器容错：坏文件时也靠它跑起来）。

        skip：本轮已从 JSON 处理过的键（主路径循环里已按 env 覆盖过）。
        返回实际发生变化的键名。"""
        changed: list[str] = []
        for key, env_var in _ENV_SEED.items():
            if key in skip:
                continue
            env_val = os.environ.get(env_var)
            if env_val and getattr(self, key) != env_val:
                setattr(self, key, _coerce(key, env_val))
                changed.append(key)
        return changed

    def _recover_from_bad_file(self) -> list[str]:
        """坏文件恢复：env 种子仍生效 + server_token 内存兑底。

        不回写文件——坏文件保持原样等主人修复（内容可能有救，
        不做破坏性覆盖）；server_token 只进内存（API 不裸奔，
        但不污染待修复的文件）。"""
        changed = self._apply_env_seeds()
        self._ensure_token(persist=False)
        return changed

    def _ensure_token(self, persist: bool = True) -> None:
        """server_token 空值重置：首启随 config 生成、热重载/保存后发现
        为空即换随机值并落盘——API 永不裸奔（安全默认）。

        persist=False：只进内存不落盘（坏文件恢复路径专用）。"""
        if self.server_token:
            return
        self.server_token = secrets.token_urlsafe(24)
        if persist:
            self.save()
            log.info("server_token 为空，已重置随机值（config.json 已更新，可在文件中查看）")
        else:
            log.warning("server_token 为空，已重置随机值（仅内存——配置文件异常，暂不落盘）")

    def _write_default(self) -> None:
        self._atomic_write({k: f.default for k, f in _JSON_FIELDS.items()})
        log.info("生成默认配置：%s", self.path)

    def _atomic_write(self, data: dict[str, Any]) -> None:
        """原子写回（临时文件 + replace，防半写）+ 同步戳（防重复解析）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        try:
            st = self.path.stat()
            self._stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass

    def save(self) -> Path:
        """把当前白名单键 + plugins 命名空间原子写回 config.json。

        按白名单原序写（与默认生成/补全回写一致）；磁盘上白名单外的
        用户键保留（不因保存而丢失）。临时文件 + replace（防半写）；
        写后同步 _stamp（防下次 reload 重复解析）。热生效由字段原地更新
        保证，save 只负责持久化。"""
        data: dict[str, Any] = {}
        for k in _JSON_FIELDS:
            data[k] = self.plugins if k == "plugins" else getattr(self, k)
        try:
            on_disk = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(on_disk, dict):
                for k, v in on_disk.items():
                    if k not in data:  # 白名单外用户键附在尾部
                        data[k] = v
        except (OSError, ValueError):
            pass
        self._atomic_write(data)
        log.info("配置已保存：%s", self.path)
        return self.path

    def apply_updates(self, updates: dict[str, Any]) -> dict[str, Any]:
        """部分键更新（面板保存用）：白名单外拒绝、密钥掩码形态跳过
        （未改）、coerce 后原地生效（热生效）+ save() 落盘。

        键形两种：主白名单键（如 log_level）与插件点路径
        （plugins.<name>.<key>，按已登记的插件声明校验+coerce）。
        返回 {applied, skipped_masked, unknown}。env 种子键可改可存，
        但重启后仍被 env 覆盖——由调用方提示（env_locked）。"""
        applied: list[str] = []
        skipped: list[str] = []
        unknown: list[str] = []
        for k, v in (updates or {}).items():
            if k.startswith("plugins."):
                # 插件点路径：plugins.<name>.<key>，按已登记声明校验
                parts = k.split(".", 2)
                if len(parts) == 3:
                    _, pname, fkey = parts
                    f = self._plugin_schemas.get(pname, {}).get(fkey)
                    if f is not None:
                        if f.secret and isinstance(v, str) and v.startswith("***"):
                            skipped.append(k)  # 掩码回传 = 未修改
                            continue
                        section = dict(self.plugins.get(pname) or {})
                        section[fkey] = coerce(v, f.default, f"plugins.{pname}.{fkey}")
                        self.plugins[pname] = section
                        applied.append(k)
                        continue
                unknown.append(k)
                continue
            if k not in _JSON_FIELDS or k == "plugins":
                unknown.append(k)
                continue
            if _JSON_FIELDS[k].secret and isinstance(v, str) and v.startswith("***"):
                skipped.append(k)  # 掩码回传 = 未修改
                continue
            setattr(self, k, _coerce(k, v))
            applied.append(k)
        if applied:
            self.save()
        result = {"applied": applied, "skipped_masked": skipped, "unknown": unknown}
        self._ensure_token()  # 面板清空 token 保存 → 立即换新（不等待重载）
        return result

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
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
                v = {name: _mask_secrets(sub, self._plugin_schemas.get(name)) for name, sub in v.items() if isinstance(sub, dict)}
            out[k] = v
        return out


_SECRET_HINT = ("token", "secret", "password", "key")


def _mask_secrets(d: dict, fields: dict[str, Setting] | None = None) -> dict:
    """插件子对象深度掩码：声明的 secret 字段精确掩码；未声明的退回
    键名敏感词启发（endswith：access_token ✓，max_tokens 不误掩）。
    字符串值只露尾 4 位。
    """
    out = {}
    for k, v in d.items():
        f = (fields or {}).get(k)
        secret = bool(f is not None and f.secret) or (isinstance(v, str) and len(v) > 8 and any(str(k).lower().endswith(h) for h in _SECRET_HINT))
        if secret and isinstance(v, str):
            out[k] = "***" + v[-4:]
        else:
            out[k] = v
    return out
