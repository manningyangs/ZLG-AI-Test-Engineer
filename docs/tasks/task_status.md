# 任务状态追踪（task_status）

**更新：** 2026-11-18（Prompt 00 + P1 第一批 ARCH/PLAT + 第二批 CORE/MOCK/HOST）

| 任务 ID | 状态 | 提交/证据 | 阻塞/备注 |
|---|---|---|---|
| Prompt-00 | Done | 942b7f9、f3ff97e、1d4c050（docs/tasks/*） | — |
| ADR-0001 | Accepted | docs/decisions/ADR-0001 | 2026-11-18 人工批准 |
| ARCH-01 | Done | domain/plugins/contracts.py、plugins/*、tests/contract/（24 断言）、docs/architecture/plugin_contracts.md | — |
| CORE-01 | Done | schemas/test-ir/v1/、domain/test_ir/、examples/test-ir/、tests/unit/test_test_ir.py（25）、docs/test-ir/README.md | draft/executable 两级校验 |
| PLAT-01 | Done | ui/*、app/main.py、tests/ui/（11） | 未做人工 UI 走查 |
| PLAT-02 | Done | infrastructure/persistence/、application/project_service.py、alembic/（0001 迁移）、tests | — |
| PLAT-03 | Done | application/{config_service,audit_service}.py、infrastructure/{secrets,logging}/、tests | — |
| MOCK-01 | Done | plugins/mock_adapter.py（810 行）、tests/unit/test_mock_adapter.py（17）、docs/device/mock_adapter.md | realtime 模式未实现（可选项） |
| HOST-01 | Done | infrastructure/ipc/（4 文件）、tests/integration/test_ipc_host.py（11）、docs/api/ipc.md | ctypes 原生 Named Pipe；单客户端串行语义 |
| P0-01～06 | Blocked / Not Started | — | 外部：SDK 包、样机、ZXDoc API 材料、许可书面确认 |
| P2 全部 | Not Started | — | 依赖项已就绪（ARCH/CORE/PLAT/MOCK/HOST） |
| P3 / P4 | Blocked | — | P0 未完成 Go/No-Go |
| P5 / P6 | Deferred | — | — |

**测试基线：** `pytest tests` = **116 passed**（contract 24 / unit 71 / ui 11 / integration 10）。
离屏窗口冒烟 SMOKE_OK；console entry 存活验证通过；IPC 集成含 kill-host 崩溃恢复且无孤儿进程。

**未解决：** 代理网络不稳（push 偶发 TLS 失败）；dirty 关闭对话框分支无自动化覆盖；有显示器环境未人工走查。
