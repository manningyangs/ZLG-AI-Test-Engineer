"""ARCH-01：插件与能力模型契约（六维扩展解耦的统一接口）。

覆盖任务书 2.3 节六个扩展维度中的五类执行相关契约 + 报告导出：
ProtocolPlugin / DeviceSDKAdapter / AssetParser / ScriptBackend /
ExecutionBackend / LLMProvider / ReportExporter。

架构红线（任务书 2.3）：
- ProtocolPlugin 不允许直接调用厂商 DLL；
- Parser 不允许操作设备；
- LLMProvider 不允许执行工具动作；
- UI 不允许判定 ECU 结果；
- Native Host 不负责推理测试预期。

本模块为纯领域层：不依赖 Qt、不依赖具体厂商 SDK、不执行任何 IO。
所有能力声明以“契约 + Fake”先行，真实实现见 P3（SDK Adapter）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# ---------------------------------------------------------------------------
# 插件 API 版本与兼容策略
# ---------------------------------------------------------------------------

#: 插件契约 API 版本。语义：major 变更不兼容，minor 向后兼容。
PLUGIN_API_VERSION = "1.0"


def api_compatible(plugin_api_version: str) -> bool:
    """判断插件声明的 api_version 是否与当前契约兼容（major 相同即兼容）。"""
    try:
        plugin_major = plugin_api_version.split(".")[0]
        host_major = PLUGIN_API_VERSION.split(".")[0]
    except (AttributeError, IndexError):
        return False
    return plugin_major == host_major


# ---------------------------------------------------------------------------
# 通用值对象
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """解析/校验诊断的严重级别。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Diagnostic:
    """解析或校验过程中的诊断信息（不允许静默丢弃问题）。"""

    code: str
    message: str
    severity: Severity = Severity.WARNING
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "location": self.location,
        }


class RiskLevel(str, Enum):
    """安全风险等级（任务书第 13 章）。"""

    L0 = "L0"  # 只读
    L1 = "L1"  # 受控发送
    L2 = "L2"  # 故障注入
    L3 = "L3"  # 诊断写入/标定
    L4 = "L4"  # 高风险（MVP 禁止）


@dataclass(frozen=True)
class DeviceInfo:
    """设备身份信息（字段按 SDK 实际可读情况在 P3 填充，此处仅契约）。"""

    device_id: str
    model: str | None = None
    serial: str | None = None
    channel_count: int | None = None
    hardware_version: str | None = None
    firmware_version: str | None = None
    driver_version: str | None = None
    sdk_version: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelConfig:
    """通道初始化配置（调用 SDK 前必须通过 VALID-01 参数校验）。"""

    channel_index: int
    bus_type: str  # "CAN" | "CAN_FD"
    bitrate_arbitration: int
    bitrate_data: int | None = None  # CAN FD 数据域
    listen_only: bool = False
    filter_config: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "channel_index": self.channel_index,
            "bus_type": self.bus_type,
            "bitrate_arbitration": self.bitrate_arbitration,
            "bitrate_data": self.bitrate_data,
            "listen_only": self.listen_only,
            "filter_config": dict(self.filter_config),
        }


@dataclass(frozen=True)
class Frame:
    """一帧总线数据。三个时间基准分别保存，绝不混用（任务书 12.2）。"""

    channel: int
    can_id: int
    data: bytes
    is_fd: bool = False
    is_extended: bool = False
    #: 设备时间戳（保留原始单位）
    device_timestamp: int | None = None
    device_timestamp_unit: str | None = None
    #: 主机单调时钟（纳秒）
    host_monotonic_ns: int | None = None
    #: 系统墙上时间（epoch 秒）
    wall_clock: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "can_id": self.can_id,
            "data_hex": self.data.hex(),
            "is_fd": self.is_fd,
            "is_extended": self.is_extended,
            "device_timestamp": self.device_timestamp,
            "device_timestamp_unit": self.device_timestamp_unit,
            "host_monotonic_ns": self.host_monotonic_ns,
            "wall_clock": self.wall_clock,
        }


@dataclass(frozen=True)
class SendResult:
    """发送结果：API 返回成功不等同于 ECU 已正确响应。"""

    requested: int
    accepted: int
    error_code: int | None = None
    tx_echo: Frame | None = None


@dataclass(frozen=True)
class EmergencyStopReport:
    """急停执行结果：各清理项单独记录（任务书 10.3/13）。"""

    sends_stopped: bool
    queues_cleared: bool
    channels_reset: bool
    device_closed: bool
    details: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 契约 1：ProtocolPlugin —— 协议语义（不碰 DLL）
# ---------------------------------------------------------------------------


