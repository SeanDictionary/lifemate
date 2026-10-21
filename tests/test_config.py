"""共享配置组件测试：JSON 事实源 / env 种子 / 热重载 / 掩码。"""
import json
import os

from lifemate.utils.config import Config


def _write(cfg: Config, **kv) -> None:
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    data.update(kv)
    cfg.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    st = os.stat(cfg.path)  # 写后 +1ms，规避文件系统时间戳粒度
    os.utime(cfg.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_creates_default_file(tmp_path):
    cfg = Config(data_dir=tmp_path / "d")
    assert cfg.path.exists()
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert data["max_turns"] == 24 and "llm_api_key" in data


def test_json_override_and_reload(tmp_path):
    cfg = Config(data_dir=tmp_path)
    assert cfg.max_turns == 24
    _write(cfg, max_turns=8, llm_model="m2")
    assert cfg.max_turns == 24  # 未 reload 不生效
    changed = cfg.reload()
    assert set(changed) == {"max_turns", "llm_model"}
    assert cfg.max_turns == 8 and cfg.llm_model == "m2"
    assert cfg.reload() == []  # mtime 未变，无变化


def test_env_seed_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFEMATE_LLM_MODEL", "env-model")
    cfg = Config(data_dir=tmp_path)
    assert cfg.llm_model == "env-model"  # env 覆盖默认
    _write(cfg, llm_model="json-model")
    cfg.reload()
    assert cfg.llm_model == "env-model"  # env 仍最强


def test_type_coercion_and_unknown_key(tmp_path):
    cfg = Config(data_dir=tmp_path)
    _write(cfg, max_turns="12", config_poll_s="15", bad_key=1)
    changed = cfg.reload()
    assert cfg.max_turns == 12 and isinstance(cfg.max_turns, int)
    assert cfg.config_poll_s == 15.0 and isinstance(cfg.config_poll_s, float)
    assert not hasattr(cfg, "bad_key")
    assert "bad_key" not in changed


def test_hot_reference_semantics(tmp_path):
    """热重载原地更新：旧引用立即看到新值。"""
    cfg = Config(data_dir=tmp_path)
    ref = cfg  # 模拟组件持有的引用
    _write(cfg, tool_timeout_s=5.5)
    cfg.reload()
    assert ref.tool_timeout_s == 5.5


def test_broken_json_keeps_old_values(tmp_path):
    cfg = Config(data_dir=tmp_path)
    _write(cfg, max_turns=9)
    cfg.reload()
    assert cfg.max_turns == 9
    cfg.path.write_text("{broken json", encoding="utf-8")
    assert cfg.reload() == []
    assert cfg.max_turns == 9  # 解析失败保持旧值


def test_masked_hides_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFEMATE_LLM_API_KEY", "sk-1234567890abcdef")
    cfg = Config(data_dir=tmp_path)
    m = cfg.masked()
    assert m["llm_api_key"].startswith("***") and "abcdef" not in m["llm_api_key"]
    assert m["llm_api_key"].endswith(m["llm_api_key"][-4:])
