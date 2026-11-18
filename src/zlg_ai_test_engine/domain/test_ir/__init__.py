"""CORE-01：Test IR v1 —— 协议/SDK/脚本目标无关的统一测试任务模型。"""

from zlg_ai_test_engine.domain.test_ir.actions import (
    ACTION_SPECS,
    MVP_ACTIONS,
    RESERVED_ACTIONS,
    ActionSpec,
)
from zlg_ai_test_engine.domain.test_ir.models import (
    IR_SCHEMA_VERSION,
    ExecutionPolicy,
    Provenance,
    TestIRDocument,
    TestStep,
)
from zlg_ai_test_engine.domain.test_ir.validation import (
    IRMigrationError,
    IRValidationError,
    ValidationResult,
    load_schema,
    migrate_ir,
    parse_ir,
    to_json,
    validate_executable,
    validate_ir,
)

__all__ = [
    "ACTION_SPECS",
    "MVP_ACTIONS",
    "RESERVED_ACTIONS",
    "ActionSpec",
    "IR_SCHEMA_VERSION",
    "ExecutionPolicy",
    "Provenance",
    "TestIRDocument",
    "TestStep",
    "IRMigrationError",
    "IRValidationError",
    "ValidationResult",
    "load_schema",
    "migrate_ir",
    "parse_ir",
    "to_json",
    "validate_executable",
    "validate_ir",
]
