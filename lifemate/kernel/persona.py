"""人格装配：identity + skills 索引，每轮请求重建（不缓存进长会话）。

- 身份（soul/identity.md）：数据非代码；缺失时用最小默认人格
- skills 目录（agentskills 风格 SKILL.md）：只注入 name+description 索引，
  全文由心智经 read_skill 工具按需读取（保持上下文精简）
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("kernel.persona")

_DEFAULT_IDENTITY = "你是一个数字生命，名叫小Q。诚实、自然、简洁。"

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_KV_RE = re.compile(r"^(\w[\w-]*):\s*(.+)$", re.M)


class _Skill:
    def __init__(self, name: str, description: str, content: str) -> None:
        self.name = name
        self.description = description
        self.content = content


class Persona:
    def __init__(self, soul_dir: Path | str, skills_dir: Path | str) -> None:
        self._soul_dir = Path(soul_dir)
        self._skills_dir = Path(skills_dir)
        self._skills: dict[str, _Skill] | None = None

    # ── 身份 ─────────────────────────────────────────────
    def identity(self) -> str:
        path = self._soul_dir / "identity.md"
        try:
            return path.read_text(encoding="utf-8").strip() or _DEFAULT_IDENTITY
        except OSError:
            return _DEFAULT_IDENTITY

    # ── skills ───────────────────────────────────────────
    def skills(self) -> dict[str, _Skill]:
        """扫描 skills 目录（每目录一个 SKILL.md；简单缓存按目录 mtime）。"""
        if not self._skills_dir.exists():
            return {}
        mtime = max(
            (p.stat().st_mtime for p in self._skills_dir.rglob("SKILL.md")),
            default=0.0)
        if self._skills is None or mtime != getattr(self, "_skills_mtime", -1):
            self._skills_mtime = mtime
            self._skills = {}
            for path in sorted(self._skills_dir.rglob("SKILL.md")):
                skill = self._parse(path)
                if skill:
                    self._skills[skill.name] = skill
        return self._skills

    @staticmethod
    def _parse(path: Path) -> _Skill | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        m = _FM_RE.match(text)
        name = desc = ""
        if m:
            for k, v in _KV_RE.findall(m.group(1)):
                if k == "name":
                    name = v.strip()
                elif k == "description":
                    desc = v.strip()
        if not name:
            name = path.parent.name
        return _Skill(name, desc, text)

    def read_skill(self, name: str) -> str:
        skill = self.skills().get(name)
        if not skill:
            return f"{{\"ok\": false, \"error\": \"skill 不存在: {name}\"}}"
        return skill.content

    # ── 装配 ─────────────────────────────────────────────
    def system_prompt(self) -> str:
        """每轮重建：身份 + skills 索引（工具清单由 API tools 参数携带）。"""
        parts = [self.identity()]
        skills = self.skills()
        if skills:
            lines = ["### 可用技能（用 read_skill 工具读取全文后再遵循）"]
            lines += [f"- {s.name}: {s.description}" for s in skills.values()]
            parts.append("\n".join(lines))
        return "\n\n".join(parts)
