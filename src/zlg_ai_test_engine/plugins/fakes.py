"""契约 Fake 实现（ARCH-01）：供契约测试、Mock Adapter 与离线开发使用。

全部为确定性实现：
- 不加载任何 DLL、不进行网络调用、不读写设备；
- LLM Fake 由固定脚本应答，保证测试可复现。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from zlg_ai_test_engine.domain.plugins.contracts import (
    AssetParser,
    ChannelConfig,
    DeviceInfo,
    DeviceSDKAdapter,
    Diagnostic,
    EmergencyStopReport,
    ExecutionBackend,
    ExportResult,
    Frame,
    LLMError,
    LLMErrorKind,
    LLMProvider,
    LLMResult,
    LLMUsage,
    ParseResult,
    ProtocolPlugin,
    ReportExporter,
    ScriptArtifact,
    ScriptBackend,
    SendResult,
    Severity,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class FakeProtocolPlugin(ProtocolPlugin):
    """恒真协议插件：decode 为十六进制透传，encode 为字节拼装。"""

    def __init__(self, protocol_id: str = "FAKE_CAN", version: str = "1.0.0") -> None:
        self._protocol_id = protocol_id
        self._version = version

    @property
    def protocol_id(self) -> str:
        return self._protocol_id

    @property
    def protocol_version(self) -> str:
        return self._version

    @property
    def supported_transports(self) -> tuple[str, ...]:
        return ("CAN", "CAN_FD")

    def decode(self, payload: bytes, database_context: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"raw_hex": payload.hex(), "length": len(payload)}

    def encode(
        self, message_ref: str, values: Mapping[str, Any], database_context: Mapping[str, Any]
    ) -> bytes:
        if "payload_hex" in values:
            return bytes.fromhex(str(values["payload_hex"]))
        return message_ref.encode("ascii")[:8].ljust(8, b"\x00")

    def validate_step(
        self, step: Mapping[str, Any], database_context: Mapping[str, Any]
    ) -> tuple[bool, tuple[Diagnostic, ...]]:
        action = step.get("action")
        if not action:
            return False, (Diagnostic("missing_action", "步骤缺少 action 字段", Severity.ERROR),)
        return True, ()

    def action_schemas(self) -> Mapping[str, Mapping[str, Any]]:
        return {
            "listen_bus": {"type": "object", "required": ["duration_ms"]},
            "send_frame": {"type": "object", "required": ["payload_hex"]},
        }

    def required_device_capabilities(self) -> frozenset[str]:
        return frozenset({"basic_tx", "basic_rx"})


# ---------------------------------------------------------------------------
# Device adapter
# ---------------------------------------------------------------------------


class FakeDeviceSDKAdapter(DeviceSDKAdapter):
    """进程内确定性设备适配器：单设备、内存收发、可注入故障。"""

    def __init__(self) -> None:
        self._devices: dict[str, DeviceInfo] = {}
        self._open: set[str] = set()
        self._configured: dict[str, ChannelConfig] = {}
        self._rx_callback: Callable[[Frame], None] | None = None
        self._receiving: set[str] = set()
        self._sent: list[Frame] = []
        #: 注入故障开关（供故障/异常退出测试）
        self.fail_open = False
        self.fail_send = False

    # -- 配置辅助（测试用） ---------------------------------------------------
    def add_device(self, info: DeviceInfo) -> None:
        self._devices[info.device_id] = info

    def sent_frames(self) -> tuple[Frame, ...]:
        return tuple(self._sent)

    def inject_rx(self, frame: Frame) -> None:
        if self._rx_callback is not None:
            self._rx_callback(frame)

    # -- 契约实现 ------------------------------------------------------------
    @property
    def adapter_id(self) -> str:
        return "fake_device"

    @property
    def adapter_version(self) -> str:
        return "1.0.0"

    def discover_devices(self) -> Sequence[DeviceInfo]:
        return list(self._devices.values())

    def get_device_info(self, device_id: str) -> DeviceInfo:
        if device_id not in self._devices:
            raise KeyError(f"设备不存在: {device_id}")
        return self._devices[device_id]

    def open_device(self, device_id: str) -> None:
        if self.fail_open:
            raise RuntimeError("注入故障：open_device 失败")
        self.get_device_info(device_id)
        if device_id in self._open:
            raise RuntimeError(f"设备已被占用: {device_id}")
        self._open.add(device_id)

    def close_device(self, device_id: str) -> None:
        self._open.discard(device_id)
        self._receiving.discard(device_id)
        self._configured.pop(device_id, None)

    def configure_channel(self, device_id: str, config: ChannelConfig) -> None:
        if device_id not in self._open:
            raise RuntimeError("设备未打开")
        self._configured[device_id] = config

    def start_channel(self, device_id: str, channel_index: int) -> None:
        if device_id not in self._configured:
            raise RuntimeError("通道未配置")

    def stop_channel(self, device_id: str, channel_index: int) -> None:
        return None

    def send_frame(self, device_id: str, frame: Frame) -> SendResult:
        if self.fail_send:
            return SendResult(requested=1, accepted=0, error_code=-1)
        if device_id not in self._open:
            raise RuntimeError("设备未打开")
        self._sent.append(frame)
        return SendResult(requested=1, accepted=1, tx_echo=frame)

    def start_receiving(self, device_id: str, callback: Callable[[Frame], None]) -> None:
        if device_id not in self._open:
            raise RuntimeError("设备未打开")
        self._rx_callback = callback
        self._receiving.add(device_id)

    def stop_receiving(self, device_id: str) -> None:
        self._receiving.discard(device_id)

    def device_capabilities(self, device_id: str) -> Mapping[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "buses": ["CAN", "CAN_FD"],
            "evidence": {"basic_tx": "verified_on_device", "hardware_periodic_tx": "unknown"},
        }

    def is_capable(self, device_id: str, capability: str) -> bool:
        caps = self.device_capabilities(device_id)
        level = caps.get("evidence", {}).get(capability, "unknown")
        return level in ("official_declared", "sdk_visible", "verified_on_device")

    def emergency_stop(self, device_id: str) -> EmergencyStopReport:
        self._receiving.discard(device_id)
        self._open.discard(device_id)
        return EmergencyStopReport(
            sends_stopped=True, queues_cleared=True, channels_reset=True, device_closed=True
        )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class DictAssetParser(AssetParser):
    """JSON 文件解析 Fake：把文件内容解析为 {"messages": [...]} 结构。"""

    parser_id_value = "fake_json"
    parser_version_value = "1.0.0"

    @property
    def parser_id(self) -> str:
        return self.parser_id_value

    @property
    def parser_version(self) -> str:
        return self.parser_version_value

    @property
    def supported_file_types(self) -> tuple[str, ...]:
        return (".json",)

    def parse(self, path: Path) -> ParseResult:
        import hashlib

        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        diagnostics: list[Diagnostic] = []
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return ParseResult(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                model={},
                diagnostics=(Diagnostic("parse_error", f"JSON 解析失败: {exc}", Severity.ERROR),),
                source_sha256=sha,
            )
        if not isinstance(data, dict):
            diagnostics.append(
                Diagnostic("unexpected_root", "根节点应为对象", Severity.WARNING)
            )
            data = {}
        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            model=data,
            diagnostics=tuple(diagnostics),
            source_sha256=sha,
        )


# ---------------------------------------------------------------------------
# Script / Execution backends
# ---------------------------------------------------------------------------


class FakeScriptBackend(ScriptBackend):
    """把 Test IR 摘要渲染为只读 Python 伪代码（仅预览，不执行）。"""

    @property
    def backend_id(self) -> str:
        return "fake_script"

    @property
    def backend_version(self) -> str:
        return "1.0.0"

    @property
    def target_language(self) -> str:
        return "python"

    def generate(self, test_ir: Mapping[str, Any], context: Mapping[str, Any]) -> ScriptArtifact:
        import hashlib

        ir_text = json.dumps(test_ir, sort_keys=True, ensure_ascii=False)
        ir_hash = hashlib.sha256(ir_text.encode("utf-8")).hexdigest()
        steps = test_ir.get("steps", [])
        lines = [f"# Test IR {test_ir.get('case_id', '?')} (hash={ir_hash[:12]})"]
        lines.append("# 仅供审查，不自动执行")
        for step in steps:
            lines.append(f"run_step({step.get('id')!r}, action={step.get('action')!r})")
        return ScriptArtifact(
            backend_id=self.backend_id,
            language=self.target_language,
            code="\n".join(lines),
            ir_hash=ir_hash,
        )


class FakeExecutionBackend(ExecutionBackend):
    """确定性计划编译与执行：直接回放 IR 步骤，产出全 Blocked 结果（无真实证据）。"""

    @property
    def backend_id(self) -> str:
        return "fake_execution"

    @property
    def backend_version(self) -> str:
        return "1.0.0"

    def compile_plan(self, test_ir: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        import hashlib

        ir_text = json.dumps(test_ir, sort_keys=True, ensure_ascii=False)
        return {
            "compiler_version": self.backend_version,
            "ir_hash": hashlib.sha256(ir_text.encode("utf-8")).hexdigest(),
            "commands": [
                {"seq": i, "action": s.get("action"), "params": dict(s)}
                for i, s in enumerate(test_ir.get("steps", []), start=1)
            ],
            "cleanup": [{"action": "release_resources"}],
        }

    def execute(
        self, plan: Mapping[str, Any], event_sink: Callable[[Mapping[str, Any]], None]
    ) -> Mapping[str, Any]:
        for cmd in plan.get("commands", []):
            event_sink({"type": "step", **cmd})
        event_sink({"type": "finished"})
        return {
            "state": "Completed",
            "steps_total": len(plan.get("commands", [])),
            "note": "Fake 执行无真实证据，断言结果应为 Blocked（由 JUDGE-01 判定）",
        }

    def cancel(self, run_id: str) -> bool:
        return True


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


class ScriptedLLMProvider(LLMProvider):
    """脚本化 Provider：按预设顺序返回固定应答（评测与测试可复现）。"""

    def __init__(self, responses: Sequence[str] | None = None) -> None:
        # 注意：空列表是合法输入（表示“无预设应答”），不能用 or 兜底
        self._responses = list(responses) if responses is not None else ['{"schema_version": "1.0"}']
        self.calls: list[dict[str, Any]] = []

    @property
    def provider_id(self) -> str:
        return "scripted"

    def list_models(self) -> tuple[str, ...]:
        return ("scripted-1",)

    def _next(self) -> str:
        if not self._responses:
            raise LLMError(LLMErrorKind.INVALID_OUTPUT, "脚本应答已耗尽", retryable=False)
        return self._responses.pop(0)

    def complete(
        self, messages: Sequence[Mapping[str, str]], *, model: str, **kwargs: Any
    ) -> LLMResult:
        self.calls.append({"model": model, "messages": list(messages)})
        return LLMResult(text=self._next(), model=model, usage=LLMUsage(10, 20))

    def structured_output(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str,
        json_schema: Mapping[str, Any],
        **kwargs: Any,
    ) -> LLMResult:
        text = self._next()
        self.calls.append({"model": model, "structured": True})
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(LLMErrorKind.INVALID_OUTPUT, f"非 JSON 输出: {exc}", retryable=True) from exc
        return LLMResult(text=text, model=model, usage=LLMUsage(10, 30))


# ---------------------------------------------------------------------------
# Report exporter
# ---------------------------------------------------------------------------


class FakeReportExporter(ReportExporter):
    """把 run_result 序列化为 JSON 报告（HTML/PDF 在 REPORT-01 实现）。"""

    @property
    def exporter_id(self) -> str:
        return "fake_report"

    @property
    def report_format(self) -> str:
        return "json"

    def export(
        self,
        run_result: Mapping[str, Any],
        evidence_paths: Sequence[str],
        output_path: Path,
    ) -> ExportResult:
        import hashlib

        payload = json.dumps(
            {"run_result": run_result, "evidence": list(evidence_paths)},
            ensure_ascii=False,
            indent=2,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
        sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return ExportResult(exporter_id=self.exporter_id, output_path=str(output_path), sha256=sha)
