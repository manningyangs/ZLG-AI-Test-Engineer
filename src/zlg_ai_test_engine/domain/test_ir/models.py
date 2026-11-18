"""Test IR v1 Pydantic 模型（CORE-01）。

与 schemas/test-ir/v1/test_ir.schema.json 保持一致（有一致性测试保证）：
- JSON Schema 负责结构与取值范围；
- 本模块补充 JSON Schema 难以表达的语义校验：
  * assert_range 的 min < max；
  * payload 字节数 == dlc；
  * 非 FD 帧载荷 ≤ 8 字节、FD 帧 ≤ 64 字节。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 当前 IR schema 版本
IR_SCHEMA_VERSION = "1.0"

_ID_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"
_HEX_PAYLOAD_PATTERN = r"^([0-9a-fA-F]{2}){0,64}$"


class _IRBase(BaseModel):
    """禁止未知字段（防止拼写错误静默通过）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# 值对象
# ---------------------------------------------------------------------------


class MessageRef(_IRBase):
    asset_id: str | None = None
    message: str = Field(min_length=1)


class ProtocolRef(_IRBase):
    bus: Literal["CAN", "CAN_FD"]
    database: str | None = Field(default=None, min_length=1)


class TargetRef(_IRBase):
    message: str | None = None
    signal: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "TargetRef":
        if not self.message and not self.signal:
            raise ValueError("target 至少包含 message 或 signal")
        return self


class DatabaseRef(_IRBase):
    asset_id: str = Field(min_length=1)
    message: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class FrameFilter(_IRBase):
    can_id: int | None = Field(default=None, ge=0, le=0x1FFFFFFF)
    mask: int | None = Field(default=None, ge=0)


# -- 前置条件（按 action 判别） ---------------------------------------------


class PreconditionDeviceReady(_IRBase):
    action: Literal["device_ready"]
    channel: int | None = Field(default=None, ge=0)


class PreconditionMeasurementStarted(_IRBase):
    action: Literal["measurement_started"]


class PreconditionListenOnly(_IRBase):
    action: Literal["listen_only"]
    enabled: bool


Precondition = Annotated[
    PreconditionDeviceReady | PreconditionMeasurementStarted | PreconditionListenOnly,
    Field(discriminator="action"),
]


# -- 执行策略 / 溯源 / 证据 --------------------------------------------------


class ExecutionPolicy(_IRBase):
    mode: Literal["offline_only", "offline_then_hardware", "hardware"]
    risk_level: Literal["L0", "L1", "L2", "L3", "L4"]
    requires_approval: bool = False
    max_duration_ms: int | None = Field(default=None, gt=0)
    max_frames: int | None = Field(default=None, ge=1)
    target_adapter: str | None = None
    target_capabilities: list[str] = Field(default_factory=list)


class Provenance(_IRBase):
    requirement_refs: list[str] = Field(default_factory=list)
    database_hash: str | None = Field(
        default=None, pattern=r"^(sha256:)?[0-9a-fA-F]{64}$"
    )
    model: str | None = None
    rule_version: str | None = None
    confirmed_by: str | None = None
    generated_at: str | None = None


class ExpectedEvidence(_IRBase):
    type: Literal["raw_frame", "timestamp", "signal_series", "log", "screenshot", "report"]
    description: str | None = None


# ---------------------------------------------------------------------------
# 测试步骤（按 action 判别的联合）
# ---------------------------------------------------------------------------


class StepBase(_IRBase):
    id: str = Field(pattern=_ID_PATTERN)
    description: str | None = None
    timeout_ms: int | None = Field(default=None, ge=0)


def _check_payload_against_fd(is_fd: bool, payload_hex: str, dlc: int | None) -> None:
    """载荷字节数与 FD/DLC 约束（JSON Schema 难以表达相等关系，此处补充）。"""
    byte_count = len(payload_hex) // 2
    limit = 64 if is_fd else 8
    if byte_count > limit:
        raise ValueError(f"载荷 {byte_count} 字节超过 {'CAN FD' if is_fd else 'CAN'} 上限 {limit} 字节")
    if dlc is not None and payload_hex and byte_count != dlc:
        raise ValueError(f"payload 字节数({byte_count})与 dlc({dlc})不一致")


class ListenBusStep(StepBase):
    action: Literal["listen_bus"]
    duration_ms: int = Field(gt=0)
    filter: FrameFilter | None = None


class SendFrameStep(StepBase):
    action: Literal["send_frame"]
    message_ref: MessageRef | None = None
    can_id: int | None = Field(default=None, ge=0, le=0x1FFFFFFF)
    is_fd: bool = False
    is_extended: bool | None = None
    payload_hex: str | None = Field(default=None, pattern=_HEX_PAYLOAD_PATTERN)
    dlc: int | None = Field(default=None, ge=0, le=64)
    count: int | None = Field(default=None, ge=1, le=10000)

    @model_validator(mode="after")
    def _require_ref_or_frame(self) -> "SendFrameStep":
        if self.message_ref is None and (self.can_id is None or self.payload_hex is None):
            raise ValueError("send_frame 需要 message_ref 或 (can_id + payload_hex)")
        if self.payload_hex is not None:
            _check_payload_against_fd(self.is_fd, self.payload_hex, self.dlc)
        return self


