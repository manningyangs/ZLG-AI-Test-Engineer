"""ARCH-01 契约测试：Fake 插件必须满足全部契约语义与注册规则。"""

from __future__ import annotations

import json

import pytest

from zlg_ai_test_engine.domain.plugins.contracts import (
    PLUGIN_API_VERSION,
    ChannelConfig,
    DeviceInfo,
    Frame,
    ProtocolPlugin,
    api_compatible,
)
from zlg_ai_test_engine.plugins.fakes import (
    DictAssetParser,
    FakeDeviceSDKAdapter,
    FakeExecutionBackend,
    FakeProtocolPlugin,
    FakeReportExporter,
    FakeScriptBackend,
    ScriptedLLMProvider,
)
from zlg_ai_test_engine.plugins.registry import PluginKind, PluginManifest, PluginRegistry


def make_manifest(plugin_id: str = "fake-plugin", api_version: str = PLUGIN_API_VERSION, **overrides):
    data = {
        "id": plugin_id,
        "version": "1.0.0",
        "api_version": api_version,
        "license": "AGPL-3.0-only",
        "capabilities": ["demo"],
        "entrypoint": "zlg_ai_test_engine.plugins.fakes:FakeProtocolPlugin",
    }
    data.update(overrides)
    return PluginManifest.from_dict(data)


# ---------------------------------------------------------------------------
# Manifest 与注册规则
# ---------------------------------------------------------------------------


class TestManifestAndRegistry:
    def test_valid_manifest_registers(self):
        registry = PluginRegistry()
        assert registry.register(PluginKind.PROTOCOL, make_manifest(), object()) is True
        assert len(registry) == 1
        assert registry.rejected == ()

    def test_incompatible_api_version_rejected_without_exception(self):
        registry = PluginRegistry()
        ok = registry.register(PluginKind.PROTOCOL, make_manifest(api_version="999.0"), object())
        assert ok is False
        # 不兼容插件被拒绝但不阻塞注册流程
        assert len(registry.rejected) == 1
        assert "api_version" in registry.rejected[0].reason

    def test_schema_violation_rejected(self):
        registry = PluginRegistry()
        bad = PluginManifest(
            id="Bad ID!",  # 含大写与空格，违反 schema pattern
            version="not-semver",
            api_version=PLUGIN_API_VERSION,
            license="",
            capabilities=(),
            entrypoint="",
        )
        assert registry.register(PluginKind.PARSER, bad, object()) is False
        assert len(registry.rejected) == 1

    def test_duplicate_id_rejected(self):
        registry = PluginRegistry()
        assert registry.register(PluginKind.PARSER, make_manifest("dup"), object()) is True
        assert registry.register(PluginKind.PARSER, make_manifest("dup"), object()) is False
        assert "重复" in registry.rejected[0].reason

    def test_same_id_different_kind_allowed(self):
        registry = PluginRegistry()
        assert registry.register(PluginKind.PARSER, make_manifest("same"), object()) is True
        assert registry.register(PluginKind.PROTOCOL, make_manifest("same"), object()) is True

    def test_get_missing_raises_key_error(self):
        registry = PluginRegistry()
        with pytest.raises(KeyError):
            registry.get(PluginKind.PARSER, "missing")

    def test_api_compatible_major_match(self):
        assert api_compatible("1.0") is True
        assert api_compatible("1.7") is True
        assert api_compatible("2.0") is False
        assert api_compatible("garbage") is False


# ---------------------------------------------------------------------------
# ProtocolPlugin 契约
# ---------------------------------------------------------------------------


class TestProtocolContract:
    def test_protocol_contract_surface(self, tmp_path):
        plugin = FakeProtocolPlugin()
        # 抽象类无法实例化缺方法的子类；Fake 必须完整实现
        assert isinstance(plugin, ProtocolPlugin)
        assert plugin.decode(b"\x01\x02", {})["raw_hex"] == "0102"
        assert plugin.encode("MSG", {"payload_hex": "aabb"}, {}) == b"\xaa\xbb"
        ok, diags = plugin.validate_step({"action": "listen_bus"}, {})
        assert ok and not diags
        ok, diags = plugin.validate_step({}, {})
        assert not ok and diags[0].severity.value == "error"
        assert "send_frame" in plugin.action_schemas()
        assert "basic_tx" in plugin.required_device_capabilities()

    def test_encode_decode_roundtrip_hex(self):
        plugin = FakeProtocolPlugin()
        payload = plugin.encode("X", {"payload_hex": "0011ff"}, {})
        assert plugin.decode(payload, {})["raw_hex"] == "0011ff"


# ---------------------------------------------------------------------------
# DeviceSDKAdapter 契约
# ---------------------------------------------------------------------------


