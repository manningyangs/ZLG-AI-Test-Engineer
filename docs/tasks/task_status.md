# 任务状态追踪（task_status）

**更新：** 2026-11-18（Prompt 00 + P1 两批 + P2 首批 DATA/VALID/COMP/EXEC/JUDGE）

| 任务 ID | 状态 | 提交/证据 | 阻塞/备注 |
|---|---|---|---|
| Prompt-00 | Done | 942b7f9、f3ff97e、1d4c050（docs/tasks/*） | — |
| ADR-0001 | Accepted | docs/decisions/ADR-0001 | 2026-11-18 人工批准 |
| ARCH-01 | Done | domain/plugins/contracts.py、plugins/*、tests/contract/（24）、docs/architecture/plugin_contracts.md | — |
| CORE-01 | Done | schemas/test-ir/v1/、domain/test_ir/、examples/test-ir/、tests（25）、docs/test-ir/ | draft/executable 两级校验 |
| PLAT-01/02/03 | Done | ui/*、persistence/*、config/audit/secrets/logging、tests（23+） | 未做人工 UI 走查 |
| MOCK-01 | Done | plugins/mock_adapter.py、tests（17）、docs/device/mock_adapter.md | realtime 模式未实现（可选） |
| HOST-01 | Done | infrastructure/ipc/*、tests（11）、docs/api/ipc.md | ctypes 原生 Named Pipe |
| DATA-01 | Done | domain/network/*、plugins/parser_dbc.py、examples/dbc/*、tests（51）、docs/parsers/dbc.md | cantools 45.2 底层；外部基准工具对照待人工（V 级） |
| VALID-01 | Done | domain/validation/validator.py、tests（32）、docs/architecture/validation_and_compiler.md | 七类检查，ERROR 含修正建议 |
| COMP-01 | Done | domain/execution/compiler.py、tests（23） | 确定性 canonical json 哈希 |
| EXEC-01a | Done | domain/execution/{orchestrator,runner}.py、tests（21） | 14 态状态机+审批门+跨线程取消 |
| JUDGE-01 | Done | domain/execution/judge.py、tests（21） | 无证据绝不 Pass；inputs_digest 复算 |
| 集成 | Done | tests/integration/test_exec_pipeline.py（9） | VALID→COMP→EXEC→JUDGE 全链路 + DBC 接线 |
| AI-01/02 | Not Started | — | 需模型 Provider 与 API Key（用户侧） |
| UI-01/02、REPORT-01 | Not Started | — | P2 后半段 |
| P0-01～06 | Blocked / Not Started | — | 外部：SDK 包、样机、ZXDoc API 材料、许可 |
| P3 / P4 / P5 / P6 | Blocked / Deferred | — | P0 Go/No-Go 前不接真实 SDK |

**测试基线：** `pytest tests` = **273 passed**（contract 24 / unit 209 / ui 11 / integration 29）。
全量含：DBC 接线（DBC GenMsgCycleTime 驱动场景与断言）、审批门、unknown 能力阻断、
跨线程取消、AI 只解释不改判、确定性复跑一致性。

**未解决：** 代理网络不稳（push 偶发 TLS 失败）；dirty 关闭对话框分支与有显示器 UI 走查未做；
DBC 与外部基准工具对照待人工；信号断言 decoder 的 NetworkModel 完整装配待 P2-D。
