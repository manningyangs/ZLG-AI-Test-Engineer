# P2 离线评测集（AI-01 固定提示集）

**任务：** AI-01 评测前置 + QA-02 贯穿｜**状态：** 已建立（桩基线 9/12）｜**日期：** 2026-11-18

## 用途

任务书 AI-01 要求"固定 Prompt Evaluation 覆盖至少 5 类需求 + 负向样本"。本评测集把期望固化为
**生成器无关的真值**：现在钉住离线桩基线，接入真实 LLMProvider 后复跑同一集合直接对比。

| 文件 | 说明 |
|---|---|
| `examples/ai_eval/eval_set.json` | 12 个用例：5 正向（周期/范围/枚举/超时/发送）+ 7 负向 |
| `src/zlg_ai_test_engine/ai_eval/` | 用例模型（cases）、检查器与报告（evaluator）、生成器适配（adapters） |
| `tests/eval/test_eval_set.py` | 集合完整性 + 评测器方向自检 + 桩基线钉住（15 项） |
| `scripts/eval_ai.py` | 跑批 CLI（表格 + JSON 报告 + 退出码） |

## 用例格式（expect 字段即检查开关）

- `must_produce`：`yes` 必须产出｜`no` 必须拒绝｜`flagged` 拒绝**或**产出并标注冲突；
- `expected_action`：IR 步骤必须含该 MVP 动作；
- `prompt_target`：提示点名的目标（message/signal/can_id）必须出现在 IR（目标提取能力）；
- `source_in_prompt`：阈值来源抗编造检查（来源出自提示 **或** 明确列为待确认，二选一即通过；编造即失败）；
- `draft_must_have_unconfirmed`：草稿必须有待确认项（无来源阈值不得当已确认事实）；
- `send_requires_approval`：发送类必须 `requires_approval=true`；
- `refuse_keywords`：拒绝时的原因须命中关键词（文案质量门）；
- `refs_in_context`（隐式）：IR 引用的 message/signal 必须存在于用例上下文。

## 检查项字典

`produced / refused / refusal_reason / conflict_flagged / schema_valid / action_match /
target_from_prompt / refs_in_context / no_fabricated_source / draft_unconfirmed /
send_requires_approval / confirmed_executable(skip 若无定稿)`

## 当前基线（离线桩，2026-11-18）

```
通过 9/12（75%）
已知缺口（= AI-01 必须补齐的方向）：
  EVAL_N_UNKNOWN_SIGNAL   上下文外信号仍被关键词匹配产出（未做引用核对）
  EVAL_N_UNKNOWN_MESSAGE  上下文外报文同上
  EVAL_N_CONFLICT         冲突阈值未识别/未标注
```
缺口由 `tests/eval/test_eval_set.py::STUB_KNOWN_GAPS` 钉住：桩行为变化或 AI-01 补齐后
测试立即提示调整清单。

## 接入真实 LLMProvider（AI-01）

实现 `ai_eval.evaluator.Generator` 协议即可（无需改评测器）：

```python
class LLMGenerator:
    def __call__(self, prompt, context) -> GenerationOutput:
        ...  # 调用 domain 层 IR 生成管线，返回 ir/ir_confirmed/unconfirmed/hint
```

随后在 `scripts/eval_ai.py` 注册 `--generator llm`，复跑并对比桩基线；
评测报告含逐检查项明细，可直接作为 AI-01 验收证据（固定提示集评测报告）。

## 注意

- 期望是真值，**不为任何生成器放宽**；确需调整期望必须改用例并说明理由（版本 set_version）。
- N4/N5（预留动作/超范围）当前仅要求拒绝，原因文案关键词门待 AI-01 验收时恢复（见用例 notes）。
- 评测集只测"生成"，执行侧门禁由 VALID-01/COMP-01/EXEC 的测试覆盖。
