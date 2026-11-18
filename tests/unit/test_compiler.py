"""COMP-01：Test IR → Mock 执行计划编译测试。

验收（任务书 COMP-01 / 16.2）：
- 有效 IR 编译成功：命令序列、固定四件套 cleanup、assert 命令齐备；
- 同一 IR 两次编译结果完全相等，ir_hash/plan_hash 稳定（canonical json）；
- IR 变动 → ir_hash 变化；
- 非法 IR（unconfirmed_items 非空）与 MVP 预留动作抛 IRValidationError。
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from zlg_ai_test_engine.domain.execution import MOCK_COMPILER_VERSION, compile_to_mock_plan
from zlg_ai_test_engine.domain.test_ir import IRValidationError
from zlg_ai_test_engine.plugins.mock_adapter import MockAdapter

CAPABILITIES = MockAdapter().device_capabilities("mock-0")


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_of(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def base_ir() -> dict:
    """含全部采集/发送/断言形态的可执行 IR。"""
    return {
        "schema_version": "1.0",
        "case_id": "COMP_001",
        "title": "编译样例：监听+周期发送+等待+监控+断言",
        "protocol": {"bus": "CAN"},
        "preconditions": [{"action": "device_ready", "channel": 0}],
        "steps": [
            {"id": "S1", "action": "listen_bus", "duration_ms": 10000},
            {
                "id": "S2",
                "action": "send_periodic",
                "can_id": 291,
                "payload_hex": "0102",
                "period_ms": 20,
                "count": 100,
                "start_delay_ms": 0,
            },
            {"id": "S3", "action": "wait_for_frame", "message": "M", "timeout_ms": 500},
            {
                "id": "S4",
                "action": "monitor_signal",
                "message": "M",
                "signal": "S",
                "duration_ms": 1000,
            },
            {
                "id": "S5",
                "action": "assert_cycle",
                "message": "M",
                "expected_period_ms": 20,
                "tolerance_ms": 1,
                "evidence_source": "REQ_X",
            },
        ],
        "execution_policy": {
            "mode": "offline_then_hardware",
            "risk_level": "L1",
            "requires_approval": True,
            "max_duration_ms": 10000,
            "target_capabilities": ["basic_rx"],
        },
        "unconfirmed_items": [],
    }


def readonly_ir() -> dict:
    """L0 只读 IR：无 send 动作。"""
    ir = base_ir()
    ir["steps"] = [
        {"id": "S1", "action": "listen_bus", "duration_ms": 1000},
        {
            "id": "S2",
            "action": "assert_cycle",
            "message": "M",
            "expected_period_ms": 20,
            "tolerance_ms": 1,
            "evidence_source": "REQ_X",
        },
    ]
    ir["execution_policy"] = {
        "mode": "offline_only",
        "risk_level": "L0",
        "requires_approval": False,
        "max_duration_ms": 2000,
        "target_capabilities": ["basic_rx"],
    }
    return ir


def commands_of_type(plan: dict, type_: str) -> list[dict]:
    return [c for c in plan["commands"] if c["type"] == type_]


# ---------------------------------------------------------------------------
# 计划形状与命令序列
# ---------------------------------------------------------------------------


class TestPlanShape:
    def test_top_level_shape_and_versions(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        assert list(plan) == [
            "plan_version",
            "compiler_version",
            "backend",
            "ir_hash",
            "plan_hash",
            "device",
            "resources",
            "commands",
            "evidence_subscriptions",
            "cleanup",
        ]
        assert plan["plan_version"] == "1.0"
        assert plan["compiler_version"] == MOCK_COMPILER_VERSION == "1.0"
        assert plan["backend"] == "mock"

    def test_configure_and_start_are_first(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        first, second = plan["commands"][0], plan["commands"][1]
        assert first["type"] == "configure_channel"
        assert first["seq"] == 1
        assert first["step_id"] is None
        assert first["params"] == {
            "channel_index": 0,
            "bus_type": "CAN",
            "listen_only": False,
        }
        assert second["type"] == "start_channel"
        assert second["seq"] == 2
        assert second["step_id"] is None
        assert second["params"] == {"channel_index": 0}

    def test_step_commands_follow_ir_order(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        types = [(c["type"], c["step_id"]) for c in plan["commands"][2:]]
        assert types == [
            ("listen", "S1"),
            ("send_periodic", "S2"),
            ("wait", "S3"),
            ("monitor", "S4"),
            ("assert", "S5"),
        ]
        assert [c["seq"] for c in plan["commands"]] == list(
            range(1, len(plan["commands"]) + 1)
        )

    def test_assert_command_after_acquisition_with_evidence(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        assert_cmd = commands_of_type(plan, "assert")
        assert len(assert_cmd) == 1
        command = assert_cmd[0]
        assert command["step_id"] == "S5"
        assert command["action"] == "assert_cycle"
        assert command["evidence_source"] == "REQ_X"
        assert command["params"]["message"] == "M"
        assert command["params"]["expected_period_ms"] == 20
        assert command["params"]["tolerance_ms"] == 1
        last_acquisition = max(
            c["seq"] for c in plan["commands"] if c["type"] != "assert"
        )
        assert command["seq"] > last_acquisition

    def test_listen_and_send_params_derived_from_ir(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        listen = commands_of_type(plan, "listen")[0]
        assert listen["params"] == {"duration_ms": 10000}
        send = commands_of_type(plan, "send_periodic")[0]
        assert send["params"] == {
            "can_id": 291,
            "payload_hex": "0102",
            "channel": 0,
            "period_ms": 20,
            "start_delay_ms": 0,
            "count": 100,
        }
        wait = commands_of_type(plan, "wait")[0]
        assert wait["params"] == {"timeout_ms": 500, "message": "M"}
        monitor = commands_of_type(plan, "monitor")[0]
        assert monitor["params"] == {
            "message": "M",
            "signal": "S",
            "duration_ms": 1000,
        }

    def test_cleanup_is_fixed_four_steps(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        assert plan["cleanup"] == [
            {"type": "stop_sends"},
            {"type": "stop_receiving"},
            {"type": "reset_channel", "channel_index": 0},
            {"type": "close_device"},
        ]

    def test_evidence_subscriptions(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        assert {"kind": "frames", "channel": 0} in plan["evidence_subscriptions"]
        assert {"kind": "signals", "names": ["S"]} in plan["evidence_subscriptions"]

    def test_device_and_resources_sections(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        assert plan["device"] == {
            "device_id": "mock-0",
            "channel": 0,
            "bus_type": "CAN",
            "listen_only": False,
            "risk_level": "L1",
            "requires_approval": True,
        }
        assert plan["resources"] == {
            "capabilities_required": ["basic_rx", "basic_tx"],
            "max_duration_ms": 10000,
            "max_frames": 10000,  # policy 未给 max_frames → 回退默认
        }


# ---------------------------------------------------------------------------
# 确定性与哈希
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_ir_compiled_twice_is_identical(self):
        ir = base_ir()
        first = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        second = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        assert first == second
        assert first["ir_hash"] == second["ir_hash"]
        assert first["plan_hash"] == second["plan_hash"]

    def test_hashes_follow_canonical_json(self):
        ir = base_ir()
        plan = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        assert plan["ir_hash"] == sha256_of(ir)
        stripped = {k: v for k, v in plan.items() if k != "plan_hash"}
        assert plan["plan_hash"] == sha256_of(stripped)

    def test_ir_change_changes_hashes(self):
        base = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        changed = base_ir()
        changed["title"] = "改标题后哈希必须变化"
        other = compile_to_mock_plan(changed, capabilities=CAPABILITIES)
        assert other["ir_hash"] != base["ir_hash"]
        assert other["plan_hash"] != base["plan_hash"]

    def test_compiler_version_recorded_and_affects_hash(self):
        plan = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES, compiler_version="1.1")
        assert plan["compiler_version"] == "1.1"
        default = compile_to_mock_plan(base_ir(), capabilities=CAPABILITIES)
        assert plan["plan_hash"] != default["plan_hash"]


# ---------------------------------------------------------------------------
# 非法 IR 阻断
# ---------------------------------------------------------------------------


class TestInvalidIRBlocked:
    def test_unconfirmed_items_raise_validation_error(self):
        ir = base_ir()
        ir["unconfirmed_items"] = ["容差 2ms 待确认"]
        with pytest.raises(IRValidationError) as excinfo:
            compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        codes = [d.code for d in excinfo.value.diagnostics]
        assert "unconfirmed_items" in codes

    def test_reserved_action_raises_validation_error(self):
        ir = base_ir()
        ir["steps"].append(
            {
                "id": "S9",
                "action": "inject_fault",
                "fault_type": "drop_frame",
                "target": "VehicleSpeed",
                "duration_ms": 100,
            }
        )
        with pytest.raises(IRValidationError) as excinfo:
            compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        codes = [d.code for d in excinfo.value.diagnostics]
        assert "reserved_action" in codes


# ---------------------------------------------------------------------------
# listen_only 与能力/资源推导
# ---------------------------------------------------------------------------


class TestDerivation:
    def test_readonly_l0_ir_is_listen_only(self):
        plan = compile_to_mock_plan(readonly_ir(), capabilities=CAPABILITIES)
        assert plan["device"]["listen_only"] is True
        assert plan["device"]["risk_level"] == "L0"
        assert plan["resources"]["capabilities_required"] == ["basic_rx"]

    def test_l0_ir_with_send_is_not_listen_only(self):
        ir = base_ir()
        ir["execution_policy"] = {
            "mode": "offline_only",
            "risk_level": "L0",
            "requires_approval": False,
            "max_duration_ms": 5000,
            "target_capabilities": [],
        }
        plan = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        assert plan["device"]["listen_only"] is False
        assert plan["resources"]["capabilities_required"] == ["basic_rx", "basic_tx"]

    def test_channel_derived_from_preconditions(self):
        ir = base_ir()
        ir["preconditions"] = [{"action": "device_ready", "channel": 1}]
        plan = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        assert plan["device"]["channel"] == 1
        assert commands_of_type(plan, "configure_channel")[0]["params"][
            "channel_index"
        ] == 1
        assert plan["cleanup"][2] == {"type": "reset_channel", "channel_index": 1}
        assert plan["evidence_subscriptions"][0] == {"kind": "frames", "channel": 1}

    def test_max_duration_derived_from_steps_when_policy_omits(self):
        ir = base_ir()
        del ir["execution_policy"]["max_duration_ms"]
        ir["steps"][0]["duration_ms"] = 7000  # 各步骤时间上界：7000 > 其余步骤
        plan = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        assert plan["resources"]["max_duration_ms"] == 7000

    def test_resource_defaults_when_no_timed_steps(self):
        ir = {
            "schema_version": "1.0",
            "case_id": "COMP_MIN",
            "title": "仅断言步骤",
            "protocol": {"bus": "CAN"},
            "steps": [
                {
                    "id": "S1",
                    "action": "assert_range",
                    "signal": "X",
                    "min": 0,
                    "max": 1,
                    "evidence_source": "REQ_R",
                }
            ],
            "execution_policy": {"mode": "offline_only", "risk_level": "L0"},
            "unconfirmed_items": [],
        }
        plan = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        assert plan["resources"] == {
            "capabilities_required": ["basic_rx"],
            "max_duration_ms": 10000,
            "max_frames": 10000,
        }
        assert plan["device"]["listen_only"] is True
        assert plan["evidence_subscriptions"] == [
            {"kind": "frames", "channel": 0},
            {"kind": "signals", "names": ["X"]},
        ]

    def test_periodic_count_derived_from_duration(self):
        ir = base_ir()
        ir["steps"][1] = {
            "id": "S2",
            "action": "send_periodic",
            "can_id": 100,
            "payload_hex": "00",
            "period_ms": 20,
            "duration_ms": 3000,
        }
        plan = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        send = commands_of_type(plan, "send_periodic")[0]
        assert send["params"]["count"] == 150

    def test_periodic_without_count_or_duration_keeps_unbounded(self):
        ir = base_ir()
        ir["steps"][1] = {
            "id": "S2",
            "action": "send_periodic",
            "can_id": 100,
            "payload_hex": "00",
            "period_ms": 20,
        }
        plan = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        send = commands_of_type(plan, "send_periodic")[0]
        assert "count" not in send["params"]

    def test_message_ref_send_keeps_reference(self):
        ir = base_ir()
        ir["steps"][1] = {
            "id": "S2",
            "action": "send_frame",
            "message_ref": {"message": "VehicleSpeed"},
            "count": 5,
        }
        plan = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        send = commands_of_type(plan, "send_frame")[0]
        assert send["params"]["message_ref"] == {"message": "VehicleSpeed"}
        assert "can_id" not in send["params"]
        assert send["params"]["count"] == 5
        assert send["params"]["channel"] == 0

    def test_mutating_input_ir_between_compiles_is_independent(self):
        ir = base_ir()
        first = compile_to_mock_plan(ir, capabilities=CAPABILITIES)
        mutated = copy.deepcopy(ir)
        mutated["steps"][0]["duration_ms"] = 1234
        second = compile_to_mock_plan(mutated, capabilities=CAPABILITIES)
        # 第一次编译结果不随后续输入变更（值语义、无共享可变引用被改写）
        assert first["commands"][2]["params"]["duration_ms"] == 10000
        assert second["commands"][2]["params"]["duration_ms"] == 1234
