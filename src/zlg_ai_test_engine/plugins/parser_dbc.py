"""DATA-01：DBC 解析器插件（AssetParser 实现）。

职责边界：
- 内部用 cantools（MIT，已随项目安装）完成 DBC 语法解析，**输出只含本项目
  NetworkModel 序列化出的纯 dict**——cantools 对象绝不进入 ParseResult.model
  或任何对外返回值；
- 附带轻量文本扫描器：把 cantools 能容忍但 NetworkModel 不表达的构造、
  cantools 静默忽略的引用错误（如 VAL_ 指向不存在的信号）、以及完全无法识别
  的内容逐条生成诊断（WARNING / ERROR），绝不静默丢弃；
- 损坏文件 → ERROR 诊断，location 形如 ``文件名:L行:C列``（定位清楚）。

诊断代码（完整语义见 docs/parsers/dbc.md）：
- ``parse_error``（ERROR）：cantools 无法解析；
- ``unrecognized_content``（WARNING）：无法识别的 DBC 行；
- ``unsupported_feature``（WARNING）：标准 DBC 构造，NetworkModel 未表达；
- ``unmodeled_attribute``（WARNING）：未纳入模型的 BA_ 属性；
- ``orphan_val_table``（WARNING）：VAL_ 引用缺失/被忽略的目标；
- ``encoding_replacement``（WARNING）：cp1252 无法解码的字节被替换；
- ``upstream_warning``（WARNING）：cantools 解析期间的上游告警；
- ``parse_completed``（INFO）：解析耗时、计数与时间戳（文件元信息）。
"""

from __future__ import annotations

import hashlib
import re
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import cantools.database

from zlg_ai_test_engine.domain.network.model import Message, NetworkModel, Node, Signal
from zlg_ai_test_engine.domain.plugins.contracts import (
    AssetParser,
    Diagnostic,
    ParseResult,
    Severity,
)

__all__ = ["DbcParser"]

#: NetworkModel 表达的 DBC 属性名；其余 BA_/BA_DEF_/BA_DEF_DEF_ 一律告警
_MODELED_ATTRIBUTES = frozenset({"GenMsgCycleTime", "GenSigStartValue"})

#: 按行处理的关键字（语句不以分号结束）
_LINE_KEYWORDS = frozenset({"VERSION", "NS_", "BS_", "BU_", "BO_", "SG_"})

#: 以分号结束的语句关键字（与 cantools 45.x tokenizer 对齐）
_STATEMENT_KEYWORDS = frozenset({
    "BA_", "BA_DEF_", "BA_DEF_DEF_", "BA_DEF_DEF_REL_", "BA_DEF_REL_",
    "BA_DEF_SGTYPE_", "BA_REL_", "BA_SGTYPE_", "BO_TX_BU_", "BU_BO_REL_",
    "BU_EV_REL_", "BU_SG_REL_", "CAT_", "CAT_DEF_", "CM_", "ENVVAR_DATA_",
    "EV_", "EV_DATA_", "FILTER", "NS_DESC_", "SG_MUL_VAL_", "SGTYPE_",
    "SGTYPE_VAL_", "SIG_GROUP_", "SIG_TYPE_REF_", "SIG_VALTYPE_",
    "SIGTYPE_VALTYPE_", "VAL_", "VAL_TABLE_",
})

#: cantools 能解析、但 NetworkModel 不表达的关键字 → 中文说明
_UNMODELED_KEYWORDS: dict[str, str] = {
    "VAL_TABLE_": "独立值表（未挂接到信号）",
    "SIG_VALTYPE_": "信号浮点类型声明（模型按整数位布局解释）",
    "SIGTYPE_VALTYPE_": "旧式信号类型声明",
    "SG_MUL_VAL_": "复用信号取值关系",
    "BO_TX_BU_": "报文附加发送节点",
    "SIG_GROUP_": "信号组",
    "SIG_TYPE_REF_": "信号类型引用",
    "SGTYPE_": "信号类型定义",
    "SGTYPE_VAL_": "信号类型取值",
    "CAT_": "类别赋值",
    "CAT_DEF_": "类别定义",
    "FILTER": "过滤器定义",
    "ENVVAR_DATA_": "环境变量数据",
    "EV_": "环境变量",
    "EV_DATA_": "环境变量数据段",
    "BU_BO_REL_": "节点-报文关系",
    "BU_EV_REL_": "节点-环境变量关系",
    "BU_SG_REL_": "节点-信号关系",
    "BA_DEF_REL_": "关系属性定义",
    "BA_DEF_DEF_REL_": "关系属性默认值",
    "BA_REL_": "关系属性赋值",
    "BA_DEF_SGTYPE_": "信号类型属性定义",
    "BA_SGTYPE_": "信号类型属性赋值",
    "NS_DESC_": "命名空间描述（NS_ 块之外出现）",
}

