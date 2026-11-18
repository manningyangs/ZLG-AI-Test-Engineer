"""评测用例模型与加载（examples/ai_eval/eval_set.json）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO_ROOT = Path(__file__).resolve().parents[3]


def resolve_default_set_path() -> Path:
    """默认评测集路径（仓库 examples/ai_eval/eval_set.json）。"""
    return _REPO_ROOT / "examples" / "ai_eval" / "eval_set.json"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptTarget(_Base):
    """提示中点名的生成目标（真值声明）。"""

    field: str = Field(pattern="^(message|signal|can_id)$")
    name: str | None = None
    value: int | str | None = None

    @model_validator(mode="after")
    def _value_present(self) -> "PromptTarget":
        if self.field == "can_id":
            if self.value is None:
                raise ValueError("can_id 目标必须给出 value")
        elif not self.name:
            raise ValueError(f"{self.field} 目标必须给出 name")
        return self


class Expectation(_Base):
    """生成期望（真值）。must_produce：

    - ``yes``：必须产出草稿 IR；
    - ``no``：必须拒绝（不允许产出）；
    - ``flagged``：允许拒绝，或产出但必须把冲突列入待确认。
    """

    must_produce: Literal["yes", "no", "flagged"] = "yes"
    expected_action: str | None = None
    prompt_target: PromptTarget | None = None
    source_in_prompt: bool | None = None
    draft_must_have_unconfirmed: bool | None = None
    conflict_flagged: bool | None = None
    send_requires_approval: bool | None = None
    refuse_keywords: list[str] = Field(default_factory=list)


class ContextMessage(_Base):
    name: str
    frame_id: int = Field(ge=0, le=0x1FFFFFFF)


class GenerationContext(_Base):
    """生成上下文（工程真值子集：生成器的引用只能来自这里）。"""

    database: str
    messages: list[ContextMessage]
    signals: list[str] = Field(default_factory=list)

    @property
    def message_names(self) -> set[str]:
        return {m.name for m in self.messages}

    @property
    def signal_names(self) -> set[str]:
        return set(self.signals)


class EvalCase(_Base):
    id: str = Field(pattern=r"^EVAL_[A-Z0-9_]+$")
    category: Literal["positive", "negative"]
    prompt: str = Field(min_length=1)
    context: GenerationContext
    expect: Expectation
    notes: str = ""


class EvalSet(_Base):
    set_version: str
    name: str
    description: str = ""
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "EvalSet":
        ids = [c.id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("评测用例 id 重复")
        return self

    @field_validator("cases")
    @classmethod
    def _sanity(cls, cases: list[EvalCase]) -> list[EvalCase]:
        for case in cases:
            exp = case.expect
            if case.category == "positive" and exp.must_produce == "no":
                raise ValueError(f"{case.id}: positive 用例不得要求 must_produce=no")
            if case.category == "negative" and exp.must_produce == "yes":
                # 允许"产草稿但必须被阻断"的负向（无来源阈值/发送安全）
                blocking = (
                    exp.source_in_prompt is not None
                    or exp.draft_must_have_unconfirmed
                    or exp.send_requires_approval
                )
                if not blocking:
                    raise ValueError(
                        f"{case.id}: negative 且 must_produce=yes 的用例必须依赖阻断型期望字段"
                        "（source_in_prompt / draft_must_have_unconfirmed / send_requires_approval）"
                    )
        return cases


def load_eval_set(path: Path | str | None = None) -> EvalSet:
    """加载并校验评测集；路径缺省为仓库默认集合。"""
    target = Path(path) if path is not None else resolve_default_set_path()
    data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    return EvalSet.model_validate(data)
