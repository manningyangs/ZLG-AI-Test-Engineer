"""Test IR 校验、序列化与版本迁移（CORE-01）。

两级校验：
- ``validate_ir``（draft 级）：JSON Schema 结构校验 + Pydantic 语义校验。
  允许草稿存在未确认项与预留动作（只出诊断，不阻断设计阶段）。
- ``validate_executable``（可执行级）：在 draft 级之上强制：
  * ``unconfirmed_items`` 必须为空（未确认关键阈值阻断执行）；
  * 不得包含 MVP 预留动作（inject_fault / uds_request）；
  * 每个断言必须有证据来源（assert_timeout 的 evidence_source 也必填）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from zlg_ai_test_engine.domain.plugins.contracts import Diagnostic, Severity
from zlg_ai_test_engine.domain.test_ir.actions import RESERVED_ACTIONS
from zlg_ai_test_engine.domain.test_ir.models import IR_SCHEMA_VERSION, TestIRDocument

#: 断言类动作（可执行级要求 evidence_source）
_ASSERT_ACTIONS = frozenset({"assert_cycle", "assert_range", "assert_enum", "assert_timeout"})


class IRValidationError(Exception):
    """IR 校验失败（携带诊断列表）。"""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        summary = "；".join(d.message for d in diagnostics[:5])
        super().__init__(f"Test IR 校验失败（{len(diagnostics)} 项）：{summary}")


class IRMigrationError(Exception):
    """IR 版本迁移失败（不支持的 schema_version）。"""


@dataclass(frozen=True)
class ValidationResult:
    """校验结果：diagnostics 中无 ERROR 即 ok。"""

    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not any(d.severity == Severity.ERROR for d in self.diagnostics)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == Severity.ERROR)

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise IRValidationError(list(self.errors))


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """加载打包进包资源的 Test IR JSON Schema（与根目录 schemas/ 有同步测试）。"""
    schema_text = (
        resources.files("zlg_ai_test_engine")
        / "resources"
        / "test_ir"
        / "v1"
        / "test_ir.schema.json"
    ).read_text(encoding="utf-8")
    return json.loads(schema_text)


def _schema_diagnostics(document: Mapping[str, Any]) -> list[Diagnostic]:
    validator = Draft202012Validator(load_schema())
    diagnostics: list[Diagnostic] = []
    for err in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        diagnostics.append(
            Diagnostic(
                code="ir_schema",
                message=f"{location}: {err.message}",
                severity=Severity.ERROR,
                location=location,
            )
        )
    return diagnostics


def _model_diagnostics(document: Mapping[str, Any]) -> list[Diagnostic]:
    try:
        TestIRDocument.model_validate(document)
    except ValidationError as exc:
        diagnostics: list[Diagnostic] = []
        for err in exc.errors():
            location = "/".join(str(p) for p in err.get("loc", ())) or "<root>"
            diagnostics.append(
                Diagnostic(
                    code="ir_model",
                    message=f"{location}: {err.get('msg', 'invalid')}",
                    severity=Severity.ERROR,
                    location=location,
                )
            )
        return diagnostics
    return []


def validate_ir(document: Mapping[str, Any]) -> ValidationResult:
    """draft 级校验：结构（JSON Schema）+ 语义（Pydantic）。"""
    diagnostics = _schema_diagnostics(document)
    if not diagnostics:  # 结构合法才做语义校验，避免重复报错
        diagnostics = _model_diagnostics(document)
    return ValidationResult(diagnostics=tuple(diagnostics))


def validate_executable(document: Mapping[str, Any]) -> ValidationResult:
    """可执行级校验：draft 级 + 阻断未确认项与预留动作。"""
    base = validate_ir(document)
    if not base.ok:
        return base

    extra: list[Diagnostic] = list(base.diagnostics)

    unconfirmed = document.get("unconfirmed_items") or []
    if unconfirmed:
        extra.append(
            Diagnostic(
                code="unconfirmed_items",
                message=f"存在 {len(unconfirmed)} 项未确认参数，执行被阻断：{unconfirmed}",
                severity=Severity.ERROR,
            )
        )

    for step in document.get("steps", []):
        action = step.get("action")
        if action in RESERVED_ACTIONS:
            extra.append(
                Diagnostic(
                    code="reserved_action",
                    message=f"步骤 {step.get('id')} 使用了 MVP 预留动作 {action}，默认禁止执行",
                    severity=Severity.ERROR,
                    location=str(step.get("id")),
                )
            )
        if action in _ASSERT_ACTIONS and not step.get("evidence_source"):
            extra.append(
                Diagnostic(
                    code="missing_evidence_source",
                    message=f"步骤 {step.get('id')}（{action}）缺少 evidence_source，无来源的阈值不得执行",
                    severity=Severity.ERROR,
                    location=str(step.get("id")),
                )
            )

    return ValidationResult(diagnostics=tuple(extra))


def parse_ir(document: Mapping[str, Any]) -> TestIRDocument:
    """解析为强类型模型；失败抛 IRValidationError（不静默容错）。"""
    result = validate_ir(document)
    result.raise_if_failed()
    return TestIRDocument.model_validate(document)


def to_json(document: TestIRDocument, *, indent: int | None = 2) -> str:
    """序列化（排除 None 可选字段；键序稳定，便于哈希与对比）。"""
    return document.model_dump_json(indent=indent, exclude_none=True)


def migrate_ir(document: Mapping[str, Any]) -> dict[str, Any]:
    """版本迁移接口：当前仅支持 1.0（恒等）；未知版本抛 IRMigrationError。

    后续 major 版本在此登记迁移链（1.0 → 1.1 → …）。
    """
    version = document.get("schema_version")
    if version is None:
        raise IRMigrationError("缺少 schema_version，无法迁移")
    if version == IR_SCHEMA_VERSION:
        return dict(document)
    raise IRMigrationError(
        f"不支持的 schema_version={version!r}（当前宿主支持 {IR_SCHEMA_VERSION}）"
    )
