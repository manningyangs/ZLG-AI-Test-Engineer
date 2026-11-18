"""CORE-01：Test IR v1 Schema、模型、校验与迁移测试。

验收（任务书 CORE-01）：
- 有效样例通过；
- 缺少来源、未确认关键阈值、未知 action、非法 DLC/payload、负周期等非法样例被阻止；
- JSON Schema 与 Pydantic 模型一致；
- 版本迁移接口存在且对未知版本报错。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zlg_ai_test_engine.domain.test_ir import (
    ACTION_SPECS,
    IR_SCHEMA_VERSION,
    MVP_ACTIONS,
    RESERVED_ACTIONS,
    IRMigrationError,
    load_schema,
    migrate_ir,
    parse_ir,
    to_json,
    validate_executable,
    validate_ir,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VALID_DIR = REPO_ROOT / "examples" / "test-ir"
INVALID_DIR = VALID_DIR / "invalid"

#: 非法样例 → 期望的首个诊断码
EXPECTED_INVALID_CODES = {
    "unknown_action.json": "ir_schema",
    "negative_period.json": "ir_schema",
    "payload_dlc_mismatch.json": "ir_model",
    "missing_evidence.json": "ir_schema",
    "range_inverted.json": "ir_model",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 有效样例
# ---------------------------------------------------------------------------


class TestValidExamples:
    @pytest.mark.parametrize(
        "name",
        [
            "valid_cycle_monitor.json",
            "valid_signal_range.json",
            "valid_enum_check.json",
            "valid_timeout_watch.json",
            "valid_periodic_send.json",
        ],
    )
    def test_valid_example_passes_schema_and_model(self, name):
        doc = _load(VALID_DIR / name)
        result = validate_ir(doc)
        assert result.ok, [d.message for d in result.diagnostics]
        parsed = parse_ir(doc)
        assert parsed.case_id == doc["case_id"]
        assert parsed.schema_version == IR_SCHEMA_VERSION

    def test_valid_examples_pass_executable(self):
        for path in sorted(VALID_DIR.glob("valid_*.json")):
            result = validate_executable(_load(path))
            assert result.ok, f"{path.name}: {[d.message for d in result.errors]}"

    def test_roundtrip_serialization_stable(self):
        doc = _load(VALID_DIR / "valid_cycle_monitor.json")
        parsed = parse_ir(doc)
        again = json.loads(to_json(parsed))
        assert validate_ir(again).ok
        assert parse_ir(again).model_dump(mode="json") == parsed.model_dump(mode="json")


# ---------------------------------------------------------------------------
# 非法样例
# ---------------------------------------------------------------------------


class TestInvalidExamples:
    @pytest.mark.parametrize("name", sorted(EXPECTED_INVALID_CODES))
    def test_invalid_example_blocked_with_expected_code(self, name):
        doc = _load(INVALID_DIR / name)
        result = validate_ir(doc)
        assert not result.ok, f"{name} 未被阻止"
        codes = [d.code for d in result.errors]
        assert EXPECTED_INVALID_CODES[name] in codes, codes
        # 解析也必须失败（不静默容错）
        with pytest.raises(Exception):
            parse_ir(doc)

    def test_unknown_action_blocked(self):
        doc = _load(INVALID_DIR / "unknown_action.json")
        messages = " ".join(d.message for d in validate_ir(doc).errors)
        assert "send_magic_frame" in messages or "steps" in messages

    def test_negative_period_blocked(self):
        doc = _load(INVALID_DIR / "negative_period.json")
        assert not validate_ir(doc).ok

    def test_every_invalid_file_is_covered(self):
        """非法目录中的每个样例都必须被 draft 校验阻止（防样例失效）。"""
        for path in sorted(INVALID_DIR.glob("*.json")):
            if path.name in ("reserved_action_draft.json", "unconfirmed_threshold.json"):
                continue  # 这两个在 draft 级合法、executable 级阻断
            assert not validate_ir(_load(path)).ok, f"{path.name} 应被阻止"


# ---------------------------------------------------------------------------
# 预留动作与未确认项（draft 合法 / 执行阻断）
# ---------------------------------------------------------------------------


class TestExecutableGate:
    def test_reserved_action_draft_ok_executable_blocked(self):
        doc = _load(INVALID_DIR / "reserved_action_draft.json")
        assert validate_ir(doc).ok, "预留动作应允许作为草稿结构存在"
        result = validate_executable(doc)
        assert not result.ok
        assert "reserved_action" in [d.code for d in result.errors]

    def test_unconfirmed_items_blocked(self):
        doc = _load(INVALID_DIR / "unconfirmed_threshold.json")
        assert validate_ir(doc).ok
        result = validate_executable(doc)
        assert not result.ok
        assert "unconfirmed_items" in [d.code for d in result.errors]

    def test_assert_timeout_without_evidence_blocked_at_executable(self):
        doc = _load(VALID_DIR / "valid_timeout_watch.json")
        del doc["steps"][1]["evidence_source"]
        assert validate_ir(doc).ok, "evidence_source 对 assert_timeout 为草稿级可选"
        result = validate_executable(doc)
        assert not result.ok
        assert "missing_evidence_source" in [d.code for d in result.errors]

    def test_mvp_actions_all_structurally_valid(self):
        """每个 MVP 动作至少有一个最小合法步骤（防止动作字典与 Schema 脱节）。"""
        minimal_steps = {
            "listen_bus": {"action": "listen_bus", "duration_ms": 100},
            "send_frame": {"action": "send_frame", "can_id": 1, "payload_hex": "00"},
            "send_periodic": {"action": "send_periodic", "can_id": 1, "payload_hex": "00", "period_ms": 20},
            "monitor_signal": {"action": "monitor_signal", "message": "M", "signal": "S", "duration_ms": 100},
            "wait_for_frame": {"action": "wait_for_frame", "message": "M", "timeout_ms": 100},
            "assert_cycle": {"action": "assert_cycle", "message": "M", "expected_period_ms": 20, "tolerance_ms": 1, "evidence_source": "R"},
            "assert_range": {"action": "assert_range", "signal": "S", "min": 0, "max": 1, "evidence_source": "R"},
            "assert_enum": {"action": "assert_enum", "signal": "S", "allowed_values": [1], "evidence_source": "R"},
            "assert_timeout": {"action": "assert_timeout", "message": "M", "timeout_ms": 100},
        }
        assert set(minimal_steps) == MVP_ACTIONS
        base = _load(VALID_DIR / "valid_cycle_monitor.json")
        for action, step in minimal_steps.items():
            doc = dict(base)
            doc["case_id"] = f"MINIMAL_{action.upper()}"
            doc["steps"] = [{"id": "S1", **step}]
            result = validate_ir(doc)
            assert result.ok, f"最小动作 {action} 未通过: {[d.message for d in result.errors]}"


# ---------------------------------------------------------------------------
# Schema / 模型一致性与版本
# ---------------------------------------------------------------------------


class TestSchemaConsistency:
    def test_packaged_schema_matches_repo_schema(self):
        """包内运行时 Schema 与根目录 schemas/ 文档副本必须一致。"""
        root = (REPO_ROOT / "schemas" / "test-ir" / "v1" / "test_ir.schema.json").read_text(
            encoding="utf-8"
        )
        assert root == json.dumps(
            load_schema(), ensure_ascii=False, indent=2, sort_keys=False
        ) or json.loads(root) == load_schema()

    def test_schema_actions_match_action_dictionary(self):
        schema = load_schema()
        step_defs = {
            name: spec for name, spec in schema["$defs"].items() if name.startswith("step_")
        }
        schema_actions = {
            spec["properties"]["action"]["const"] for spec in step_defs.values()
        }
        assert schema_actions == set(ACTION_SPECS)
        reserved_in_schema = {
            a for a in schema_actions if a in RESERVED_ACTIONS
        }
        assert reserved_in_schema == set(RESERVED_ACTIONS)

    def test_schema_version_is_v1(self):
        assert load_schema()["properties"]["schema_version"]["const"] == IR_SCHEMA_VERSION


class TestMigration:
    def test_v10_identity(self):
        doc = _load(VALID_DIR / "valid_cycle_monitor.json")
        assert migrate_ir(doc) == doc

    def test_unknown_version_raises(self):
        with pytest.raises(IRMigrationError, match="不支持"):
            migrate_ir({"schema_version": "99.0", "case_id": "x"})

    def test_missing_version_raises(self):
        with pytest.raises(IRMigrationError, match="schema_version"):
            migrate_ir({"case_id": "x"})
