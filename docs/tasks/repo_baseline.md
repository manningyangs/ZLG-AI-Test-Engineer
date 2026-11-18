# 仓库基线盘点报告（Prompt 00 · 第 2 轮）

**任务 ID：** Prompt 00 / 仓库盘点与执行准备
**状态：** Done（替换 2026-11-18 首轮盘点）
**日期：** 2026-11-18
**执行原则：** 只盘点、不写业务代码、不删除/覆盖既有功能、不提交厂商二进制与真实 API Key。

---

## 1. 目录树与仓库现状

```
ZLG-AI-Test-Engineer/          （本地目录：e:\desktop\ZLG AI Test Engineer）
├─ .gitignore                   # 厂商二进制/Python/IDE 忽略规则（f3ff97e 加固）
├─ LICENSE                      # AGPL-3.0-only（GitHub 模板生成）
├─ README.md                    # 中英双语项目介绍（免责声明、特性、路线图）
├─ ZLG_AI_Test_Engineer_产品需求与技术架构及项目开发计划_V1.0.docx   # PRD/架构/计划
├─ ZLG_AI_Test_Engineer_开发任务拆解_V1.0.md                       # 任务拆解（P0–P6）
├─ ZLG_AI_Test_Engineer_Trae_可执行开发任务书_V1.0.md / .docx       # Trae 执行任务书 V1.0
├─ docs/
│  ├─ decisions/ADR-0001-技术栈与目录结构.md                        # 技术栈 ADR 草案
│  └─ tasks/repo_baseline.md、task_traceability.csv                # 本盘点与任务追踪
├─ ZXDoc_um.pdf                 [33.0MB，未跟踪·已 gitignore]      # ZXDoc 用户手册（厂商文件）
└─ ZXDoc_x64_v1.3.14-beta.1+build20261030.zip  [184MB，未跟踪·已 gitignore]  # ZXDoc x64 安装包（厂商文件）
```

**结论：仍为纯文档仓库，零业务代码。** 任务书 2.2 建议的 `src/`、`schemas/`、`native/`、`plugins/`、`tests/`、`scripts/`、`examples/` 结构均不存在。

**新变化（较首轮）：**
1. 首轮基线的 2 个提交已落地本地 main（942b7f9 盘点+任务书、f3ff97e gitignore 加固）。
2. 新增两个厂商文件（ZXDoc 手册 PDF、ZXDoc x64 安装包 zip），**均已被 .gitignore 排除，不入库**；其内容属 P0-05/P0-06 证据材料范畴，仅本地留存。

## 2. 技术栈 / 依赖 / 启动 / 测试 / Git

| 项 | 现状 |
|---|---|
| 代码技术栈 | 无（尚未初始化）；ADR-0001 草案已给出推荐栈待批准 |
| 依赖声明 | 无 pyproject.toml / requirements |
| 启动方式 / 测试方式 | 均不存在 |
| 工具链 | Python 3.12.10（py 启动器）、pip 26.2.1；Node.js（D:\NodeJs）；git、gh（登录账号 manningyangs）；pandoc 不可用 |
| Git 分支 | main 跟踪 origin/main，**本地领先 2 个提交未推送**（origin 停在 f131d45，本地 HEAD f3ff97e） |
| 远程推送 | 走 127.0.0.1:7897 代理；上一轮末期代理断连导致 push 失败，**待网络恢复后补推** |
| 密钥风险 | 无 API Key / .env / 凭据文件 |
| 厂商二进制入库 | 无。仓库内无 DLL/驱动/头文件/Demo；本地厂商文件（手册+安装包）已显式忽略 |

## 3. 分层与合规检查

- 现有功能：无代码，任务书 2.3 架构红线（ProtocolPlugin 不碰 DLL、Parser 不碰设备、UI 不判定结果、LLM 不执行工具动作）暂不适用，P1 起强制执行。
- .gitignore 覆盖：`vendor_sdk_local/`、`ZXDoc_um.pdf`、`ZXDoc_*.zip`、`*.dll`、`*.lib`、`kerneldlls/`、Python 缓存/虚拟环境、IDE 目录。
- 注意：`*.lib` 忽略规则将来可能误伤 Native Host 自身构建产物，属预期（构建产物本就不入库）。

## 4. 任务追踪结论

逐项见 [task_traceability.csv](task_traceability.csv)。汇总：

| 阶段 | 任务数 | 状态 |
|---|---|---|
| Prompt-00 | 1 | **Done**（本轮） |
| P0-01～06 | 6 | P0-01/05/06 Not Started（可人工发起）；P0-02/03/04 预期 Blocked（无 SDK/样机） |
| P1（ARCH-01、CORE-01、PLAT-01~03、MOCK-01、HOST-01） | 7 | Not Started（全部缺失，可立即启动） |
| P2（DATA-01、AI-01/02、VALID-01、COMP-01、EXEC-01a、JUDGE-01、UI-01/02、REPORT-01） | 10 | Not Started（不依赖硬件，待 P1） |
| P3（SDK-01~04、COMP-02、EXEC-01b、UI-03、实机验收） | 6 | Blocked（等待 P0 外部依赖） |
| P4（ZXDOC-*、UDS-*） | 6 | Blocked（等待厂商 API 材料；UDS-01 可先接口设计） |
| P5（PROTO-*、REPLAY-01） | 6 | Deferred（按客户场景立项） |
| P6（PROD-01~06） | 6 | Not Started / Deferred（依赖 MVP 验收） |

**外部阻塞清单（需人工向周立功索取）：**
1. zlgcan.dll / zlgcan.h / .lib / kerneldlls / 官方 C++/Python Demo（P0-01②）
2. MVP 参考设备型号与可借测样机（P0-01③）
3. 逐型号设备能力表（P0-01④）
4. ZXDoc DataTransmit.py、Python 脚本说明、第三方 API 完整签名（P0-05）
5. LGPLv3 / SDK 再分发书面许可（P0-06）
6. 可公开演示的 DBC/ODX/历史日志样本（P0-01⑦）

**本地已有但未消费的材料：** ZXDoc_um.pdf（即 PRD 引用的 [S1] 来源，可作为文档依据复核）、ZXDoc x64 安装包（可支撑 ZXDoc API 材料的本地探索，但正式 API 签名仍需 P0-05 确认）。

## 5. 建议实施顺序

1. ✅ Prompt 00（本轮）→ 等待人工检查。
2. 网络恢复后补推 2 个本地提交。
3. 并行发起 P0-01/P0-05/P0-06（厂商沟通线）。
4. 批准 ADR-0001 → ARCH-01 + PLAT-01/02/03。
5. CORE-01 + MOCK-01 + HOST-01。
6. P2 离线闭环（DATA-01 → AI-01 → VALID-01/COMP-01 → EXEC-01a/JUDGE-01 → UI-01/02 → REPORT-01）。
7. P0 实机条件就绪后再启动 P3；此前 SDK-01 只允许 Fake Host 骨架。

## 6. 边界核对

- 未编写业务代码；未删除/覆盖/重构既有文件（本轮仅替换本报告与追踪表、ADR 草案共 3 份盘点文档）。
- 未提交厂商二进制（PDF/zip 保持未跟踪+忽略状态）、未提交任何真实 API Key。
- 未启动下一任务。
