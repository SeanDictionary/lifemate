"""人格装配：identity + 内置机制指引 + skills 索引，每轮请求重建。

三层分工：
- soul/identity.md      人格（数据非代码，常驻；缺失时用最小默认人格）
- kernel/builtin/*.md   内置机制指引（代码管理，常驻——核心制度保证在场，
                        不能依赖"模型碰巧去读"）
- skills/*/SKILL.md     第三方/扩展知情层（目录扫描，索引常驻、全文按需）
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("kernel.persona")

_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"

_DEFAULT_IDENTITY = "你是一个数字生命。诚实、自然、简洁。"

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
        """扫描技能目录：skills/（全局第三方）+ plugins/*/SKILL.md（插件自带）。"""
        paths: list[Path] = []
        if self._skills_dir.exists():
            paths += list(self._skills_dir.rglob("SKILL.md"))
        if _PLUGINS_DIR.exists():
            paths += [p for p in _PLUGINS_DIR.glob("*/SKILL.md")]
        mtime = max((p.stat().st_mtime for p in paths), default=0.0)
        if self._skills is None or mtime != getattr(self, "_skills_mtime", -1):
            self._skills_mtime = mtime
            self._skills = {}
            for path in sorted(paths):
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
    def builtin_docs(self) -> list[str]:
        """内置机制指引（随内核分发，每轮常驻——核心制度保证在场）。"""
        out = []
        for p in sorted(_BUILTIN_DIR.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                out.append(text)
        return out

    def system_prompt(self) -> str:
        """每轮重建：身份 + 内置机制指引 + skills 索引（工具清单由 API tools 携带）。"""
        parts = [self.identity()]
        parts.extend(self.builtin_docs())
        skills = self.skills()
        if skills:
            lines = ["### 可用技能（全文经 read_skill 工具获取）"]
            lines += [f"- {s.name}: {s.description}" for s in skills.values()]
            parts.append("\n".join(lines))
        return "\n\n".join(x for x in parts if x.strip())
