"""DATA-01：NetworkModel 查询接口（查找 / decode / encode）。

约定：
- 只依赖本项目 NetworkModel；返回值全部为项目类型或 Python 内建类型，
  不持有、不返回任何三方解析库（cantools）对象；
- ``decode_message`` 输出物理值（原始值 * scale + offset），值表标签通过
  ``Signal.choices`` 查询，不在此处翻译成文本；
- ``encode_message`` 对未给出的信号按原始值 0 编码；超出信号位宽可表示
  范围时抛 ``ValueError``（不静默截断）；
- 位布局实现（intel/motorola、有/无符号、跨字节）与 cantools 的交叉验证
  见 tests/unit/test_dbc_parser.py。
"""

from __future__ import annotations

from typing import Mapping

from zlg_ai_test_engine.domain.network.model import Message, NetworkModel, Node, Signal
from zlg_ai_test_engine.domain.plugins.contracts import ParseResult

__all__ = ["NetworkQueries"]


def _extract_raw(data: bytes, start: int, bit_length: int, byte_order: str) -> int:
    """按 DBC 位布局从帧数据提取原始整数值（未做符号/换算处理）。"""
    raw = 0
    if byte_order == "intel":
        # little-endian：start 为最低有效位，线性向高位扩展
        for i in range(bit_length):
            byte_index, bit_index = divmod(start + i, 8)
            if byte_index >= len(data):
                raise ValueError(f"数据长度 {len(data)} 字节不足以覆盖位 {start + i}")
            raw |= ((data[byte_index] >> bit_index) & 1) << i
    else:
        # motorola（big-endian）：start 为最高有效位（DBC 锯齿位编号），
        # 位序递减，跨字节边界时跳到下一字节的最高位
        pos = start
        for i in range(bit_length):
            byte_index, bit_index = divmod(pos, 8)
            if byte_index >= len(data):
                raise ValueError(f"数据长度 {len(data)} 字节不足以覆盖位 {pos}")
            raw = (raw << 1) | ((data[byte_index] >> bit_index) & 1)
            pos = pos - 1 if pos % 8 else pos + 15
    return raw


def _insert_raw(buf: bytearray, start: int, bit_length: int, byte_order: str, raw: int) -> None:
    """按 DBC 位布局把原始整数值写入帧缓冲。"""
    if byte_order == "intel":
        for i in range(bit_length):
            byte_index, bit_index = divmod(start + i, 8)
            if byte_index >= len(buf):
                raise ValueError(f"报文长度 {len(buf)} 字节不足以覆盖位 {start + i}")
            mask = 1 << bit_index
            if (raw >> i) & 1:
                buf[byte_index] |= mask
            else:
                buf[byte_index] &= ~mask & 0xFF
    else:
        pos = start
        for i in range(bit_length):
            byte_index, bit_index = divmod(pos, 8)
            if byte_index >= len(buf):
                raise ValueError(f"报文长度 {len(buf)} 字节不足以覆盖位 {pos}")
            mask = 1 << bit_index
            if (raw >> (bit_length - 1 - i)) & 1:
                buf[byte_index] |= mask
            else:
                buf[byte_index] &= ~mask & 0xFF
            pos = pos - 1 if pos % 8 else pos + 15


class NetworkQueries:
    """对 NetworkModel 的只读查询与编解码。

    frame_id 兼容两种形式：裸 CAN ID（与 ``Message.frame_id`` 相同），
    以及带扩展标志位 0x80000000 的原始 DBC 帧 ID（仅当目标报文确为扩展帧
    时命中）。
    """

    def __init__(self, model: NetworkModel) -> None:
        self._model = model

    @classmethod
    def from_parse_result(cls, parse_result: ParseResult) -> "NetworkQueries":
        """从 ParseResult 重建查询对象（顺带校验 model 为纯 dict）。"""
        return cls(NetworkModel.model_validate(parse_result.model))

    @property
    def model(self) -> NetworkModel:
        return self._model

    # -- 查找 ----------------------------------------------------------------

    def list_nodes(self) -> tuple[Node, ...]:
        """全部节点（按模型中的声明顺序）。"""
        return tuple(self._model.nodes)

    def find_message_by_name(self, message_name: str) -> Message | None:
        """按报文名查找；未命中返回 None。"""
        return self._model.find_message(message_name)

    def find_message_by_frame_id(self, frame_id: int) -> Message | None:
        """按帧 ID 查找（兼容带扩展标志位的原始 DBC ID）；未命中返回 None。"""
        for message in self._model.messages:
            if message.frame_id == frame_id:
                return message
            if message.is_extended and message.frame_id == (frame_id & 0x7FFFFFFF):
                return message
        return None

    def find_signal(self, message_name: str, signal_name: str) -> Signal | None:
        """按 报文名+信号名 查找信号；未命中返回 None。"""
        return self._model.find_signal(message_name, signal_name)

    # -- 编解码 ---------------------------------------------------------------

    def decode_message(self, frame_id: int, data: bytes) -> dict[str, float]:
        """把一帧数据解码为 {信号名: 物理值}。

        - frame_id 未命中：``KeyError``；
        - 数据长度不等于报文 DLC：``ValueError``（与基准实现语义一致）。
        """
        message = self._require_message(frame_id)
        if len(data) != message.dlc:
            raise ValueError(
                f"报文 {message.message_name} 的 DLC 为 {message.dlc}，"
                f"实际数据长度 {len(data)}"
            )
        decoded: dict[str, float] = {}
        for signal in message.signals:
            raw = _extract_raw(data, signal.start, signal.bit_length, signal.byte_order)
            if signal.is_signed and raw >= (1 << (signal.bit_length - 1)):
                raw -= 1 << signal.bit_length
            decoded[signal.signal_name] = raw * signal.scale + signal.offset
        return decoded

    def encode_message(self, frame_id: int, values: Mapping[str, int | float]) -> bytes:
        """把 {信号名: 物理值} 编码为帧数据。

        - frame_id 未命中或 values 含未知信号名：``KeyError``（不静默忽略）；
        - 值换算后超出信号位宽可表示范围：``ValueError``（不静默截断）；
        - 未出现在 values 中的信号按原始值 0 编码。
        """
        message = self._require_message(frame_id)
        known = {signal.signal_name for signal in message.signals}
        unknown = [name for name in values if name not in known]
        if unknown:
            raise KeyError(
                f"报文 {message.message_name} 不含信号: {', '.join(sorted(unknown))}"
            )
        buf = bytearray(message.dlc)
        for signal in message.signals:
            if signal.signal_name in values:
                value = values[signal.signal_name]
                if signal.scale == 0:
                    raise ValueError(
                        f"信号 {signal.signal_name} 的 scale 为 0，无法从物理值反算"
                    )
                raw = round((value - signal.offset) / signal.scale)
            else:
                raw = 0
            if signal.is_signed and raw < 0:
                raw += 1 << signal.bit_length
            if not 0 <= raw < (1 << signal.bit_length):
                raise ValueError(
                    f"信号 {signal.signal_name} 的值 {value!r} 超出 "
                    f"{signal.bit_length} 位{'有' if signal.is_signed else '无'}符号"
                    f"可表示范围"
                )
            _insert_raw(buf, signal.start, signal.bit_length, signal.byte_order, raw)
        return bytes(buf)

    # -- 内部 -----------------------------------------------------------------

    def _require_message(self, frame_id: int) -> Message:
        message = self.find_message_by_frame_id(frame_id)
        if message is None:
            raise KeyError(f"未知 frame_id: 0x{frame_id & 0xFFFFFFFF:X}")
        return message