class ProtocolPlugin(ABC):
    """通信协议插件：协议标识、编解码、动作参数 Schema 与校验。"""

    @property
    @abstractmethod
    def protocol_id(self) -> str:
        """协议标识，如 "CAN"、"UDS"、"SOMEIP"。"""

    @property
    @abstractmethod
    def protocol_version(self) -> str:
        """协议实现版本。"""

    @property
    @abstractmethod
    def supported_transports(self) -> tuple[str, ...]:
        """适用的传输层，如 ("CAN", "CAN_FD")。"""

    @abstractmethod
    def decode(self, payload: bytes, database_context: Mapping[str, Any]) -> Mapping[str, Any]:
        """将帧/载荷解码为语义数据（依赖数据库上下文）。"""

    @abstractmethod
    def encode(
        self, message_ref: str, values: Mapping[str, Any], database_context: Mapping[str, Any]
    ) -> bytes:
        """将语义数据编码为帧/载荷。"""

    @abstractmethod
    def validate_step(
        self, step: Mapping[str, Any], database_context: Mapping[str, Any]
    ) -> tuple[bool, tuple[Diagnostic, ...]]:
        """校验测试步骤对本协议是否合法。"""

    @abstractmethod
    def action_schemas(self) -> Mapping[str, Mapping[str, Any]]:
        """动作类型 → JSON Schema（AI 输出白名单的协议侧定义）。"""

    @abstractmethod
    def required_device_capabilities(self) -> frozenset[str]:
        """本协议动作要求的设备能力标识集合（供 VALID-01 检查）。"""


# ---------------------------------------------------------------------------
# 契约 2：DeviceSDKAdapter —— 设备与通道（唯一可触碰设备的一层）
# ---------------------------------------------------------------------------


class DeviceSDKAdapter(ABC):
    """设备 SDK 适配器：发现、生命周期、通道、收发、能力、急停。

    实现方负责独占管理设备句柄与接收线程；UI/AI 不得绕过本接口。
    """

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """适配器标识，如 "mock"、"zlgcan"、"zxdoc"。"""

    @property
    @abstractmethod
    def adapter_version(self) -> str:
        """适配器实现版本（写入执行记录，保证可复现）。"""

    @abstractmethod
    def discover_devices(self) -> Sequence[DeviceInfo]:
        """扫描已连接设备；无法读取时应给出明确错误而非返回空。"""

    @abstractmethod
    def get_device_info(self, device_id: str) -> DeviceInfo:
        """读取指定设备信息。"""

    @abstractmethod
    def open_device(self, device_id: str) -> None:
        """打开设备；设备被占用必须抛出明确异常。"""

    @abstractmethod
    def close_device(self, device_id: str) -> None:
        """关闭设备并释放句柄（无论成功/失败/取消都需可调用）。"""

    @abstractmethod
    def configure_channel(self, device_id: str, config: ChannelConfig) -> None:
        """校验并下发通道配置。"""

    @abstractmethod
    def start_channel(self, device_id: str, channel_index: int) -> None:
        """启动通道（多通道按厂商要求线性初始化由实现方保证）。"""

    @abstractmethod
    def stop_channel(self, device_id: str, channel_index: int) -> None:
        """停止通道。"""

    @abstractmethod
    def send_frame(self, device_id: str, frame: Frame) -> SendResult:
        """发送一帧/有限次数报文（调用前必须已通过 L1+ 审批）。"""

    @abstractmethod
    def start_receiving(self, device_id: str, callback: Callable[[Frame], None]) -> None:
        """启动接收（实现方须使用独立线程与有界队列）。"""

    @abstractmethod
    def stop_receiving(self, device_id: str) -> None:
        """停止接收并 join 线程。"""

    @abstractmethod
    def device_capabilities(self, device_id: str) -> Mapping[str, Any]:
        """返回当前设备能力集（Capability Profile 视图，含证据等级）。"""

    @abstractmethod
    def is_capable(self, device_id: str, capability: str) -> bool:
        """能力判断；未知能力必须返回 False（未知 ≠ 支持）。"""

    @abstractmethod
    def emergency_stop(self, device_id: str) -> EmergencyStopReport:
        """紧急停止：停发送、清队列/定时（按能力）、复位通道、释放资源。"""


# ---------------------------------------------------------------------------
# 契约 3：AssetParser —— 工程文件解析（不碰设备）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParseResult:
    """解析结果：统一模型 + 全部诊断（未识别字段必须生成警告）。"""

    parser_id: str
    parser_version: str
    model: Mapping[str, Any]
    diagnostics: tuple[Diagnostic, ...] = ()
    source_sha256: str | None = None


