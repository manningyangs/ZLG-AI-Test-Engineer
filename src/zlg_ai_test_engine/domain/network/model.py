"""DATA-01：协议无关的统一 NetworkModel（AI 与测试设计的工程上下文）。

设计原则：
- 本模型是解析结果的**唯一**领域表达：ParseResult.model 只允许本模块类型
  序列化出的纯 dict（str/int/float/bool/None/list/dict），任何三方解析库
  （cantools 等）对象不得进入领域层；
- 字段取 DBC 等源格式的公共语义交集（frame_id 存放不含扩展标志位的裸 ID，
  是否扩展帧由 ``is_extended`` 表达）；
- 与源格式多对一映射：源格式中不可表达的构造由解析器生成诊断
  （见 docs/parsers/dbc.md），绝不静默丢弃。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _NetworkBase(BaseModel):
    """禁止未知字段（防止拼写错误静默通过）。"""

    model_config = ConfigDict(extra="forbid")


class Node(_NetworkBase):
    """总线节点（网络节点，如 ECU）。"""

    node_name: str
    comment: str | None = None


class Signal(_NetworkBase):
    """信号（位布局 + 物理换算 + 值表）。

    - ``start``/``bit_length``：DBC 语义。intel（little-endian）信号的
      ``start`` 为最低有效位位置；motorola（big-endian）信号的 ``start``
      为最高有效位位置（DBC 锯齿位编号）；
    - ``scale``/``offset``：物理值 = 原始值 * scale + offset；
    - ``initial_value``：物理域初值（源格式 GenSigStartValue 的原始值经
      scale/offset 换算），无则 None；
    - ``choices``：原始值 → 文本标签（值表），无值表为空 dict。
    """

    signal_name: str
    start: int
    bit_length: int
    byte_order: Literal["intel", "motorola"]
    is_signed: bool
    initial_value: float | None = None
    scale: float
    offset: float
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None
    receivers: list[str] = []
    choices: dict[int, str] = {}
    comment: str | None = None


class Message(_NetworkBase):
    """报文（帧）及其信号集合。"""

    message_name: str
    frame_id: int
    is_extended: bool
    dlc: int
    sender: str | None = None
    cycle_time_ms: int | None = None
    comment: str | None = None
    signals: list[Signal] = []


class NetworkModel(_NetworkBase):
    """统一网络模型根：一次解析的完整、确定性结果。

    - ``database_name``：数据库名（DBC 无显式名称字段时取文件名主干）；
    - ``version``：源文件 VERSION 语义，缺失为 None；
    - ``sha256``：源文件字节的 SHA-256（与 ParseResult.source_sha256 一致）；
    - ``parser_id``/``parser_version``：产出本结果的解析器身份（可复现性）。
    """

    database_name: str
    version: str | None = None
    nodes: list[Node]
    messages: list[Message]
    sha256: str
    parser_id: str = "dbc"
    parser_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        """序列化为纯 dict（ParseResult.model 的唯一来源）。

        用 python 模式保留 choices 的 int 键（JSON 模式会把 int 键转字符串）。
        """
        return self.model_dump()

    def find_message(self, message_name: str) -> Message | None:
        """按名称查找报文；未命中返回 None。"""
        for message in self.messages:
            if message.message_name == message_name:
                return message
        return None

    def find_signal(self, message_name: str, signal_name: str) -> Signal | None:
        """按 报文名+信号名 查找信号；任一层未命中返回 None。"""
        message = self.find_message(message_name)
        if message is None:
            return None
        for signal in message.signals:
            if signal.signal_name == signal_name:
                return signal
        return None

    @property
    def node_names(self) -> tuple[str, ...]:
        """全部节点名（声明顺序）。"""
        return tuple(node.node_name for node in self.nodes)