_LINE_KEYWORD_RE = re.compile(r"^([A-Za-z0-9_]+:?)")
_MUX_SIGNAL_RE = re.compile(r"^SG_\s+\S+\s+(M|m\d+)\s*:")
_QUOTED_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_CM_SCOPE_RE = re.compile(r"^CM_\s+(BU_|BO_|SG_|EV_)\b")
_VAL_REF_RE = re.compile(r"^VAL_\s+(?:(\d+)\s+)?([A-Za-z0-9_]+)\s")
_LINE_COL_RE = re.compile(r"at line (\d+), column (\d+)")


def _scan_dbc_text(text: str, filename: str, model: NetworkModel | None) -> list[Diagnostic]:
    """扫描 DBC 原文，找出 NetworkModel 未表达 / 被上游忽略 / 无法识别的内容。

    - ``model`` 为 None（源文件解析失败）时跳过需要模型的交叉核对（VAL_ 引用）；
    - 返回按出现行号稳定排序的诊断列表；同输入必得同输出（确定性）。
    """
    inline: list[tuple[int, Diagnostic]] = []
    deduped: dict[str, tuple[str, int, str]] = {}  # key → (说明, 首次行号, 次数)

    def feature(first_line: int, key: str, description: str) -> None:
        if key not in deduped:
            deduped[key] = (description, first_line, 1)
        else:
            desc, line, count = deduped[key]
            deduped[key] = (desc, line, count + 1)

    lines = text.splitlines()
    in_ns_block = False
    index = 0
    while index < len(lines):
        line_no = index + 1
        stripped = lines[index].lstrip()
        if not stripped or stripped.startswith("//"):
            index += 1
            continue
        if in_ns_block:
            if stripped.startswith("BS_"):
                in_ns_block = False
            index += 1
            continue
        if re.match(r"^NS_\s*:", stripped):  # 注意：不能误吞 NS_DESC_ 等关键字
            in_ns_block = True
            index += 1
            continue

        match = _LINE_KEYWORD_RE.match(stripped)
        first_token = match.group(1).rstrip(":") if match else ""
        statement: str | None = None
        if first_token in _STATEMENT_KEYWORDS:
            statement, index = _read_statement(lines, index)
        elif first_token in _LINE_KEYWORDS:
            statement = stripped
            index += 1
        else:
            inline.append((line_no, Diagnostic(
                "unrecognized_content",
                f"无法识别的 DBC 内容: {stripped[:120]}",
                Severity.WARNING,
                f"{filename}:L{line_no}",
            )))
            index += 1
            continue

        if first_token == "SG_":
            mux = _MUX_SIGNAL_RE.match(stripped)
            if mux:
                inline.append((line_no, Diagnostic(
                    "unsupported_feature",
                    f"复用信号标记 {mux.group(1)} 未纳入 NetworkModel"
                    "（信号存在但复用语义缺失，decode/encode 按普通信号处理）",
                    Severity.WARNING,
                    f"{filename}:L{line_no}",
                )))
            continue
        if first_token in ("VERSION", "BS_", "BU_", "NS_", "BO_"):
            if first_token == "BO_" and "VECTOR__INDEPENDENT_SIG_MSG" in statement:
                inline.append((line_no, Diagnostic(
                    "unsupported_feature",
                    "BO_ VECTOR__INDEPENDENT_SIG_MSG（未分配到报文的信号容器）"
                    "已被 cantools 丢弃，未纳入 NetworkModel",
                    Severity.WARNING,
                    f"{filename}:L{line_no}",
                )))
            continue

        # —— 以下为分号结束的语句 ——
        if first_token == "CM_":
            scope = _CM_SCOPE_RE.match(statement)
            if scope and scope.group(1) in ("BU_", "BO_", "SG_"):
                continue  # 节点/报文/信号注释：已建模
            if scope:  # CM_ EV_ ...
                feature(line_no, "CM_ EV_", "环境变量注释")
            else:
                feature(line_no, "CM_ 数据库", "数据库级注释（CM_ 裸字符串）")
            continue
        if first_token in ("BA_", "BA_DEF_", "BA_DEF_DEF_"):
            quoted = _QUOTED_RE.search(statement)
            if quoted is None or quoted.group(1) not in _MODELED_ATTRIBUTES:
                name = quoted.group(1) if quoted else "<缺少属性名>"
                feature(line_no, f"attr:{name}", f"未建模属性 {name}")
            continue
        if first_token == "VAL_":
            if model is not None:
                diag = _check_val_reference(statement, model, filename, line_no)
                if diag is not None:
                    inline.append((line_no, diag))
            continue
        # 其余语句关键字：标准构造，NetworkModel 未表达
        description = _UNMODELED_KEYWORDS.get(first_token, "未知构造")
        feature(line_no, f"kw:{first_token}", f"{first_token} {description}")

    diagnostics = [diag for _, diag in sorted(inline, key=lambda item: item[0])]
    for key, (description, first_line, count) in deduped.items():
        times = f"（共 {count} 处）" if count > 1 else ""
        diagnostics.append(Diagnostic(
            "unsupported_feature" if not key.startswith("attr:") else "unmodeled_attribute",
            f"{description} 未纳入 NetworkModel{times}，首次出现于 L{first_line}",
            Severity.WARNING,
            f"{filename}:L{first_line}",
        ))
    diagnostics.sort(key=lambda diag: int(diag.location.rsplit("L", 1)[-1]))
    return diagnostics


