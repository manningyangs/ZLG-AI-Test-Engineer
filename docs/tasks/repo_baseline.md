# 仓库基线盘点报告（Prompt 00）

**任务 ID：** Prompt 00 / 仓库盘点与执行准备
**状态：** Done
**日期：** 2026-11-18
**执行原则：** 只盘点、不写业务代码、不删除/覆盖/重构现有内容、不提交厂商二进制或真实 API Key。

---

## 1. 目录树与仓库现状

```
ZLG-AI-Test-Engineer/          （本地目录：e:\desktop\ZLG AI Test Engineer）
├─ .gitignore                   # 仅忽略 .screenshots/ 和 *.tmp；缺 Python/IDE/厂商本地目录规则
├─ LICENSE                      # AGPL-3.0-only（GitHub 模板生成）
├─ README.md                    # 中英双语项目介绍（含免责声明、特性、路线图）
├─ ZLG_AI_Test_Engineer_产品需求与技术架构及项目开发计划_V1.0.docx      # PRD/架构/计划
├─ ZLG_AI_Test_Engineer_开发任务拆解_V1.0.md                          # 任务拆解（P0–P6）
├─ ZLG_AI_Test_Engineer_Trae_可执行开发任务书_V1.0.md / .docx          # Trae 执行任务书 V1.0
└─ .screenshots/                # 空目录（已 gitignore）
```

**结论：纯文档仓库，零业务代码。** 任务书 2.2 建议的 `src/`、`schemas/`、`native/`、`plugins/`、`tests/`、`docs/`、`scripts/`、`examples/` 结构均不存在。

## 2. 技术栈 / 依赖 / 启动方式 / 测试方式

| 项 | 现状 |
|---|---|
| 代码技术栈 | 无（尚未初始化） |
| 依赖声明 | 无 pyproject.toml / requirements |
| 启动方式 | 无 |
| 测试方式 | 无测试框架、无测试目录 |
| Git 状态 | 分支 main；2 个提交（2cc943b Initial commit、f131d45 docs）；工作区有 2 个未跟踪文件（任务书 md/docx），无未提交修改 |
| 本机工具链 | Python 3.12.10（py 启动器）、pip 26.2.1、Node.js（D:\NodeJs）、git、gh（已登录 manningyangs，网络需 127.0.0.1:7897 代理）；pandoc 不可用 |

任务书推荐技术栈（Python 3.11+ / PySide6 / Pydantic v2 / SQLAlchemy 2.x / Alembic / cantools 评估 / C++17 x64 / Named Pipe / pytest）在本机均可实施：Python 3.12 ≥ 3.11 要求；C++ 编译器（MSVC）在 P0 前不作要求，待 P0-02 确认。

## 3. 现有功能与偏离检查

- 现有功能：无。无未提交的功能修改。
- 密钥风险：仓库内未发现 API Key、凭据、.env 等敏感文件（纯文档）。
- 厂商 SDK 文件：**未发现** zlgcan.dll / zlgcan.h / kerneldlls / 驱动 / Demo 等任何厂商二进制或头文件。合规状态良好（符合 P0-06 基线要求）。
- 分层约束检查：无代码，暂不适用；后续按任务书第 2.3 节架构红线执行。

## 4. 对照任务书的任务追踪结论

详细逐项状态见 [task_traceability.csv](task_traceability.csv)。汇总：

| 阶段 | 任务数 | 状态 |
|---|---|---|
| Prompt 00 仓库盘点 | 1 | **Done**（本报告） |
| P0-01～P0-06 | 6 | Not Started；其中 P0-02/03/04 预期 Blocked（无 SDK 包/样机） |
| P1（ARCH-01、CORE-01、PLAT-01~03、MOCK-01、HOST-01） | 7 | Not Started（全部缺失，可立即启动） |
| P2（DATA-01、AI-01/02、VALID-01、COMP-01、EXEC-01a、JUDGE-01、UI-01/02、REPORT-01） | 10 | Not Started（不依赖硬件，待 P1） |
| P3（SDK-01~04、COMP-02、EXEC-01b、UI-03、实机验收） | 6 | Blocked（等待 P0 外部依赖） |
| P4（ZXDOC-01/02/03、UDS-01/02/03） | 6 | Blocked（等待厂商 API 材料） |
| P5（PROTO-*、REPLAY-01） | 6 | Deferred（按客户场景立项） |
| P6（PROD-01~06） | 6 | Not Started（依赖 MVP 验收） |
| 贯穿（QA/DOC 复盘类） | 若干 | Not Started（每阶段执行） |

**阻塞项（P0 外部依赖清单，需人工向周立功索取）：**
1. zlgcan.dll / zlgcan.h / .lib / kerneldlls / 官方 C++/Python Demo（P0-01 第 2 项）
2. MVP 参考设备型号与可借测样机（P0-01 第 3 项）
3. 设备能力表逐型号确认（P0-01 第 4 项）
4. ZXDoc DataTransmit.py、Python 脚本说明、第三方 API 完整签名（P0-05）
5. LGPLv3 / SDK 再分发书面许可（P0-06）
6. 可公开演示的 DBC/ODX/历史日志样本（P0-01 第 7 项）

## 5. 建议实施顺序

按任务书第 18 章：

1. ✅ Prompt 00（本报告）→ 等待人工检查。
2. 并行启动 P0-01/P0-05/P0-06（厂商沟通线，人工发起，最外部、周期最长）。
3. ARCH-01 + PLAT-01/02/03（不依赖硬件，可立即开始）。
4. CORE-01 + MOCK-01 + HOST-01。
5. P2 离线闭环（DATA-01 → AI-01 → VALID-01/COMP-01 → EXEC-01a/JUDGE-01 → UI-01/02 → REPORT-01）。
6. P0 实机条件就绪后再启动 P3；在此之前 SDK-01 仅可搭 Fake Host 骨架。

## 6. 边界核对

- 未编写任何业务代码；未删除/覆盖/重构任何现有文件。
- 未提交厂商二进制、未提交真实 API Key。
- 本任务仅新增 `docs/tasks/` 与 `docs/decisions/` 下的盘点文档。
