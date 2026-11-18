# AI-01/02 生成管线与上下文管理

**任务：** AI-01（需求→Test IR）+ AI-02（RAG/上下文）｜**状态：** 已实现（待真实模型实测）｜**日期：** 2026-11-18

## 组件

| 模块 | 职责 |
|---|---|
| `ai/prompt_builder.py` | 系统规则提示 + 信封 Schema（decision/三组标注/ir）+ `build_messages`（只发清单化上下文，不传完整工程） |
| `ai/generation_service.py` | 编排调用 + **确定性后置门** + Provenance + ModelInvocation 审计 |
| `ai/context_snapshot.py` | 内容寻址快照、知识源元数据、外发过滤（AI-02） |
| `plugins/llm_providers/` | OpenAI-compatible 实现（可注入 Transport，错误分类/重试/用量） |
| `ai_eval/adapters.LLMGenerator` | 管线接入评测协议（复跑 eval_set 对比桩基线） |

## 确定性后置门（不信任模型自觉）

1. **结构门**：信封 JSON 解析 + `validate_ir`（Draft 2020-12）；失败携带诊断**重试 ≤3 次**（修
   复提示带上失败原因），用尽 → 拒绝并注明"经 N 次校验未通过"。
2. **引用核验**：ir 的 message/signal 必须逐字 ∈ 上下文清单；幻觉引用 → **服务级拒绝**
   （附澄清问题），与模型是否自觉无关。
3. **来源标注**：`evidence_source` 不在用户原文且未被模型列入确认项 → 自动追加待确认
   （草案可留值；执行级 `validate_executable` 必阻断）。
4. **草案必确认**：`unconfirmed_items` 至少 1 条 —— AI 草案永不直接可执行。
5. **风险改写**：含 send_* → 强制 `L1 + requires_approval=true`（模型谎报 L0 会被改写）。
6. **Provenance**：model / prompt 版本（`ai01-prompt-v1`）/ 提示哈希 / 生成时刻 / 快照 id。

## AI-02 上下文策略

- `ContextSnapshot`：内容寻址（同输入同 id），记录资产 sha256/解析器版本/状态 +
  知识源元数据（来源/版本/适用协议型号/许可/机密标记）；`stale_assets()` 检测哈希变化
  → UI 应提示"工程已更新，建议重新生成"。
- `snapshot_allows_upload(local_only=True)` 默认**全部知识不外发**；机密永不外发；
  协议/型号不匹配显式排除并记录（不得无提示进入生成上下文）。
- `ModelInvocation` 审计：只记 prompt 哈希、database、上下文名录、输出哈希、token 用量
  ——不记完整工程、不记密钥。

## 测试与评测

| 测试 | 覆盖 |
|---|---|
| `tests/unit/test_ai_generation.py`（13） | 五道门各自正反例、重试/用尽、AUTH 上抛、审计行与脱敏 |
| `tests/unit/test_context_snapshot.py`（6） | 内容寻址、stale 三态、外发过滤三条规则 |
| `tests/eval/test_ai_eval_llm.py`（5） | **强假 LLM → 真实管线 → 评测集 12/12**；桩 3 缺口全关闭；服务级兜底（劣化模型幻觉引用仍被拒） |

**离线桩基线 9/12 → AI-01 管线 12/12**（差值恰为上下文感知×2 + 冲突识别）。

## 待真实模型实测（阻塞：用户提供 API Key）

- `build_provider(ConfigService)`（OpenAI 兼容端点）→ `GenerationService` →
  `scripts/eval_ai.py --generator llm` 实跑出**真实模型评测报告**（AI-01 验收证据）；
- 固定提示集下错误输出分布、修复重试率、token 成本（ModelInvocation 已留用量字段）。
