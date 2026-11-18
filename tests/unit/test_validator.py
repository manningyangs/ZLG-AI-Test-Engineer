"""VALID-01：执行前七类集中规则校验测试。

验收（任务书 VALID-01 / 16.2）：
- 七类检查各至少 1 正 1 反；反例断言对应 code 前缀，且 ERROR message 含“修正建议”；
- 未知能力、stale/未注册资产、L1 未审批、L4（恒阻断）、周期/队列互斥在执行前阻断；
- 结构合法的最小 IR 七类全过；网络模型降级只出 WARNING，绝不升级为 ERROR；
- 结构级错误原样透传 validate_executable 的诊断。
"""

from __future__ import annotations

import copy

from zlg_ai_test_engine.domain.plugins.contracts import Severity
from zlg_ai_test_engine.domain.test_ir import validate_executable
from zlg_ai_test_engine.domain.validation import ValidationContext, validate_execution
from zlg_ai_test_engine.plugins.mock_adapter import MockAdapter

ACTIVE_SHA = "a" * 64

#: 结构合法、七类全过的最小 IR（含资产引用、报文/信号引用与断言步骤）
BASE_IR: dict = {
    "schema_version": "1.0",
    "case_id": "VALID_MIN_001",
    "title": "最小七类全过用例",
    "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
    "target": {"message": "VehicleSpeed"},
    "database_refs": [
        {"asset_id": "dbc_vehicle", "message": "VehicleSpeed", "sha256": ACTIVE_SHA}
    ],
    "preconditions": [{"action": "device_ready", "channel": 0}],
    "steps": [
        {"id": "S1", "action": "listen_bus", "duration_ms": 1000},
        {
            "id": "S2",
            "action": "monitor_signal",
            "message": "VehicleSpeed",
            "signal": "VehSpd",
            "duration_ms": 500,
        },
        {
            "id": "S3",
            "action": "assert_cycle",
            "message": "VehicleSpeed",
            "expected_period_ms": 20,
            "tolerance_ms": 2,
            "evidence_source": "REQ_CYCLE",
        },
    ],
    "execution_policy": {
        "mode": "offline_only",
        "risk_level": "L0",
        "requires_approval": False,
        "max_duration_ms": 2000,
        "max_frames": 100,
        "target_capabilities": ["basic_rx"],
    },
    "unconfirmed_items": [],
}

#: asset_id -> {"sha256", "status", "type"}（两条均为 active）
ASSET_REGISTRY = {
    "vehicle.dbc": {"sha256": "b" * 64, "status": "active", "type": "dbc"},
    "dbc_vehicle": {"sha256": ACTIVE_SHA, "status": "active", "type": "dbc"},
}

#: 解析后的 NetworkModel（含 messages/signals 两级清单）
NETWORK_MODEL = {
    "messages": {
        "VehicleSpeed": {
            "name": "VehicleSpeed",
            "can_id": 256,
            "dlc": 8,
            "signals": [{"name": "VehSpd"}, {"name": "VehSpdValid"}],
        }
    },
    "signals": {"VehSpd": {"name": "VehSpd"}, "VehSpdValid": {"name": "VehSpdValid"}},
}

#: MockAdapter.device_capabilities() 形状（含 evidence 四级）
CAPABILITIES = MockAdapter().device_capabilities("mock-0")

DEFAULT_APPROVAL = {"user_level": "L0", "approved_actions": [], "environment": "offline_lab"}

FREE_RESOURCES = {
    "device_busy": False,
    "busy_channels": [],
    "periodic_active": False,
    "queue_active": False,
}

_SENTINEL = object()


def make_ir(**overrides) -> dict:
    """基于最小 IR 构造变体（深拷贝，覆盖顶层键）。"""
    ir = copy.deepcopy(BASE_IR)
    ir.update(overrides)
    return ir


def make_context(
    ir: dict | None = None,
    *,
    registry=None,
    network_model=_SENTINEL,
    capabilities=None,
    approval=None,
    resource_state=None,
) -> ValidationContext:
    """构造 ValidationContext；未显式给出的字段使用可通过七类检查的默认值。"""
    return ValidationContext(
        ir=BASE_IR if ir is None else ir,
        asset_registry=ASSET_REGISTRY if registry is None else registry,
        network_model=NETWORK_MODEL if network_model is _SENTINEL else network_model,
        capabilities=CAPABILITIES if capabilities is None else capabilities,
        approval=DEFAULT_APPROVAL if approval is None else approval,
        resource_state=FREE_RESOURCES if resource_state is None else resource_state,
    )


