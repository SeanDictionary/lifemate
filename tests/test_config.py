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
    monkeypatch.setenv("LIFEMATE_TIMEZONE", "Asia/Shanghai")
    cfg = Config(data_dir=tmp_path)
    assert cfg.llm_model == "env-model"  # env 覆盖默认
    assert cfg.timezone == "Asia/Shanghai"  # 时区 env 种子（容器部署用）
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


def test_broken_json_env_seeds_still_apply(tmp_path, monkeypatch):
    """坏 JSON 容错：env 种子仍生效，坏文件保持原样，token 内存兑底。

    场景：容器里 env 配齐了 LLM，挂载的 config.json 损坏——
    仍能起来（带病运行，日志有 error），不覆盖待修复的文件。"""
    monkeypatch.setenv("LIFEMATE_LLM_MODEL", "env-model")
    monkeypatch.setenv("LIFEMATE_LLM_BASE_URL", "http://env:1")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{broken json", encoding="utf-8")
    cfg = Config(data_dir=tmp_path)
    assert cfg.llm_model == "env-model"        # env 种子在坏文件下仍生效
    assert cfg.llm_base_url == "http://env:1"
    assert cfg.server_token                     # API 不裸奔（内存兑底）
    assert cfg_path.read_text(encoding="utf-8") == "{broken json"  # 文件未被覆盖
    # 修好文件后正常重载
    cfg_path.write_text(json.dumps({"llm_model": "fixed"}), encoding="utf-8")
    cfg.reload()
    assert cfg.llm_model == "env-model"        # env 仍最强


def test_non_dict_root_recovers_same_way(tmp_path, monkeypatch):
    """根节点非对象：同样走 env 恢复路径。"""
    monkeypatch.setenv("LIFEMATE_LLM_MODEL", "env-model")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("[1, 2]", encoding="utf-8")
    cfg = Config(data_dir=tmp_path)
    assert cfg.llm_model == "env-model"
    assert cfg.server_token
    assert cfg_path.read_text(encoding="utf-8") == "[1, 2]"


def test_masked_hides_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFEMATE_LLM_API_KEY", "sk-1234567890abcdef")
    cfg = Config(data_dir=tmp_path)
    m = cfg.masked()
    assert m["llm_api_key"].startswith("***") and "abcdef" not in m["llm_api_key"]
    assert m["llm_api_key"].endswith(m["llm_api_key"][-4:])


def test_plugin_namespace_secret_masked(tmp_path, monkeypatch):
    """plugins 命名空间深度掩码：token 类键不进日志/档案明文。"""
    cfg = Config(data_dir=tmp_path)
    cfg.plugins = {"qq": {"enabled": True, "ws_url": "ws://x:1",
                          "access_token": "sk-abcdef123456789",
                          "bot_uin": 123}}
    m = cfg.masked()
    qq = m["plugins"]["qq"]
    assert qq["access_token"].startswith("***")
    assert "123456789" not in qq["access_token"]
    assert qq["ws_url"] == "ws://x:1" and qq["bot_uin"] == 123  # 非敏感键原样


def test_plugin_defaults_written_on_fresh_config(tmp_path):
    """首启预设：插件声明段（enabled=false）自动写进 config.json。"""
    cfg = Config(data_dir=tmp_path)
    assert "qq" not in cfg.plugins
    from lifemate.plugins.qq import QQConfig
    assert cfg.write_plugin_defaults("qq", QQConfig.declared()) is True
    # 文件里有了，且 enabled=False
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert data["plugins"]["qq"]["enabled"] is False
    assert data["plugins"]["qq"]["ws_url"] == "ws://127.0.0.1:3001"
    # 内存同步（热重载不误报）
    assert cfg.reload() == []
    assert cfg.plugins["qq"]["enabled"] is False
    # 已有段不覆盖（用户改动受保护）
    assert cfg.write_plugin_defaults("qq", {"enabled": True}) is False


def test_save_and_apply_updates(tmp_path):
    """save() 原子写回 + apply_updates 部分键更新（面板保存链路）。"""
    cfg = Config(data_dir=tmp_path)
    cfg.log_level = "debug"
    cfg.save()
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert data["log_level"] == "debug"
    assert cfg.reload() == []                    # _stamp 已同步，不重复解析

    result = cfg.apply_updates({"log_level": "warning", "no_such_key": 1})
    assert result["applied"] == ["log_level"]
    assert result["unknown"] == ["no_such_key"]
    assert cfg.log_level == "warning"
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert data["log_level"] == "warning"        # 已落盘

    # 密钥掩码形态跳过（未修改）
    cfg.llm_api_key = "sk-secret-12345678"
    result = cfg.apply_updates({"llm_api_key": "***5678"})
    assert result["skipped_masked"] == ["llm_api_key"]
    assert cfg.llm_api_key == "sk-secret-12345678"


def test_backfills_missing_fields_on_load(tmp_path):
    """已有 config.json 缺字段：加载即按默认顺序/内容补全回写。

    场景：版本升级新增白名单键（如 timezone），旧文件没有——
    用户打开 config.json 永远能看到完整可改的键表。"""
    from lifemate.utils.config import _JSON_FIELDS
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(
        {"llm_model": "my-model", "my_custom": 1}, ensure_ascii=False),
        encoding="utf-8")
    cfg = Config(data_dir=tmp_path)
    # 内存：文件里有的用文件值，缺的用默认
    assert cfg.llm_model == "my-model"
    assert cfg.timezone == ""
    # 文件：白名单键全部补齐，顺序=默认生成顺序，未知键保留在尾部
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert set(data) >= set(_JSON_FIELDS) | {"my_custom"}
    assert list(data.keys())[:len(_JSON_FIELDS)] == list(_JSON_FIELDS)
    assert data["llm_model"] == "my-model"   # 已有值不被覆盖
    assert data["timezone"] == ""            # 缺的按默认内容补
    assert data["plugins"] == {}
    assert cfg.reload() == []                 # 回写已同步戳，不重复解析


def test_backfill_on_hot_reload_resets_memory(tmp_path):
    """热重载发现缺键：内存回落默认（不残留旧值）+ 文件补全。"""
    cfg = Config(data_dir=tmp_path)
    cfg.log_level = "debug"                  # 模拟运行中改过
    cfg.path.write_text(json.dumps({"llm_model": "x"}),
                        encoding="utf-8")   # 手写缺 log_level 的文件
    changed = cfg.reload()
    assert "log_level" in changed
    assert cfg.log_level == "info"           # 回落默认，不残留 debug
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert data["log_level"] == "info"       # 文件也补全了


def test_server_token_generated_and_reset(tmp_path):
    """server_token：首启随 config 生成随机值；清空重载即重置（永不裸奔）。"""
    cfg = Config(data_dir=tmp_path)
    assert cfg.server_token and len(cfg.server_token) >= 20   # 首启已生成
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert data["server_token"] == cfg.server_token           # 已落盘

    # 用户清空（文件层面）→ 热重载发现为空 → 换新随机值并落盘
    data["server_token"] = ""
    cfg.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    old = cfg.server_token
    cfg.reload()
    assert cfg.server_token and cfg.server_token != old
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert data["server_token"] == cfg.server_token           # 新值已写回

    # 掩码：API/档案不泄露
    masked = cfg.masked()
    assert masked["server_token"].startswith("***")
    assert cfg.server_token not in json.dumps(masked)
