"""DATA-01：DBC 解析器测试。

覆盖任务书验收项：
- 字段解析（报文/信号/文件元信息：SHA-256、parser 版本、解析时间、警告）；
- 未识别字段/异常 → WARNING 或 ERROR 诊断，绝不静默丢弃；
- Golden 硬编码物理值（Intel/Motorola、Signed/Unsigned、跨字节、
  Factor/Offset、枚举值表、边界值）+ decode/encode 往返；
- 与 cantools 自身 decode/encode 的交叉验证（外部基准工具对照见
  docs/parsers/dbc.md，标注为待人工执行的 V 级）；
- 两次解析结果确定、sha256 稳定；
- 查询接口命中/未命中；
- ParseResult.model 与查询返回值不含任何 cantools 类型对象。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import cantools.database
import pytest

from zlg_ai_test_engine.domain.network import (
    Message,
    NetworkModel,
    NetworkQueries,
    Node,
    Signal,
)
from zlg_ai_test_engine.domain.plugins.contracts import AssetParser, ParseResult, Severity
from zlg_ai_test_engine.plugins.parser_dbc import DbcParser, _scan_dbc_text

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "dbc" / "demo_vehicle.dbc"
CORRUPT = ROOT / "examples" / "dbc" / "corrupt_bad_syntax.dbc"

FRAME_VEHICLE = 0x100
FRAME_WHEEL = 0x200
FRAME_DOOR = 0x18DAF110
FRAME_DOOR_RAW = 0x80000000 | FRAME_DOOR  # 带扩展标志位的原始 DBC 帧 ID

#: cantools 可解析、但 NetworkModel 不表达的构造集合（行号与内容对应）
UNSUPPORTED_DBC = """VERSION "unsupported_demo"
NS_ :
BS_:
BU_: ECU TESTER
BO_ 100 MuxFrame: 4 ECU
 SG_ MuxSel M : 0|8@1+ (1,0) [0|255] "" TESTER
 SG_ Alt0 m0 : 8|16@1+ (0.5,0) [0|32767.5] "" TESTER