def _read_statement(lines: list[str], start: int) -> tuple[str, int]:
    """从 ``start`` 行起收集语句直到分号（引号感知），返回 (语句, 结束行索引)。"""
    parts: list[str] = []
    in_quote = False
    escaped = False
    terminated = False
    index = start
    while index < len(lines):
        line = lines[index]
        parts.append(line)
        for char in line:
            if escaped:
                escaped = False
                continue
            if in_quote and char == "\\":
                escaped = True
                continue
            if char == '"':
                in_quote = not in_quote
            elif char == ";" and not in_quote:
                terminated = True
                break
        index += 1
        if terminated:
            break
    return "\n".join(parts), index


def _check_val_reference(
    statement: str, model: NetworkModel, filename: str, line_no: int
) -> Diagnostic | None:
    """核对 VAL_ 引用的报文/信号是否存在（cantools 对缺失引用静默忽略）。"""
    normalized = re.sub(r"\s+", " ", statement.strip())
    match = _VAL_REF_RE.match(normalized + " ")
    if match is None:
        return Diagnostic(
            "orphan_val_table",
            f"VAL_ 语句无法解析出引用目标: {normalized[:80]}",
            Severity.WARNING,
            f"{filename}:L{line_no}",
        )
    frame_text, signal_name = match.group(1), match.group(2)
    if frame_text is None:
        return Diagnostic(
            "orphan_val_table",
            f"VAL_ 缺少 frame id（上游解析器会静默忽略）: 信号 {signal_name}",
            Severity.WARNING,
            f"{filename}:L{line_no}",
        )
    frame_id = int(frame_text)
    message = None
    for candidate in model.messages:
        if candidate.frame_id == frame_id or (
            candidate.is_extended
            and candidate.frame_id == (frame_id & 0x7FFFFFFF)
        ):
            message = candidate
            break
    if message is None:
        return Diagnostic(
            "orphan_val_table",
            f"VAL_ 引用未知报文 frame_id=0x{frame_id:X}（上游解析器静默忽略）",
            Severity.WARNING,
            f"{filename}:L{line_no}",
        )
    if all(signal.signal_name != signal_name for signal in message.signals):
        return Diagnostic(
            "orphan_val_table",
            f"VAL_ 引用报文 {message.message_name} 中不存在的信号 {signal_name}"
            "（上游解析器静默忽略）",
            Severity.WARNING,
            f"{filename}:L{line_no}",
        )
    return None


