"""内核引导配置：env 优先，带默认值。

当前阶段只有内核必需项；器官的业务配置（QQ 限额/沙箱地址/作息区间等）
在器官落地时进白名单扩展，禁止散落硬编码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# 可被 env 覆盖的键（白名单即文档）
_ENV_KEYS = (
    "LIFEMATE_DATA_DIR",       # 运行数据目录（memory.db / sessions/）
    "LIFEMATE_SOUL_DIR",       # 人格目录（identity.md）
    "LIFEMATE_SKILLS_DIR",     # skill 目录
    "LIFEMATE_LLM_BASE_URL",   # OpenAI 兼容端点
    "LIFEMATE_LLM_API_KEY",
    "LIFEMATE_LLM_MODEL",
    "LIFEMATE_MAX_TURNS",      # 单次运行（一封信封）内最大模型轮数
    "LIFEMATE_TOOL_TIMEOUT_S", # 单工具执行超时
    "LIFEMATE_COMPACT_THRESHOLD_TOKENS",  # 超过则触发压缩（估算）
    "LIFEMATE_KEEP_RECENT_TOKENS",        # 压缩时保留最近消息的估算 token
)


@dataclass(frozen=True)
class Config:
    data_dir: str = "data"
    soul_dir: str = "soul"
    skills_dir: str = "skills"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    max_turns: int = 24
    tool_timeout_s: float = 120.0
    compact_threshold_tokens: int = 24_000
    keep_recent_tokens: int = 8_000


def load() -> Config:
    """从 env 装配配置（进程启动时一次；热更新走未来的配置器官）。"""
    def _int(key: str, default: int) -> int:
        try:
            return int(os.environ[key])
        except (KeyError, ValueError):
            return default

    def _float(key: str, default: float) -> float:
        try:
            return float(os.environ[key])
        except (KeyError, ValueError):
            return default

    return Config(
        data_dir=os.environ.get("LIFEMATE_DATA_DIR", Config.data_dir),
        soul_dir=os.environ.get("LIFEMATE_SOUL_DIR", Config.soul_dir),
        skills_dir=os.environ.get("LIFEMATE_SKILLS_DIR", Config.skills_dir),
        llm_base_url=os.environ.get("LIFEMATE_LLM_BASE_URL", ""),
        llm_api_key=os.environ.get("LIFEMATE_LLM_API_KEY", ""),
        llm_model=os.environ.get("LIFEMATE_LLM_MODEL", ""),
        max_turns=_int("LIFEMATE_MAX_TURNS", Config.max_turns),
        tool_timeout_s=_float("LIFEMATE_TOOL_TIMEOUT_S", Config.tool_timeout_s),
        compact_threshold_tokens=_int(
            "LIFEMATE_COMPACT_THRESHOLD_TOKENS", Config.compact_threshold_tokens),
        keep_recent_tokens=_int("LIFEMATE_KEEP_RECENT_TOKENS", Config.keep_recent_tokens),
    )