def send_ir(*, risk_level="L0", requires_approval=False, steps=None) -> dict:
    """含 send_periodic 的 IR（默认 L0 无审批要求，隔离其他类别干扰）。"""
    policy = {
        "mode": "offline_only",
        "risk_level": risk_level,
        "requires_approval": requires_approval,
        "max_duration_ms": 5000,
        "max_frames": 100,
        "target_capabilities": ["basic_tx"],
    }
    return make_ir(
        execution_policy=policy,
        steps=steps
        or [
            {
                "id": "S1",
                "action": "send_periodic",
                "can_id": 100,
                "payload_hex": "0102",
                "period_ms": 20,
                "count": 10,
            }
        ],
    )


def errors_with_prefix(result, prefix: str):
    """断言：存在该前缀的 ERROR，且全部 ERROR 的 message 都含“修正建议”。"""
    for diagnostic in result.errors:
        assert "修正建议" in diagnostic.message, (
            f"ERROR {diagnostic.code} 的 message 缺少修正建议: {diagnostic.message}"
        )
    matched = [d for d in result.errors if d.code.startswith(prefix)]
    assert matched, (
        f"缺少 {prefix} 前缀的 ERROR: {[d.code for d in result.diagnostics]}"
    )
    return matched


def warnings_with_prefix(result, prefix: str):
    matched = [d for d in result.diagnostics if d.code.startswith(prefix)]
    assert matched, (
        f"缺少 {prefix} 前缀的诊断: {[d.code for d in result.diagnostics]}"
    )
    for diagnostic in matched:
        assert diagnostic.severity == Severity.WARNING, diagnostic
    return matched


# ---------------------------------------------------------------------------
# 基线：结构合法的最小 IR 七类全过
# ---------------------------------------------------------------------------


class TestBaselineAllPass:
    def test_minimal_ir_passes_all_seven_checks(self):
        result = validate_execution(make_context())
        assert result.ok, [d.code for d in result.errors]
        # 七类全过：零 ERROR 且零 WARNING（无降级、无缺项）
        assert result.diagnostics == (), [d.code for d in result.diagnostics]


# ---------------------------------------------------------------------------
# 1. 工程引用（chk_project_ref）
# ---------------------------------------------------------------------------


class TestProjectReference:
    def test_resolvable_references_pass(self):
        result = validate_execution(make_context())
        assert not [d for d in result.diagnostics if d.code.startswith("chk_project_ref")]

    def test_stale_asset_blocked(self):
        registry = {
            **ASSET_REGISTRY,
            "vehicle.dbc": {**ASSET_REGISTRY["vehicle.dbc"], "status": "stale"},
        }
        result = validate_execution(make_context(registry=registry))
        assert not result.ok
        errors = errors_with_prefix(result, "chk_project_ref")
        assert any(e.code == "chk_project_ref.asset_not_active" for e in errors)

    def test_unregistered_asset_blocked(self):
        registry = {k: v for k, v in ASSET_REGISTRY.items() if k != "dbc_vehicle"}
        result = validate_execution(make_context(registry=registry))
        errors = errors_with_prefix(result, "chk_project_ref")
        assert any(e.code == "chk_project_ref.asset_not_registered" for e in errors)

    def test_asset_hash_mismatch_blocked(self):
        registry = {
            **ASSET_REGISTRY,
            "dbc_vehicle": {**ASSET_REGISTRY["dbc_vehicle"], "sha256": "c" * 64},
        }
        result = validate_execution(make_context(registry=registry))
        errors = errors_with_prefix(result, "chk_project_ref")
        assert any(e.code == "chk_project_ref.asset_hash_mismatch" for e in errors)

    def test_nonexistent_message_blocked(self):
        result = validate_execution(make_context(make_ir(target={"message": "Ghost"})))
        errors = errors_with_prefix(result, "chk_project_ref")
        assert any(e.code == "chk_project_ref.message_not_found" for e in errors)

    def test_nonexistent_signal_blocked(self):
        ir = make_ir()
        ir["steps"][1]["signal"] = "Nope"
        result = validate_execution(make_context(ir))
        errors = errors_with_prefix(result, "chk_project_ref")
        assert any(e.code == "chk_project_ref.signal_not_found" for e in errors)

    def test_missing_network_model_is_warning_not_error(self):
        result = validate_execution(make_context(network_model=None))
        assert result.ok, [d.code for d in result.errors]
        warnings_with_prefix(result, "chk_project_ref")

    def test_model_without_signal_list_degrades_to_warning(self):
        # 网络模型只含 messages（无任何 signals 清单）：信号引用降级待核，不得升级为 ERROR
        model = {"messages": {"VehicleSpeed": {"name": "VehicleSpeed", "can_id": 256}}}
        result = validate_execution(make_context(network_model=model))
        assert result.ok, [d.code for d in result.errors]
        warnings_with_prefix(result, "chk_project_ref")


# ---------------------------------------------------------------------------
# 2. 协议能力（chk_protocol）
# ---------------------------------------------------------------------------


