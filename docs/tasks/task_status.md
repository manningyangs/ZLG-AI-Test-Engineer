# 任务状态追踪（task_status）

**更新：** 2026-11-18（Prompt 00 两轮盘点 + P1 首批任务）

| 任务 ID | 状态 | 提交/证据 | 阻塞/备注 |
|---|---|---|---|
| Prompt-00 | Done | 942b7f9、f3ff97e、1d4c050（docs/tasks/*） | — |
| ADR-0001 | Accepted | docs/decisions/ADR-0001 | 2026-11-18 人工批准 |
| ARCH-01 | Done | src/zlg_ai_test_engine/domain/plugins/contracts.py、plugins/{registry.py,fakes.py,manifest.schema.json}、tests/contract/（24 断言）、docs/architecture/plugin_contracts.md | — |
| CORE-01 | Not Started | — | 待下一批任务 |
| PLAT-01 | Done | src/zlg_ai_test_engine/ui/*、app/main.py、tests/ui/（11 通过） | 未做人工 UI 走查 |
| PLAT-02 | Done | infrastructure/persistence/、application/project_service.py、alembic/（0001 迁移）、tests/unit/test_project_service.py、tests/integration/ | — |
| PLAT-03 | Done | application/{config_service,audit_service}.py、infrastructure/{secrets,logging}/、tests/unit/{test_config_and_audit,test_logging_redaction}.py | — |
| MOCK-01 / HOST-01 | Not Started | — | 依赖 ARCH-01（已就绪） |
| P0-01～06 | Blocked / Not Started | — | 外部：SDK 包、样机、ZXDoc API 材料、许可书面确认 |
| P2 全部 | Not Started | — | 依赖 CORE-01 |
| P3 / P4 | Blocked | — | P0 未完成 Go/No-Go |
| P5 / P6 | Deferred | — | — |

**测试基线：** `pytest tests` = 63 passed（unit 24 / contract 24 / ui 11 / integration 4）。
离屏窗口冒烟 SMOKE_OK；console entry 4 秒存活验证通过。

**未解决：** 代理网络不稳（push 偶发 TLS 失败）；dirty 关闭对话框分支无自动化覆盖；有显示器环境未人工走查。