class DbcParser(AssetParser):
    """DBC → NetworkModel 解析器（不碰设备、不执行 IO 之外的动作）。"""

    @property
    def parser_id(self) -> str:
        return "dbc"

    @property
    def parser_version(self) -> str:
        return "1.0.0"

    @property
    def supported_file_types(self) -> tuple[str, ...]:
        return (".dbc",)

    def parse(self, path: Path) -> ParseResult:
        """解析 DBC 文件；损坏文件返回带行:列定位的 ERROR 诊断。

        文件不存在等调用方错误直接抛 OSError（不转成解析诊断）。
        """
        started = time.perf_counter()
        raw = path.read_bytes()
        sha256 = hashlib.sha256(raw).hexdigest()
        # DBC 惯用编码 cp1252（与 cantools 的 dbc 默认一致）；无法解码的字节
        # 替换为 U+FFFD 并显式告警，绝不静默
        text = raw.decode("cp1252", errors="replace")

        diagnostics: list[Diagnostic] = []
        if "\ufffd" in text:
            diagnostics.append(Diagnostic(
                "encoding_replacement",
                "文件包含 cp1252 无法解码的字节，已替换为 U+FFFD 后继续解析",
                Severity.WARNING,
                path.name,
            ))

        db: Any = None
        load_error: Exception | None = None
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                db = cantools.database.load_string(
                    text,
                    database_format="dbc",
                    prune_choices=False,
                    strict=True,
                )
            except Exception as exc:  # cantools 各层异常统一转为 ERROR 诊断
                load_error = exc

        model: NetworkModel | None = None
        if load_error is None and db is not None:
            model = self._build_model(db, path, sha256)

        scan = _scan_dbc_text(text, path.name, model)
        if load_error is not None:
            detail = getattr(load_error, "e_dbc", None) or load_error
            message = str(detail) or type(load_error).__name__
            located = _LINE_COL_RE.search(message)
            location = (
                f"{path.name}:L{located.group(1)}:C{located.group(2)}"
                if located
                else path.name
            )
            diagnostics.append(Diagnostic(
                "parse_error", f"DBC 解析失败: {message}", Severity.ERROR, location
            ))
        for caught_warning in caught:
            diagnostics.append(Diagnostic(
                "upstream_warning",
                f"cantools: {type(caught_warning.message).__name__}: "
                f"{caught_warning.message}",
                Severity.WARNING,
                path.name,
            ))
        diagnostics.extend(scan)

        if model is not None:
            signal_count = sum(len(message.signals) for message in model.messages)
            elapsed_ms = (time.perf_counter() - started) * 1000
            parsed_at = datetime.now().astimezone().isoformat(timespec="seconds")
            diagnostics.append(Diagnostic(
                "parse_completed",
                f"解析完成: 节点={len(model.nodes)} 报文={len(model.messages)} "
                f"信号={signal_count} duration_ms={elapsed_ms:.0f} parsed_at={parsed_at}",
                Severity.INFO,
                path.name,
            ))

        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            model=model.to_dict() if model is not None else {},
            diagnostics=tuple(diagnostics),
            source_sha256=sha256,
        )

    # -- cantools → NetworkModel（只在此处接触 cantools 类型） -----------------

    @staticmethod
    def _build_model(db: Any, path: Path, sha256: str) -> NetworkModel:
        nodes = [
            Node(node_name=node.name, comment=node.comment)
            for node in db.nodes
        ]
        messages: list[Message] = []
        for message in db.messages:
            signals = [
                Signal(
                    signal_name=signal.name,
                    start=signal.start,
                    bit_length=signal.length,
                    byte_order=(
                        "intel" if signal.byte_order == "little_endian" else "motorola"
                    ),
                    is_signed=signal.is_signed,
                    initial_value=(
                        # cantools 的 .initial 对带值表信号返回文本标签；
                        # 统一按 decode 语义换算为物理值
                        None if signal.raw_initial is None
                        else signal.raw_initial * float(signal.scale)
                        + float(signal.offset)
                    ),
                    scale=float(signal.scale),
                    offset=float(signal.offset),
                    minimum=signal.minimum,
                    maximum=signal.maximum,
                    unit=signal.unit,
                    receivers=list(signal.receivers),
                    choices=(
                        {int(k): str(v) for k, v in signal.choices.items()}
                        if signal.choices
                        else {}
                    ),
                    comment=signal.comment,
                )
                for signal in message.signals
            ]
            messages.append(Message(
                message_name=message.name,
                frame_id=message.frame_id,
                is_extended=message.is_extended_frame,
                dlc=message.length,
                sender=message.senders[0] if message.senders else None,
                cycle_time_ms=(
                    int(message.cycle_time) if message.cycle_time is not None else None
                ),
                comment=message.comment,
                signals=signals,
            ))
        return NetworkModel(
            database_name=path.stem,
            version=db.version,
            nodes=nodes,
            messages=messages,
            sha256=sha256,
            parser_id="dbc",
            parser_version="1.0.0",
        )