BO_TX_BU_ 100 : TESTER;
SIG_GROUP_ 100 Grp 1 : MuxSel;
SIG_VALTYPE_ 100 Alt0 : 1;
VAL_TABLE_ MyTable 1 "One" 2 "Two";
CM_ "database level comment";
BA_DEF_ "GenMsgSendType" ENUM "Cyclic","OnEvent";
BA_DEF_DEF_ "GenMsgSendType" "Cyclic";
BA_ "GenMsgSendType" BO_ 100 0;
SG_MUL_VAL_ 100 Alt0 MuxSel 0-0;
VAL_ 100 NoSuchSignal 0 "Off" 1 "On";
VAL_ NoFrameIdSig 0 "A";
"""


@pytest.fixture(scope="module")
def parser() -> DbcParser:
    return DbcParser()


@pytest.fixture(scope="module")
def result(parser: DbcParser) -> ParseResult:
    return parser.parse(DEMO)


@pytest.fixture(scope="module")
def model(result: ParseResult) -> dict:
    assert isinstance(result.model, dict)
    return result.model


@pytest.fixture(scope="module")
def queries(result: ParseResult) -> NetworkQueries:
    return NetworkQueries.from_parse_result(result)


def _cantools_dbc_db():
    """按与解析器一致的方式加载 demo（交叉验证参照）。"""
    text = DEMO.read_bytes().decode("cp1252")
    return cantools.database.load_string(
        text, database_format="dbc", prune_choices=False, strict=True
    )


def _message(model: dict, name: str) -> dict:
    for message in model["messages"]:
        if message["message_name"] == name:
            return message
    raise AssertionError(f"model 中不存在报文 {name}")


def _signal(message: dict, name: str) -> dict:
    for signal in message["signals"]:
        if signal["signal_name"] == name:
            return signal
    raise AssertionError(f"报文 {message['message_name']} 中不存在信号 {name}")


def _diagnostics(result: ParseResult, code: str) -> list:
    return [d for d in result.diagnostics if d.code == code]


def _assert_no_cantools_types(obj, path: str = "model") -> None:
    """递归断言：任何层级的类型名/模块名都不含 cantools。"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            _assert_no_cantools_types(key, f"{path}.<key>")
            _assert_no_cantools_types(value, f"{path}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            _assert_no_cantools_types(value, f"{path}[{i}]")
        return
    type_name = type(obj).__name__
    type_module = type(obj).__module__ or ""
    assert "cantools" not in type_name, f"{path} 泄漏 cantools 类型: {type_name}"
    assert "cantools" not in type_module, f"{path} 泄漏 cantools 模块: {type_module}"


# ---------------------------------------------------------------------------
# 解析器契约身份
# ---------------------------------------------------------------------------


class TestParserSurface:
    def test_parser_identity_and_contract(self, parser: DbcParser):
        assert isinstance(parser, AssetParser)
        assert parser.parser_id == "dbc"
        assert parser.parser_version == "1.0.0"
        assert parser.supported_file_types == (".dbc",)

    def test_parse_result_shape(self, result: ParseResult):
        assert result.parser_id == "dbc"
        assert result.parser_version == "1.0.0"
        assert isinstance(result.model, dict)
        assert isinstance(result.source_sha256, str)
        assert len(result.source_sha256) == 64


# ---------------------------------------------------------------------------
# 字段解析（报文 / 信号 / 文件元信息）
# ---------------------------------------------------------------------------


class TestFieldCoverage:
    def test_root_model_fields(self, result: ParseResult, model: dict):
        assert set(model.keys()) == {
            "database_name", "version", "nodes", "messages",
            "sha256", "parser_id", "parser_version",
        }
        assert model["database_name"] == "demo_vehicle"
        assert model["version"] == "demo_vehicle 1.0"
        assert model["parser_id"] == "dbc"
        assert model["parser_version"] == "1.0.0"

    def test_sha256_matches_source_bytes_and_parse_result(self, result: ParseResult, model: dict):
        expected = hashlib.sha256(DEMO.read_bytes()).hexdigest()
        assert model["sha256"] == expected
        assert result.source_sha256 == expected

    def test_parse_completed_reports_duration_and_warnings_are_absent(
        self, result: ParseResult
    ):
        completed = _diagnostics(result, "parse_completed")
        assert len(completed) == 1
        assert completed[0].severity is Severity.INFO
        assert re.search(r"duration_ms=\d+", completed[0].message)
        assert re.search(r"parsed_at=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", completed[0].message)
        # demo 样例除元信息外不得有警告/错误
        assert [
            d for d in result.diagnostics
            if d.severity in (Severity.WARNING, Severity.ERROR)
        ] == []

    def test_nodes(self, model: dict):
        assert [n["node_name"] for n in model["nodes"]] == ["ECM", "BCM", "GW", "DOOR"]
        ecm = model["nodes"][0]
        assert set(ecm.keys()) == {"node_name", "comment"}
        assert "Engine control module" in ecm["comment"]

    def test_message_fields_vehicle_status(self, model: dict):
        message = _message(model, "VehicleStatus")
        assert message["frame_id"] == FRAME_VEHICLE
        assert message["is_extended"] is False
        assert message["dlc"] == 8
        assert message["sender"] == "ECM"
        assert message["cycle_time_ms"] == 100  # GenMsgCycleTime 属性存在时
        assert "100ms" in message["comment"]
        assert [s["signal_name"] for s in message["signals"]] == [
            "VehicleSpeed", "EngineRPM", "EngineTemp",
            "GearPosition", "IgnitionState", "Odometer",
        ]

    def test_message_fields_extended_without_cycle(self, model: dict):
        message = _message(model, "DoorStatus")
        assert message["frame_id"] == FRAME_DOOR  # 扩展帧存裸 ID
        assert message["is_extended"] is True
        assert message["dlc"] == 2
        assert message["sender"] == "DOOR"
        assert message["cycle_time_ms"] is None  # 无 GenMsgCycleTime 属性
        assert "0x18DAF110" in message["comment"]

    def test_message_fields_wheel_speeds_cycle(self, model: dict):
        message = _message(model, "WheelSpeeds")
        assert message["frame_id"] == FRAME_WHEEL
        assert message["cycle_time_ms"] == 20
        assert message["sender"] == "GW"

    def test_signal_fields_motorola_unsigned_cross_byte(self, model: dict):
        signal = _signal(_message(model, "VehicleStatus"), "VehicleSpeed")
        assert signal["start"] == 7
        assert signal["bit_length"] == 16  # 跨字节（byte0+byte1）
        assert signal["byte_order"] == "motorola"
        assert signal["is_signed"] is False
        assert signal["scale"] == 0.01
        assert signal["offset"] == 0.0
        assert signal["minimum"] == 0.0
        assert signal["maximum"] == 655.35
        assert signal["unit"] == "km/h"
        assert signal["receivers"] == ["BCM", "GW"]
        assert signal["choices"] == {}
        assert "Filtered vehicle speed" in signal["comment"]

    def test_signal_fields_intel_signed_factor_offset(self, model: dict):
        signal = _signal(_message(model, "VehicleStatus"), "EngineTemp")
        assert signal["start"] == 32
        assert signal["bit_length"] == 8
        assert signal["byte_order"] == "intel"
        assert signal["is_signed"] is True
        assert signal["scale"] == 1.0
        assert signal["offset"] == -40.0
        assert signal["minimum"] == -40.0
        assert signal["maximum"] == 215.0
        assert signal["unit"] == "degC"
        assert signal["receivers"] == ["BCM"]

    def test_signal_fields_value_table_and_initial(self, model: dict):
        signal = _signal(_message(model, "VehicleStatus"), "GearPosition")
        assert signal["choices"] == {0: "P", 1: "R", 2: "N", 3: "D", 4: "S"}
        assert signal["initial_value"] == 3.0  # GenSigStartValue → 物理域初值
        assert signal["byte_order"] == "intel"
        assert "Gear selector position" in signal["comment"]
        ignition = _signal(_message(model, "VehicleStatus"), "IgnitionState")
        assert ignition["choices"] == {0: "Off", 1: "Accessory", 2: "Run", 3: "Crank"}

    def test_signal_fields_motorola_scale_unit(self, model: dict):
        signal = _signal(_message(model, "VehicleStatus"), "Odometer")
        assert signal["start"] == 55  # 跨字节（byte6+byte7）
        assert signal["bit_length"] == 16
        assert signal["byte_order"] == "motorola"
        assert signal["scale"] == 0.1
        assert signal["unit"] == "km"

    def test_signal_fields_intel_signed_cross_byte(self, model: dict):
        signal = _signal(_message(model, "WheelSpeeds"), "WheelRR")
        assert signal["start"] == 48
        assert signal["bit_length"] == 16
        assert signal["byte_order"] == "intel"
        assert signal["is_signed"] is True
        assert signal["minimum"] == -327.68
        assert signal["maximum"] == 327.67

    def test_signal_fields_motorola_signed(self, model: dict):
        signal = _signal(_message(model, "DoorStatus"), "SunroofPos")
        assert signal["start"] == 15
        assert signal["byte_order"] == "motorola"
        assert signal["is_signed"] is True
        assert signal["unit"] == "%"
        lock = _signal(_message(model, "DoorStatus"), "DoorLockState")
        assert lock["choices"] == {0: "Unlocked", 1: "Locked", 2: "Unknown", 3: "Error"}


# ---------------------------------------------------------------------------
# Golden 解码 + 与 cantools 的交叉验证
# ---------------------------------------------------------------------------

#: 硬编码期望物理值（与 cantools 输出逐位一致的精确值）
GOLDEN_DECODE = [
    # (frame_id, 数据 hex, 期望 {信号: 物理值})
    (FRAME_VEHICLE, "0bb827100a2300fa", {
        "VehicleSpeed": 30.0,      # motorola 跨字节, factor 0.01
        "EngineRPM": 1033.75,      # intel 跨字节, factor 0.25
        "EngineTemp": -30.0,       # intel signed + offset -40
        "GearPosition": 3.0,       # 枚举值表 (3 → "D")
        "IgnitionState": 2.0,
        "Odometer": 25.0,          # motorola 跨字节, factor 0.1
    }),
    (FRAME_WHEEL, "0100020003008000", {
        "WheelFL": 2.56,
        "WheelFR": 5.12,
        "WheelRL": 0.03,
        "WheelRR": 1.28,
    }),
    (FRAME_DOOR, "05f0", {
        "DriverDoorAjar": 1.0,
        "PassengerDoorAjar": 0.0,
        "DoorLockState": 1.0,      # 枚举值表 (1 → "Locked")
        "SunroofPos": -16.0,       # motorola signed（0xF0 → -16）
    }),
]


class TestGoldenDecode:
    @pytest.mark.parametrize("frame_id,data_hex,expected", GOLDEN_DECODE)
    def test_golden_values(self, queries: NetworkQueries, frame_id, data_hex, expected):
        decoded = queries.decode_message(frame_id, bytes.fromhex(data_hex))
        assert decoded == expected

    @pytest.mark.parametrize("frame_id,data_hex,expected", GOLDEN_DECODE)
    def test_cross_validation_with_cantools_decode(
        self, queries: NetworkQueries, frame_id, data_hex, expected
    ):
        db = _cantools_dbc_db()
        reference = db.get_message_by_frame_id(frame_id).decode(
            bytes.fromhex(data_hex), decode_choices=False, scaling=True
        )
        ours = queries.decode_message(frame_id, bytes.fromhex(data_hex))
        assert ours == reference  # 与 cantools 解码结果逐项一致

    def test_boundary_max_min_values(self, queries: NetworkQueries):
        # VehicleSpeed 上边界：0xFFFF → 655.35 km/h（= maximum）
        decoded = queries.decode_message(FRAME_VEHICLE, bytes.fromhex("ffff27100a230000"))
        assert decoded["VehicleSpeed"] == pytest.approx(655.35)
        # 下边界：0 → 0.0（= minimum）
        decoded = queries.decode_message(FRAME_VEHICLE, bytes.fromhex("000027100a230000"))
        assert decoded["VehicleSpeed"] == 0.0
        # WheelRR signed 16 位上下边界
        lo = queries.decode_message(FRAME_WHEEL, bytes.fromhex("0100020003000080"))
        assert lo["WheelRR"] == pytest.approx(-327.68)
        hi = queries.decode_message(FRAME_WHEEL, bytes.fromhex("010002000300ff7f"))
        assert hi["WheelRR"] == pytest.approx(327.67)
        # EngineTemp offset 下边界：原始值 0 → -40
        decoded = queries.decode_message(FRAME_VEHICLE, bytes.fromhex("0bb82710002300fa"))
        assert decoded["EngineTemp"] == -40.0

    def test_enum_choices_lookup_after_decode(self, queries: NetworkQueries):
        decoded = queries.decode_message(FRAME_VEHICLE, bytes.fromhex("0bb827100a2300fa"))
        gear = queries.find_signal("VehicleStatus", "GearPosition")
        assert gear is not None and gear.choices[int(decoded["GearPosition"])] == "D"
        lock = queries.find_signal("DoorStatus", "DoorLockState")
        assert lock is not None and lock.choices[1] == "Locked"

    def test_decode_unknown_frame_and_bad_length(self, queries: NetworkQueries):
        with pytest.raises(KeyError):
            queries.decode_message(0x999, b"\x00\x00")
        with pytest.raises(ValueError, match="DLC"):
            queries.decode_message(FRAME_VEHICLE, b"\x01\x02")


class TestGoldenEncodeRoundtrip:
    #: (frame_id, 物理值集合, 期望帧字节 hex —— 与 cantools encode 结果一致)
    ENCODE_VECTORS = [
        (FRAME_VEHICLE, {
            "VehicleSpeed": 30.0, "EngineRPM": 1033.75, "EngineTemp": -30,
            "GearPosition": 3, "IgnitionState": 2, "Odometer": 25.0,
        }, "0bb827100a2300fa"),
        (FRAME_WHEEL, {
            "WheelFL": 1.0, "WheelFR": 2.0, "WheelRL": 3.0, "WheelRR": -327.68,
        }, "006400c82c010080"),
        (FRAME_DOOR, {
            "DriverDoorAjar": 1, "PassengerDoorAjar": 0,
            "DoorLockState": 1, "SunroofPos": -16,
        }, "05f0"),
    ]

    @pytest.mark.parametrize("frame_id,values,expected_hex", ENCODE_VECTORS)
    def test_encode_matches_golden_bytes(
        self, queries: NetworkQueries, frame_id, values, expected_hex
    ):
        assert queries.encode_message(frame_id, values).hex() == expected_hex

    @pytest.mark.parametrize("frame_id,values,expected_hex", ENCODE_VECTORS)
    def test_encode_cross_validation_with_cantools(
        self, queries: NetworkQueries, frame_id, values, expected_hex
    ):
        db = _cantools_dbc_db()
        message = db.get_message_by_frame_id(frame_id)
        assert queries.encode_message(frame_id, values) == message.encode(values)

    @pytest.mark.parametrize("frame_id,values,expected_hex", ENCODE_VECTORS)
    def test_decode_encode_roundtrip(
        self, queries: NetworkQueries, frame_id, values, expected_hex
    ):
        encoded = queries.encode_message(frame_id, values)
        decoded = queries.decode_message(frame_id, encoded)
        assert decoded == pytest.approx(values)

    def test_encode_omitted_signals_default_to_raw_zero(self, queries: NetworkQueries):
        encoded = queries.encode_message(FRAME_VEHICLE, {"VehicleSpeed": 30.0})
        decoded = queries.decode_message(FRAME_VEHICLE, encoded)
        assert decoded["VehicleSpeed"] == 30.0
        assert decoded["EngineTemp"] == -40.0  # 原始值 0 → offset
        assert decoded["GearPosition"] == 0.0

    def test_encode_boundary_max(self, queries: NetworkQueries):
        encoded = queries.encode_message(FRAME_VEHICLE, {"VehicleSpeed": 655.35})
        assert queries.decode_message(FRAME_VEHICLE, encoded)["VehicleSpeed"] == pytest.approx(
            655.35
        )

    def test_encode_rejects_unknown_and_out_of_range(self, queries: NetworkQueries):
        with pytest.raises(KeyError):
            queries.encode_message(FRAME_VEHICLE, {"NotASignal": 1})
        with pytest.raises(KeyError):
            queries.encode_message(0x999, {"VehicleSpeed": 1.0})
        with pytest.raises(ValueError, match="超出"):
            queries.encode_message(FRAME_VEHICLE, {"VehicleSpeed": 700.0})  # > 655.35


# ---------------------------------------------------------------------------
# 确定性
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_two_parses_produce_identical_model_and_sha(
        self, parser: DbcParser, result: ParseResult
    ):
        again = parser.parse(DEMO)
        assert again.model == result.model
        assert again.source_sha256 == result.source_sha256
        assert again.parser_id == result.parser_id
        assert again.parser_version == result.parser_version

    def test_diagnostics_identical_except_timing(
        self, parser: DbcParser, result: ParseResult
    ):
        again = parser.parse(DEMO)
        def stable(diags):
            return [d for d in diags if d.code != "parse_completed"]
        assert stable(again.diagnostics) == stable(result.diagnostics)


# ---------------------------------------------------------------------------
# 诊断：损坏文件 / 未识别字段 / 编码替换
# ---------------------------------------------------------------------------


class TestDiagnostics:
    def test_corrupt_file_reports_error_with_location(self, parser: DbcParser):
        outcome = parser.parse(CORRUPT)
        errors = [d for d in outcome.diagnostics if d.severity is Severity.ERROR]
        assert len(errors) == 1
        assert errors[0].code == "parse_error"
        assert errors[0].location == "corrupt_bad_syntax.dbc:L7:C5"
        assert "line 7" in errors[0].message  # 定位到具体行
        assert "not_a_number" in errors[0].message  # 定位到具体内容
        assert outcome.model == {}
        assert outcome.source_sha256 == hashlib.sha256(CORRUPT.read_bytes()).hexdigest()

    def test_garbage_line_end_to_end_is_error(self, parser: DbcParser, tmp_path):
        bad = tmp_path / "garbage.dbc"
        bad.write_text(
            'VERSION "g"\n'
            "NS_ :\n"
            "BS_:\n"
            "BU_: A B\n"
            "BO_ 100 M: 2 A\n"
            " SG_ X : 0|16@1+ (1,0) [0|65535] \"\" B\n"
            "GARBAGE unknown construct;\n",
            encoding="ascii",
        )
        outcome = parser.parse(bad)
        errors = [d for d in outcome.diagnostics if d.severity is Severity.ERROR]
        assert len(errors) == 1
        assert "line 7" in errors[0].message

    def test_unmodeled_constructs_all_warned_not_dropped(
        self, parser: DbcParser, tmp_path
    ):
        path = tmp_path / "unsupported.dbc"
        path.write_text(UNSUPPORTED_DBC, encoding="ascii")
        outcome = parser.parse(path)
        # 上游可解析 → 不得有 ERROR
        assert [d for d in outcome.diagnostics if d.severity is Severity.ERROR] == []
        warnings = [d for d in outcome.diagnostics if d.severity is Severity.WARNING]
        assert warnings, "未识别构造必须生成警告"
        by_location = {d.location: d for d in warnings}
        # 标准构造（cantools 容忍、模型不表达）→ unsupported_feature + 行定位
        for line in (6, 7, 8, 9, 10, 11, 12, 16):
            diag = by_location.get(f"unsupported.dbc:L{line}")
            assert diag is not None, f"L{line} 的构造未生成警告"
            assert diag.code in ("unsupported_feature", "unmodeled_attribute")
        # 未建模属性（BA_/BA_DEF_/BA_DEF_DEF_ 三处，同名去重）
        attr = by_location["unsupported.dbc:L13"]
        assert attr.code == "unmodeled_attribute"
        assert "GenMsgSendType" in attr.message
        assert "共 3 处" in attr.message
        # cantools 静默丢弃的 VAL_ 引用 → orphan_val_table
        orphan_signal = by_location["unsupported.dbc:L17"]
        assert orphan_signal.code == "orphan_val_table"
        assert "NoSuchSignal" in orphan_signal.message
        orphan_frame = by_location["unsupported.dbc:L18"]
        assert orphan_frame.code == "orphan_val_table"
        assert "frame id" in orphan_frame.message
        # 报文本体仍正常解析
        assert [m["message_name"] for m in outcome.model["messages"]] == ["MuxFrame"]

    def test_scanner_flags_fully_unrecognized_line(self):
        # 白盒：即使上游容忍未知行，扫描器也必须逐行告警（双保险）
        text = 'VERSION "x"\nNS_ :\nBS_:\nBU_: A B\nNOT_A_DBC_KEYWORD oops;\n'
        diags = _scan_dbc_text(text, "x.dbc", None)
        assert len(diags) == 1
        assert diags[0].code == "unrecognized_content"
        assert diags[0].severity is Severity.WARNING
        assert diags[0].location == "x.dbc:L5"
        assert "NOT_A_DBC_KEYWORD" in diags[0].message

    def test_undecodable_byte_warns_instead_of_silent_replace(
        self, parser: DbcParser, tmp_path
    ):
        path = tmp_path / "bad_encoding.dbc"
        path.write_bytes(
            b'VERSION "enc"\n'
            b"NS_ :\n"
            b"BS_:\n"
            b"BU_: A B\n"
            b"BO_ 100 M: 2 A\n"
            b' SG_ X : 0|16@1+ (1,0) [0|65535] "" B\n'
            b'CM_ BO_ 100 "\x81";\n'  # 0x81 在 cp1252 中未定义
        )
        outcome = parser.parse(path)
        assert _diagnostics(outcome, "encoding_replacement")
        assert [m for m in outcome.model["messages"] if m["message_name"] == "M"]
        assert [d for d in outcome.diagnostics if d.severity is Severity.ERROR] == []


# ---------------------------------------------------------------------------
# 查询接口
# ---------------------------------------------------------------------------


class TestQueries:
    def test_find_message_by_name_hit_and_miss(self, queries: NetworkQueries):
        hit = queries.find_message_by_name("WheelSpeeds")
        assert hit is not None and hit.frame_id == FRAME_WHEEL
        assert queries.find_message_by_name("DoesNotExist") is None

    def test_find_message_by_frame_id_hit_and_miss(self, queries: NetworkQueries):
        assert queries.find_message_by_frame_id(FRAME_VEHICLE).message_name == "VehicleStatus"
        # 扩展帧：裸 ID 与带 0x80000000 标志位的原始 DBC ID 都命中
        assert queries.find_message_by_frame_id(FRAME_DOOR).message_name == "DoorStatus"
        assert queries.find_message_by_frame_id(FRAME_DOOR_RAW).message_name == "DoorStatus"
        assert queries.find_message_by_frame_id(0x999) is None

    def test_find_signal_hit_and_miss(self, queries: NetworkQueries):
        hit = queries.find_signal("VehicleStatus", "GearPosition")
        assert hit is not None and hit.choices[3] == "D"
        assert queries.find_signal("VehicleStatus", "NoSuchSignal") is None
        assert queries.find_signal("NoSuchMessage", "GearPosition") is None

    def test_list_nodes(self, queries: NetworkQueries):
        nodes = queries.list_nodes()
        assert all(isinstance(node, Node) for node in nodes)
        assert tuple(node.node_name for node in nodes) == ("ECM", "BCM", "GW", "DOOR")

    def test_from_parse_result_revalidates_model(self, result: ParseResult):
        rebuilt = NetworkQueries.from_parse_result(result)
        assert isinstance(rebuilt.model, NetworkModel)
        assert rebuilt.model == NetworkQueries.from_parse_result(result).model


# ---------------------------------------------------------------------------
# 三方对象泄漏防线
# ---------------------------------------------------------------------------


class TestNoCantoolsLeakage:
    def test_parse_result_model_has_no_cantools_types(self, result: ParseResult):
        _assert_no_cantools_types(result.model, "ParseResult.model")

    def test_model_classes_are_project_types(self, queries: NetworkQueries):
        model = queries.model
        assert isinstance(model, NetworkModel)
        assert all(isinstance(node, Node) for node in model.nodes)
        assert all(isinstance(message, Message) for message in model.messages)
        assert all(
            isinstance(signal, Signal)
            for message in model.messages
            for signal in message.signals
        )
        _assert_no_cantools_types(model, "NetworkModel")

    def test_query_returns_have_no_cantools_types(self, queries: NetworkQueries):
        _assert_no_cantools_types(queries.find_message_by_name("VehicleStatus"), "find_message")
        _assert_no_cantools_types(queries.find_signal("VehicleStatus", "EngineRPM"), "find_signal")
        _assert_no_cantools_types(queries.list_nodes(), "list_nodes")
        decoded = queries.decode_message(FRAME_VEHICLE, bytes.fromhex("0bb827100a2300fa"))
        _assert_no_cantools_types(decoded, "decode_message")
        encoded = queries.encode_message(FRAME_VEHICLE, {"VehicleSpeed": 30.0})
        assert type(encoded) is bytes
        _assert_no_cantools_types(encoded, "encode_message")