class TestDeviceAdapterContract:
    @pytest.fixture
    def adapter(self):
        a = FakeDeviceSDKAdapter()
        a.add_device(DeviceInfo(device_id="dev-0", model="FAKE-200U", serial="SN001", channel_count=2))
        return a

    def test_discover_open_close(self, adapter):
        assert [d.device_id for d in adapter.discover_devices()] == ["dev-0"]
        adapter.open_device("dev-0")
        with pytest.raises(RuntimeError, match="占用"):
            adapter.open_device("dev-0")  # 单实例锁语义
        adapter.close_device("dev-0")

    def test_channel_lifecycle_order(self, adapter):
        adapter.open_device("dev-0")
        with pytest.raises(RuntimeError, match="未配置"):
            adapter.start_channel("dev-0", 0)
        adapter.configure_channel("dev-0", ChannelConfig(0, "CAN", 500_000))
        adapter.start_channel("dev-0", 0)
        adapter.stop_channel("dev-0", 0)
        adapter.close_device("dev-0")

    def test_send_requires_open_and_records(self, adapter):
        frame = Frame(channel=0, can_id=0x123, data=b"\x01\x02")
        with pytest.raises(RuntimeError):
            adapter.send_frame("dev-0", frame)
        adapter.open_device("dev-0")
        result = adapter.send_frame("dev-0", frame)
        assert result.accepted == 1 and result.tx_echo is not None
        assert adapter.sent_frames() == (frame,)

    def test_send_failure_injection(self, adapter):
        adapter.open_device("dev-0")
        adapter.fail_send = True
        result = adapter.send_frame("dev-0", Frame(0, 1, b""))
        assert result.accepted == 0 and result.error_code is not None

    def test_unknown_capability_is_not_supported(self, adapter):
        # 未知能力必须返回 False（未知 ≠ 支持）
        assert adapter.is_capable("dev-0", "basic_tx") is True
        assert adapter.is_capable("dev-0", "hardware_periodic_tx") is False
        assert adapter.is_capable("dev-0", "never_seen") is False

    def test_emergency_stop_releases_everything(self, adapter):
        adapter.open_device("dev-0")
        adapter.configure_channel("dev-0", ChannelConfig(0, "CAN", 500_000))
        adapter.start_receiving("dev-0", lambda f: None)
        report = adapter.emergency_stop("dev-0")
        assert report.sends_stopped and report.channels_reset and report.device_closed
        # 停止后可重新打开（无残留占用）
        adapter.open_device("dev-0")
        adapter.close_device("dev-0")

    def test_receiving_callback(self, adapter):
        received = []
        adapter.open_device("dev-0")
        adapter.start_receiving("dev-0", received.append)
        adapter.inject_rx(Frame(0, 0x1, b"\xaa"))
        assert len(received) == 1
        adapter.stop_receiving("dev-0")


# ---------------------------------------------------------------------------
# AssetParser 契约
# ---------------------------------------------------------------------------


class TestParserContract:
    def test_parse_valid_json(self, tmp_path):
        f = tmp_path / "sample.json"
        f.write_text('{"messages": []}', encoding="utf-8")
        result = DictAssetParser().parse(f)
        assert result.parser_id == "fake_json"
        assert result.model == {"messages": []}
        assert result.source_sha256 and len(result.source_sha256) == 64
        assert not [d for d in result.diagnostics if d.severity.value == "error"]

    def test_parse_corrupt_file_reports_diagnostic(self, tmp_path):
        f = tmp_path / "broken.json"
        f.write_bytes(b"{not json")
        result = DictAssetParser().parse(f)
        errors = [d for d in result.diagnostics if d.severity.value == "error"]
        assert errors and errors[0].code == "parse_error"

    def test_same_file_same_hash(self, tmp_path):
        f = tmp_path / "a.json"
        f.write_text('{"x": 1}', encoding="utf-8")
        assert DictAssetParser().parse(f).source_sha256 == DictAssetParser().parse(f).source_sha256


# ---------------------------------------------------------------------------
# Script / Execution / LLM / Report 契约
# ---------------------------------------------------------------------------


class TestBackendsContract:
    IR = {
        "schema_version": "1.0",
        "case_id": "TC_DEMO",
        "steps": [
            {"id": "S1", "action": "listen_bus", "duration_ms": 1000},
            {"id": "S2", "action": "assert_cycle", "expected_period_ms": 20},
        ],
    }

    def test_script_backend_deterministic(self):
        backend = FakeScriptBackend()
        a = backend.generate(self.IR, {})
        b = backend.generate(self.IR, {})
        assert a.ir_hash == b.ir_hash
        assert a.code == b.code
        assert "不自动执行" in a.code

    def test_execution_backend_compile_stable_and_execute_events(self):
        backend = FakeExecutionBackend()
        p1 = backend.compile_plan(self.IR, {})
        p2 = backend.compile_plan(self.IR, {})
        assert p1 == p2
        assert p1["ir_hash"] and p1["commands"] and p1["cleanup"]
        events = []
        result = backend.execute(p1, events.append)
        assert result["state"] == "Completed"
        assert [e["type"] for e in events] == ["step", "step", "finished"]
        assert backend.cancel("run-1") is True

    def test_llm_structured_output_valid_json(self):
        provider = ScriptedLLMProvider(['{"ok": true}'])
        result = provider.structured_output(
            [{"role": "user", "content": "hi"}], model="scripted-1", json_schema={"type": "object"}
        )
        assert json.loads(result.text) == {"ok": True}
        assert result.usage.prompt_tokens == 10

    def test_llm_exhausted_raises_invalid_output(self):
        provider = ScriptedLLMProvider([])
        from zlg_ai_test_engine.domain.plugins.contracts import LLMError, LLMErrorKind

        with pytest.raises(LLMError) as exc:
            provider.complete([{"role": "user", "content": "hi"}], model="m")
        assert exc.value.kind == LLMErrorKind.INVALID_OUTPUT

    def test_report_exporter_writes_file(self, tmp_path):
        out = tmp_path / "reports" / "run.json"
        result = FakeReportExporter().export({"state": "Completed"}, ["/evidence/a.bin"], out)
        assert out.exists() and result.sha256
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["run_result"]["state"] == "Completed"