class TestProtocolCapability:
    def test_can_fd_declared_in_buses_passes(self):
        ir = make_ir(protocol={"bus": "CAN_FD", "database": "vehicle.dbc"})
        result = validate_execution(make_context(ir))
        assert result.ok, [d.code for d in result.errors]

    def test_bus_missing_from_capabilities_blocked(self):
        ir = make_ir(protocol={"bus": "CAN_FD", "database": "vehicle.dbc"})
        capabilities = {**CAPABILITIES, "buses": ["CAN"]}
        result = validate_execution(make_context(ir, capabilities=capabilities))
        errors = errors_with_prefix(result, "chk_protocol")
        assert any(e.code == "chk_protocol.bus_unsupported" for e in errors)


# ---------------------------------------------------------------------------
# 3. 设备能力（chk_device_cap）
# ---------------------------------------------------------------------------


class TestDeviceCapability:
    def test_verified_capabilities_pass(self):
        policy = dict(BASE_IR["execution_policy"], target_capabilities=["basic_tx", "basic_rx"])
        result = validate_execution(make_context(make_ir(execution_policy=policy)))
        assert result.ok, [d.code for d in result.errors]

    def test_unknown_capability_blocked(self):
        # Mock 对 hardware_periodic_tx 显式声明 unknown：未知 ≠ 支持
        policy = dict(
            BASE_IR["execution_policy"], target_capabilities=["hardware_periodic_tx"]
        )
        result = validate_execution(make_context(make_ir(execution_policy=policy)))
        errors = errors_with_prefix(result, "chk_device_cap")
        assert any(e.code == "chk_device_cap.unproven_capability" for e in errors)
        assert all("未知" in e.message for e in errors)

    def test_undeclared_capability_blocked(self):
        policy = dict(
            BASE_IR["execution_policy"], target_capabilities=["ghost_capability"]
        )
        result = validate_execution(make_context(make_ir(execution_policy=policy)))
        errors = errors_with_prefix(result, "chk_device_cap")
        assert any(e.code == "chk_device_cap.unproven_capability" for e in errors)


# ---------------------------------------------------------------------------
# 4. 参数合法性（chk_param）
# ---------------------------------------------------------------------------


class TestParamLegality:
    def test_boundary_classic_frame_params_pass(self):
        ir = make_ir(
            steps=[
                {
                    "id": "S1",
                    "action": "send_periodic",
                    "can_id": 0x1FFFFFFF,
                    "payload_hex": "0102030405060708",
                    "dlc": 8,
                    "period_ms": 1,
                    "count": 1,
                }
            ]
        )
        result = validate_execution(make_context(ir))
        assert result.ok, [d.code for d in result.errors]
        assert not [d for d in result.errors if d.code.startswith("chk_param")]

    def test_fd_frame_on_can_bus_blocked(self):
        # 结构层不交叉校验 is_fd 与 protocol.bus（已实测 validate_executable 通过），
        # chk_param 在此防御性拦截。
        ir = make_ir(
            steps=[
                {
                    "id": "S1",
                    "action": "send_periodic",
                    "can_id": 1,
                    "payload_hex": "0102030405060708090A",
                    "period_ms": 20,
                    "count": 3,
                    "is_fd": True,
                }
            ]
        )
        result = validate_execution(make_context(ir))
        errors = errors_with_prefix(result, "chk_param")
        assert any(e.code == "chk_param.fd_on_non_fd_bus" for e in errors)

    def test_classic_dlc_over_limit_without_payload_blocked(self):
        # dlc>8 且无 payload_hex 时 CORE-01 结构层放行（if 条件不触发），此处拦截
        ir = make_ir(
            steps=[
                {
                    "id": "S1",
                    "action": "send_frame",
                    "message_ref": {"message": "VehicleSpeed"},
                    "dlc": 9,
                }
            ]
        )
        result = validate_execution(make_context(ir))
        errors = errors_with_prefix(result, "chk_param")
        assert any(e.code == "chk_param.invalid_dlc" for e in errors)


# ---------------------------------------------------------------------------
# 5. 安全授权（chk_security）
# ---------------------------------------------------------------------------


