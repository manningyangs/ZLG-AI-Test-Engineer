"""P2-D 应用门面：装配离线 Mock 执行管线（UI-02、REPORT-01 与演示共用）。

职责：
- ``build_mock_orchestrator``：一键装配 MockAdapter + VALID-01 + COMP-01 + EXEC-01a；
- ``load_example_irs``：加载仓库内置 Test IR 样例（UI 工作台/编辑器的数据源）。

说明（离线演示口径）：默认 asset_registry 采用“IR 自声明”策略——把 IR 的
protocol.database / database_refs 视为已导入且 active。这只用于无工程上下文的
离线演示；真实项目必须由 ProjectService 提供真实资产注册表（含哈希），
校验逻辑本身（VALID-01）不变且已有独立严格测试。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from zlg_ai_test_engine.application.audit_service import AuditService
from zlg_ai_test_engine.domain.execution.compiler import compile_to_mock_plan
from zlg_ai_test_engine.domain.execution.orchestrator import ExecutionOrchestrator, RunRecord
from zlg_ai_test_engine.domain.plugins.contracts import DeviceSDKAdapter
from zlg_ai_test_engine.domain.validation import ValidationContext, validate_execution
from zlg_ai_test_engine.plugins.mock_adapter import MockAdapter

#: 仓库根（src 布局：src/zlg_ai_test_engine/application/pipeline.py → parents[3]）
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _self_declared_registry(ir: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """离线演示用：IR 自声明的资产视为已导入且 active（真实项目走 ProjectService）。"""
    registry: dict[str, dict[str, str]] = {}
    database = (ir.get("protocol") or {}).get("database")
    if database:
        registry[str(database)] = {"sha256": "", "status": "active", "type": "dbc"}
    for ref in ir.get("database_refs") or []:
        if isinstance(ref, Mapping) and ref.get("asset_id"):
            registry[str(ref["asset_id"])] = {
                "sha256": str(ref.get("sha256") or ""),
                "status": "active",
                "type": "dbc",
            }
    return registry


def build_mock_orchestrator(
    *,
    adapter: DeviceSDKAdapter | None = None,
    audit: AuditService | None = None,
    message_ids: Mapping[str, int] | None = None,
    signal_ids: Mapping[str, int] | None = None,
    signal_decoder: Callable[[str, bytes], float | int] | None = None,
    approval: Mapping[str, Any] | None = None,
    ai_explainer: Callable[[Sequence[Any], Mapping[str, Any]], str] | None = None,
    state_listener: Callable[[RunRecord], None] | None = None,
    network_model: Mapping[str, Any] | None = None,
    device_id: str = "mock-0",
) -> ExecutionOrchestrator:
    """装配完整离线管线：Mock 适配器 + VALID-01 + COMP-01 + EXEC-01a。

    - ``approval`` 默认 L0/无授权（受控发送会在 VALID-01 被阻断，符合安全默认）；
    - ``state_listener`` 每次状态迁移回调（UI 实时刷新）；
    - ``network_model`` 提供时启用 message/signal 引用的严格存在性检查。
    """
    adapter = adapter if adapter is not None else MockAdapter()
    caps = adapter.device_capabilities(device_id)

    def validator(ir: Mapping[str, Any]):
        return validate_execution(
            ValidationContext(
                ir=dict(ir),
                asset_registry=_self_declared_registry(ir),
                network_model=network_model,
                capabilities=caps,
                approval=dict(
                    approval
                    or {"user_level": "L0", "approved_actions": [], "environment": "offline"}
                ),
                resource_state={
                    "device_busy": False,
                    "busy_channels": [],
                    "periodic_active": False,
                    "queue_active": False,
                },
            )
        )

    def compiler(ir: Mapping[str, Any]):
        return compile_to_mock_plan(ir, capabilities=caps)

    return ExecutionOrchestrator(
        adapter=adapter,
        compiler=compiler,
        device_id=device_id,
        audit=audit,
        extra_validators=[validator],
        message_ids=dict(message_ids or {}),
        signal_ids=dict(signal_ids or {}),
        signal_decoder=signal_decoder,
        ai_explainer=ai_explainer,
        state_listener=state_listener,
    )


def load_example_irs() -> list[dict[str, Any]]:
    """加载 examples/test-ir/valid_*.json 样例；仓库不存在时返回空列表。"""
    examples_dir = _REPO_ROOT / "examples" / "test-ir"
    if not examples_dir.is_dir():
        return []
    docs: list[dict[str, Any]] = []
    for path in sorted(examples_dir.glob("valid_*.json")):
        try:
            docs.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue  # 单个损坏样例不影响其余加载（工作台容错）
    return docs