class AssetParser(ABC):
    """工程文件/记录格式解析器（DBC/ODX/LDF/ARXML/A2L/ASC…）。"""

    @property
    @abstractmethod
    def parser_id(self) -> str:
        """解析器标识，如 "dbc"、"odx"。"""

    @property
    @abstractmethod
    def parser_version(self) -> str:
        """解析器版本（写入解析记录，保证可复现）。"""

    @property
    @abstractmethod
    def supported_file_types(self) -> tuple[str, ...]:
        """支持的扩展名（小写、含点），如 (".dbc",)。"""

    @abstractmethod
    def parse(self, path: Path) -> ParseResult:
        """解析文件为统一模型；损坏文件需给出定位清楚的诊断。"""


# ---------------------------------------------------------------------------
# 契约 4/5：ScriptBackend 与 ExecutionBackend（严禁混为一个接口）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptArtifact:
    """生成的可审查代码/计划文本（仅预览与导出，不默认执行）。"""

    backend_id: str
    language: str
    code: str
    ir_hash: str
    warnings: tuple[str, ...] = ()


class ScriptBackend(ABC):
    """脚本后端：把 Test IR 生成可审查的代码（ZLGCAN Python/ZXDoc Python…）。"""

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """后端标识，如 "zlgcan_python"、"zxdoc_python"。"""

    @property
    @abstractmethod
    def backend_version(self) -> str: ...

    @property
    @abstractmethod
    def target_language(self) -> str: ...

    @abstractmethod
    def generate(self, test_ir: Mapping[str, Any], context: Mapping[str, Any]) -> ScriptArtifact:
        """由 Test IR 生成脚本；不得在生成阶段执行任何代码。"""


class ExecutionBackend(ABC):
    """执行后端：把 Test IR 编译为执行计划并运行（Mock / 设备）。"""

    @property
    @abstractmethod
    def backend_id(self) -> str: ...

    @property
    @abstractmethod
    def backend_version(self) -> str: ...

    @abstractmethod
    def compile_plan(self, test_ir: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        """确定性编译：同一 IR + 同一版本配置必须生成稳定计划（禁用 LLM）。"""

    @abstractmethod
    def execute(
        self, plan: Mapping[str, Any], event_sink: Callable[[Mapping[str, Any]], None]
    ) -> Mapping[str, Any]:
        """执行计划；事件经 event_sink 上报；返回执行结果摘要。"""

    @abstractmethod
    def cancel(self, run_id: str) -> bool:
        """取消运行；返回是否已受理。"""


# ---------------------------------------------------------------------------
# 契约 6：LLMProvider —— 模型调用（不执行工具动作）
# ---------------------------------------------------------------------------


class LLMErrorKind(str, Enum):
    """模型调用错误分类（AI-01 要求可恢复性判断）。"""

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    NETWORK = "network"
    UNKNOWN = "unknown"


class LLMError(Exception):
    """模型调用异常，带错误分类与是否可重试标记。"""

    def __init__(self, kind: LLMErrorKind, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


@dataclass(frozen=True)
class LLMUsage:
    """Token 用量（成本统计可选）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None


@dataclass(frozen=True)
class LLMResult:
    """模型返回：文本或结构化 JSON，附使用量。"""

    text: str
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str | None = None


class LLMProvider(ABC):
    """大模型 Provider 抽象（OpenAI 兼容/国内/本地端点）。

    红线：只做文本/结构化输出，不执行工具、不访问文件系统、不触碰设备。
    API Key 必须来自 SecretStore，禁止由调用方明文传入日志可及范围。
    """

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    def list_models(self) -> tuple[str, ...]:
        """可用模型列表。"""

    @abstractmethod
    def complete(
        self, messages: Sequence[Mapping[str, str]], *, model: str, **kwargs: Any
    ) -> LLMResult:
        """普通对话补全。"""

    @abstractmethod
    def structured_output(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str,
        json_schema: Mapping[str, Any],
        **kwargs: Any,
    ) -> LLMResult:
        """要求输出符合 JSON Schema 的结构化结果（校验失败抛 INVALID_OUTPUT）。"""


# ---------------------------------------------------------------------------
# 契约 7：ReportExporter —— 报告导出（只读证据）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportResult:
    exporter_id: str
    output_path: str
    sha256: str | None = None
    warnings: tuple[str, ...] = ()


class ReportExporter(ABC):
    """报告导出器：输入标准化 RunResult/Evidence，输出 HTML/PDF 等。"""

    @property
    @abstractmethod
    def exporter_id(self) -> str: ...

    @property
    @abstractmethod
    def report_format(self) -> str:
        """输出格式标识，如 "html"、"pdf"。"""

    @abstractmethod
    def export(
        self,
        run_result: Mapping[str, Any],
        evidence_paths: Sequence[str],
        output_path: Path,
    ) -> ExportResult:
        """导出报告；只读证据，不得修改/覆盖原始证据文件。"""