class TestSecurityApproval:
    def test_l1_fully_approved_passes(self):
        approval = {
            "user_level": "L1",
            "approved_actions": ["send_periodic"],
            "environment": "lab_bench",
        }
        ir = send_ir(risk_level="L1", requires_approval=True)
        result = validate_execution(make_context(ir, approval=approval))
        assert result.ok, [d.code for d in result.errors]

    def test_l1_without_approval_blocked(self):
        ir = send_ir(risk_level="L1", requires_approval=True)
        result = validate_execution(make_context(ir))  # 默认 L0、审批清单为空
        errors = errors_with_prefix(result, "chk_security")
        codes = {e.code for e in errors}
        assert "chk_security.user_level_insufficient" in codes
        assert "chk_security.approval_missing_action" in codes

    def test_action_approved_but_user_level_low_blocked(self):
        approval = {
            "user_level": "L0",
            "approved_actions": ["send_periodic"],
            "environment": "lab_bench",
        }
        ir = send_ir(risk_level="L1", requires_approval=True)
        result = validate_execution(make_context(ir, approval=approval))
        errors = errors_with_prefix(result, "chk_security")
        codes = {e.code for e in errors}
        assert "chk_security.user_level_insufficient" in codes
        assert "chk_security.approval_missing_action" not in codes

    def test_l4_always_blocked_even_fully_approved(self):
        approval = {
            "user_level": "L4",
            "approved_actions": ["send_periodic"],
            "environment": "lab_bench",
        }
        ir = send_ir(risk_level="L4", requires_approval=True)
        result = validate_execution(make_context(ir, approval=approval))
        errors = errors_with_prefix(result, "chk_security")
        assert any(e.code == "chk_security.level_prohibited" for e in errors)

    def test_l4_blocked_even_without_requires_approval(self):
        ir = send_ir(risk_level="L4", requires_approval=False)
        result = validate_execution(make_context(ir))
        errors = errors_with_prefix(result, "chk_security")
        assert any(e.code == "chk_security.level_prohibited" for e in errors)


# ---------------------------------------------------------------------------
# 6. 资源冲突（chk_resource）
# ---------------------------------------------------------------------------


class TestResourceConflict:
    def test_free_resources_pass(self):
        result = validate_execution(make_context())
        assert not [d for d in result.errors if d.code.startswith("chk_resource")]

    def test_device_busy_blocked(self):
        state = {**FREE_RESOURCES, "device_busy": True}
        result = validate_execution(make_context(resource_state=state))
        errors = errors_with_prefix(result, "chk_resource")
        assert any(e.code == "chk_resource.device_busy" for e in errors)

    def test_required_channel_busy_blocked(self):
        state = {**FREE_RESOURCES, "busy_channels": [0]}
        result = validate_execution(make_context(resource_state=state))
        errors = errors_with_prefix(result, "chk_resource")
        assert any(e.code == "chk_resource.channel_busy" for e in errors)

    def test_periodic_conflict_when_periodic_active_blocked(self):
        state = {**FREE_RESOURCES, "periodic_active": True}
        result = validate_execution(make_context(send_ir(), resource_state=state))
        errors = errors_with_prefix(result, "chk_resource")
        assert any(e.code == "chk_resource.periodic_conflict" for e in errors)

    def test_periodic_conflict_when_queue_active_blocked(self):
        state = {**FREE_RESOURCES, "queue_active": True}
        result = validate_execution(make_context(send_ir(), resource_state=state))
        errors = errors_with_prefix(result, "chk_resource")
        assert any(e.code == "chk_resource.periodic_conflict" for e in errors)


# ---------------------------------------------------------------------------
# 7. 计划完备性（chk_plan）
# ---------------------------------------------------------------------------


class TestPlanCompleteness:
    def test_complete_plan_passes(self):
        result = validate_execution(make_context())
        assert not [d for d in result.diagnostics if d.code.startswith("chk_plan")]

    def test_missing_max_duration_blocked(self):
        ir = make_ir()
        del ir["execution_policy"]["max_duration_ms"]
        result = validate_execution(make_context(ir))
        errors = errors_with_prefix(result, "chk_plan")
        assert any(e.code == "chk_plan.missing_max_duration" for e in errors)

    def test_no_assertion_step_is_warning_not_error(self):
        ir = make_ir(steps=[{"id": "S1", "action": "listen_bus", "duration_ms": 100}])
        result = validate_execution(make_context(ir))
        assert result.ok, [d.code for d in result.errors]
        warnings_with_prefix(result, "chk_plan")

    def test_unconfirmed_items_inherited_from_executable_gate(self):
        ir = make_ir(unconfirmed_items=["周期阈值 20ms 待确认"])
        result = validate_execution(make_context(ir))
        assert not result.ok
        codes = [d.code for d in result.errors]
        assert "unconfirmed_items" in codes


# ---------------------------------------------------------------------------
# 结构级错误原样透传
# ---------------------------------------------------------------------------


class TestStructuralPassthrough:
    def test_structure_errors_forwarded_verbatim(self):
        ir = make_ir()
        del ir["case_id"]  # 违反 JSON Schema required
        result = validate_execution(make_context(ir))
        assert not result.ok
        gate = validate_executable(ir)
        assert [d.code for d in result.diagnostics] == [d.code for d in gate.diagnostics]
        assert [d.message for d in result.diagnostics] == [d.message for d in gate.diagnostics]
        # 透传时不进入七类检查
        assert not [d for d in result.diagnostics if d.code.startswith("chk_")]