class SendPeriodicStep(StepBase):
    action: Literal["send_periodic"]
    message_ref: MessageRef | None = None
    can_id: int | None = Field(default=None, ge=0, le=0x1FFFFFFF)
    is_fd: bool = False
    payload_hex: str | None = Field(default=None, pattern=_HEX_PAYLOAD_PATTERN)
    dlc: int | None = Field(default=None, ge=0, le=64)
    period_ms: int = Field(gt=0, description="周期必须为正（负/零周期非法）")
    start_delay_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, gt=0)
    count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_ref_or_frame(self) -> "SendPeriodicStep":
        if self.message_ref is None and (self.can_id is None or self.payload_hex is None):
            raise ValueError("send_periodic 需要 message_ref 或 (can_id + payload_hex)")
        if self.payload_hex is not None:
            _check_payload_against_fd(self.is_fd, self.payload_hex, self.dlc)
        return self


class MonitorSignalStep(StepBase):
    action: Literal["monitor_signal"]
    asset_id: str | None = None
    message: str = Field(min_length=1)
    signal: str = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    sample_ms: int | None = Field(default=None, gt=0)


class WaitForFrameStep(StepBase):
    action: Literal["wait_for_frame"]
    message: str | None = Field(default=None, min_length=1)
    can_id: int | None = Field(default=None, ge=0, le=0x1FFFFFFF)
    timeout_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _require_target(self) -> "WaitForFrameStep":
        if self.message is None and self.can_id is None:
            raise ValueError("wait_for_frame 需要 message 或 can_id")
        return self


class AssertCycleStep(StepBase):
    action: Literal["assert_cycle"]
    message: str = Field(min_length=1)
    expected_period_ms: int = Field(gt=0)
    tolerance_ms: float = Field(ge=0)
    evidence_source: str = Field(min_length=1, description="阈值来源，不得为空")


class AssertRangeStep(StepBase):
    action: Literal["assert_range"]
    signal: str = Field(min_length=1)
    min: float
    max: float
    unit: str | None = None
    evidence_source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _min_lt_max(self) -> "AssertRangeStep":
        if self.min >= self.max:
            raise ValueError(f"assert_range 的 min({self.min}) 必须小于 max({self.max})")
        return self


class AssertEnumStep(StepBase):
    action: Literal["assert_enum"]
    signal: str = Field(min_length=1)
    allowed_values: list[int | str] = Field(min_length=1)
    evidence_source: str = Field(min_length=1)


class AssertTimeoutStep(StepBase):
    action: Literal["assert_timeout"]
    message: str = Field(min_length=1)
    timeout_ms: int = Field(gt=0)
    evidence_source: str | None = None
    requires_fault_injection: bool = False


class InjectFaultStep(StepBase):
    """预留动作：结构合法，validate_executable 默认阻断（L2）。"""

    action: Literal["inject_fault"]
    fault_type: Literal["drop_frame", "corrupt_data", "delay_frames", "error_frame"]
    target: str = Field(min_length=1)
    duration_ms: int = Field(gt=0)


class UdsRequestStep(StepBase):
    """预留动作：结构合法，validate_executable 默认阻断（L3，1.2 版本启用）。"""

    action: Literal["uds_request"]
    transport: Literal["DoCAN"]
    service: str = Field(pattern=r"^(0x)?[0-9A-Fa-f]{2}$")
    payload_hex: str | None = Field(default=None, pattern=_HEX_PAYLOAD_PATTERN)
    timeout_ms: int = Field(gt=0)


TestStep = Annotated[
    (
        ListenBusStep
        | SendFrameStep
        | SendPeriodicStep
        | MonitorSignalStep
        | WaitForFrameStep
        | AssertCycleStep
        | AssertRangeStep
        | AssertEnumStep
        | AssertTimeoutStep
        | InjectFaultStep
        | UdsRequestStep
    ),
    Field(discriminator="action"),
]


# ---------------------------------------------------------------------------
# 根文档
# ---------------------------------------------------------------------------


class TestIRDocument(_IRBase):
    """Test IR 根对象（v1.0）。"""

    schema_version: Literal["1.0"] = IR_SCHEMA_VERSION
    suite_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    suite_name: str | None = Field(default=None, max_length=255)
    case_id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=255)
    project_id: str | None = None
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    protocol: ProtocolRef
    target: TargetRef | None = None
    database_refs: list[DatabaseRef] = Field(default_factory=list)
    preconditions: list[Precondition] = Field(default_factory=list)
    steps: list[TestStep] = Field(min_length=1)
    execution_policy: ExecutionPolicy
    expected_result: str | None = Field(default=None, min_length=1)
    provenance: Provenance | None = None
    expected_evidence: list[ExpectedEvidence] = Field(default_factory=list)
    unconfirmed_items: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_step_ids(self) -> "TestIRDocument":
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("步骤 id 重复")
        return self
