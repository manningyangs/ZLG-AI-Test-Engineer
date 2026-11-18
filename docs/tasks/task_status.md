# 任务状态追踪（task_status）

**更新：** 2026-11-18（Prompt 00 + P1 两批 + P2 全部 + P0 文档线 + 评测集 + AI-01/02 实现）

**P2 阶段状态：全部任务 Done（实现层）**；唯一待实测项 = 真实模型评测跑批（等 API Key）。
**验收对照：** 见 [p2_acceptance_report.md](p2_acceptance_report.md)（TC-01~07 全通过( Mock 层)、P2 退出条件 3/3、§15.1 部分硬件项随 P3）。

| 任务 ID | 状态 | 提交/证据 | 阻塞/备注 |
|---|---|---|---|
| Prompt-00 / ADR-0001 | Done / Accepted | docs/tasks、docs/decisions | — |
| ARCH-01 | Done | contracts/registry/fakes、tests（24） | — |
| CORE-01 | Done | test_ir/*、schemas、examples、tests（25） | draft/executable 两级 |
| PLAT-01/02/03 | Done | ui 壳、persistence、config/audit、tests | 未人工 UI 走查 |
| MOCK-01 | Done | mock_adapter.py、tests（17） | realtime 可选项未做 |
| HOST-01 | Done | ipc/*、tests（11） | ctypes Named Pipe |
| DATA-01 | Done | network/*、parser_dbc.py、tests（51） | 外部基准对照待人工（V） |
| VALID-01 | Done | validator.py、tests（32） | 七类检查 |
| COMP-01 | Done | compiler.py、tests（23） | 确定性哈希 |
| EXEC-01a | Done | orchestrator.py、runner.py、tests（21） | evidence 挂载+state_listener |
| JUDGE-01 | Done | judge.py、tests（21） | 无证据绝不 Pass |
| UI-01 | Done | ui/{workbench,case_editor,generation_stub}.py、tests/ui（30 含 19 新） | 离线桩生成（AI-01 未接入） |
| UI-02 | Done | ui/{run_controller,monitor,results}.py | QThread 后台运行+急停接线 |
| REPORT-01 | Done | application/reporting.py、tests（14）、docs/reports.md | append-only 证据+HTML/PDF |
| 集成门面 | Done | application/pipeline.py、tests（4）+ test_exec_pipeline（9） | UI/报告共用装配 |
| P0-01 | Done（清单） | docs/device/vendor_questionnaire.md（Q1~Q10） | **答复全 Open**：需人工发往周立功 |
| P0-05 | Done（登记） | docs/device/{zxdoc_api_gap,manual_evidence_notes}.md | 40 项四态：19 已确认/16 待实测/5 未找到；实体材料 9 项未获取（M1~M9） |
| P0-06 | Done（初版） | docs/security/licensing_and_distribution.md | D 级 p1 原文已回填；Q8 书面答复后升版 |
| P0-02/03/04 | Blocked | — | 等 Q1/Q2 答复（SDK 包+样机） |
| AI-评测前置 | Done | examples/ai_eval/、ai_eval/*、tests/eval（15）、scripts/eval_ai.py、docs/ai/eval_set.md | 桩基线 9/12，3 缺口钉住 |
| AI-01 | Done（实现） | ai/{prompt_builder,generation_service}.py、plugins/llm_providers/*、tests（13+40） | 五道后置门；强假 LLM 评测 12/12；**真实模型跑批待 API Key** |
| AI-02 | Done（实现） | ai/context_snapshot.py、tests（6）、docs/ai/generation_pipeline.md | 快照 stale + 外发过滤 |
| P3+ | Blocked / Deferred | — | P0 Go/No-Go 前不接真实 SDK |

**测试基线：** `pytest tests` = **389 passed**（contract 24 / unit 294 / ui 30 / integration 19 / eval 20）。
离屏真实启动 SMOKE_OK；HTML 报告真实导出验证通过。

**未解决：** 代理网络不稳（push 偶发 TLS 失败）；有显示器 UI 人工走查未做；
DBC 外部基准对照待人工；编辑器保存为内存快照。
