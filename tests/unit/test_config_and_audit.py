"""PLAT-03：配置中心、密钥安全存储与审计日志测试。

验收点（任务书 PLAT-03）：
- 密钥不落入明文配置文件；
- 审计记录可以按 run_id 检索；
- 配置校验与默认安全模式。
"""

from __future__ import annotations

import json

import pytest

from zlg_ai_test_engine.application.audit_service import AuditService
from zlg_ai_test_engine.application.config_service import ConfigService, Settings
from zlg_ai_test_engine.infrastructure.persistence import create_engine_and_session, init_db
from zlg_ai_test_engine.infrastructure.secrets import (
    InMemorySecretStore,
    reset_secret_store_for_tests,
)


@pytest.fixture
def session_factory(tmp_path):
    db_path = tmp_path / "audit.db"
    engine, factory = create_engine_and_session(db_path)
    init_db(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def config(tmp_path):
    store = InMemorySecretStore()
    return ConfigService(config_path=tmp_path / "settings.json", secret_store=store)


class TestSettings:
    def test_defaults_offline_and_l0(self):
        s = Settings()
        assert s.default_offline_mode is True
        assert s.default_security_level == "L0"

    def test_invalid_values_rejected(self):
        with pytest.raises(Exception):
            Settings(timeout_s=0)
        with pytest.raises(Exception):
            Settings(concurrency=99)
        with pytest.raises(Exception):
            Settings(default_security_level="L9")

    def test_theme_invalid_value_falls_back(self):
        assert Settings(theme="blue").theme == "light"

    def test_save_load_roundtrip(self, config):
        cfg = Settings(provider="deepseek", model="chat", timeout_s=30, theme="dark")
        config.save(cfg)
        loaded = config.load()
        assert loaded == cfg


class TestSecretSafety:
    def test_api_key_never_written_to_config_file(self, config):
        """验收：密钥不落入明文配置文件。"""
        config.save(Settings(provider="openai-compatible"))
        config.set_api_key("sk-super-secret-key-123")
        raw = config.config_path.read_text(encoding="utf-8")
        assert "sk-super-secret-key-123" not in raw
        # JSON 内也不含任何 key 字段
        data = json.loads(raw)
        assert "api_key" not in data
        # 但可通过 SecretStore 读回
        assert config.get_api_key() == "sk-super-secret-key-123"
        assert config.api_key_configured() is True

    def test_set_empty_key_deletes(self, config):
        config.set_api_key("abc")
        config.set_api_key("")
        assert config.get_api_key() is None
        assert config.api_key_configured() is False

    def test_corrupt_config_file_returns_defaults(self, config):
        config.config_path.write_text("{broken json", encoding="utf-8")
        assert config.load() == Settings()

    def test_in_memory_store_isolation(self):
        reset_secret_store_for_tests(None)  # 复位全局
        try:
            store = InMemorySecretStore()
            store.set_secret("k", "v")
            assert store.get_secret("k") == "v"
            store.delete_secret("k")
            assert store.get_secret("k") is None
        finally:
            reset_secret_store_for_tests(None)


class TestAuditLog:
    def test_record_and_query_by_run_id(self, session_factory):
        audit = AuditService(session_factory)
        id1 = audit.record(
            "run.start", user="tester", object_ref="case/TC-01", run_id="run-A"
        )
        id2 = audit.record("run.finish", user="tester", run_id="run-A", result="ok")
        audit.record("device.connect", user="tester", run_id="run-B")

        events = audit.list_events(run_id="run-A")
        assert {e.event_id for e in events} == {id1, id2}
        assert audit.list_events(run_id="run-B")[0].action == "device.connect"
        assert audit.list_events(action="run.finish")[0].run_id == "run-A"

    def test_append_only_event_ids_increase(self, session_factory):
        audit = AuditService(session_factory)
        ids = [audit.record(f"action.{i}") for i in range(5)]
        assert ids == sorted(ids) and len(set(ids)) == 5

    def test_audit_contains_approval_and_refs(self, session_factory):
        audit = AuditService(session_factory)
        audit.record(
            "send.approved",
            approval="granted",
            refs=[{"frame": "0x123"}, "evidence/raw.bin"],
        )
        event = audit.list_events(action="send.approved")[0]
        assert event.approval == "granted"
        assert event.refs_json == [{"frame": "0x123"}, "evidence/raw.bin"]
