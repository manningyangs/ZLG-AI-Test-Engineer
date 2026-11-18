# ZLG AI Test Engineer

**周立功汽车总线智能测试工程平台 · AI Automotive Bus Test Engineering Assistant**

[English](#english) | [中文](#中文)

---

<a name="english"></a>

## English

> **Disclaimer**: This is an independent product proposal and open-source engineering project. It is **not** an official ZLG (周立功) product, and does not imply any endorsement, authorization, or partnership with ZLG. The name "ZLG AI Test Engineer" is a working name; official use of ZLG trademarks requires prior authorization.

**ZLG AI Test Engineer** is a Windows desktop AI test engineering assistant for automotive bus networks (CAN / CAN FD first), built around the ZLGCAN device SDK and the ZXDoc software ecosystem. It closes the loop of *test requirement understanding → test case design → script generation → controlled device execution → result verification → evidence report*.

### Core Design Principle

The LLM **never** freely writes and executes arbitrary code. It generates a structured **Test IR** (Intermediate Representation) that must pass rule validation, device-capability checks, and safety approval before a deterministic execution engine runs it. AI handles understanding, planning, and explanation; timing, encoding, and pass/fail judgment are done by deterministic components.

### Key Features

- 📥 **Project & Asset Management** — import DBC (and later ODX/PDX, LDF, ARXML, A2L) with SHA-256 versioning; stale caches invalidate automatically
- 🤖 **AI Test Workbench** — natural-language test intent → reviewable test cases with provenance; every generated item is marked *read-from-project / inferred / to-be-confirmed*
- 🧩 **Test IR as the Platform Hub** — protocol-, SDK-, and language-agnostic test model with JSON Schema; one IR compiles to offline simulation, ZLGCAN execution plans, or ZXDoc Python scripts
- 🔌 **Plugin Architecture** — decoupled protocol plugins, device SDK adapters, asset parsers, script backends, LLM providers, and report exporters
- 🛡️ **Safety-Gated Execution** — five risk levels (L0 listen-only → L4 destructive); default read-only, whitelisted actions, emergency stop, tamper-evident audit log
- ⚙️ **Isolated Native Host** — vendor DLLs load in a separate C/C++ process (Named Pipe RPC) so device crashes never take down the UI or AI session
- 📊 **Evidence-Complete Reports** — every assertion traces back to raw frames, timestamps, decoded values, and rule versions; missing evidence yields *Blocked*, never *Pass*

### MVP Scope (v1.0)

DBC-driven CAN/CAN FD communication tests: cycle monitoring, signal range, enum values, message timeout, controlled periodic sending — runnable fully offline via a Mock adapter, then on one confirmed ZLG CAN FD device model.

### Roadmap

| Version | Highlights |
|---|---|
| **1.0 (MVP)** | CAN/CAN FD, DBC, Test IR, offline Mock, ZLGCAN basic TX/RX, HTML/PDF report |
| **1.1** | ZXDoc third-party API adapter, ZXDoc Python script export, trace replay |
| **1.2** | UDS / DoCAN, ODX/PDX, diagnostic cases, NRC analysis |
| **2.0** | LIN/DoLIN, Automotive Ethernet/DoIP, SOME/IP, ARXML |
| **2.x** | XCP/CCP, A2L, E2E, J1939, enterprise knowledge base |
| **3.0** | Multi-vendor devices (Vector/PEAK/Kvaser/NI…), CAPL backend, CI execution |

### Documentation

- `ZLG_AI_Test_Engineer_产品需求与技术架构及项目开发计划_V1.0.docx` — PRD, architecture, and project plan
- `ZLG_AI_Test_Engineer_开发任务拆解_V1.0.md` — development task breakdown (P0–P6)
- `ZLG_AI_Test_Engineer_Trae_可执行开发任务书_V1.0.md` — executable task book for staged delivery

### Development

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\test.ps1        # run all tests
.\scripts\dev.ps1         # launch desktop app (Windows)
```

Status: P1 in progress (ARCH-01, CORE-01, PLAT-01/02/03, MOCK-01, HOST-01 done — see `docs/tasks/task_status.md`; 116 tests). No real hardware SDK integration before P0 vendor validation.

### License

AGPL-3.0-only. Vendor SDK binaries (zlgcan.dll, drivers, kerneldlls) are **not** redistributed; users install official components themselves.

---

<a name="中文"></a>

## 中文

> **声明**：本项目为独立产品方案与开源工程，**非**周立功官方产品，不代表周立功的背书、授权或合作。"ZLG AI Test Engineer"为工作名称，正式对外使用 ZLG 商标须事先取得授权。

**ZLG AI Test Engineer（周立功汽车总线智能测试工程平台）** 是一款面向汽车电子研发/测试/验证人员的 Windows 桌面端 AI 测试工程助手，首期围绕 ZLGCAN 设备 SDK 与 ZXDoc 软件生态，打通"测试需求理解 → 用例设计 → 脚本生成 → 受控执行 → 结果验证 → 报告归档"的工程闭环。

### 核心设计理念

大模型**不**自由编写并直接运行任意代码。AI 只生成经过结构化约束的 **Test IR**（测试中间表示），由规则校验器、设备能力检查与安全审批通过后，交确定性执行引擎运行。AI 负责理解、规划与解释；总线时序、编码与结果判定由确定性组件完成。

### 关键特性

- 📥 **工程与资产管理** — 导入 DBC（后续 ODX/PDX、LDF、ARXML、A2L），SHA-256 版本追踪，源文件变更自动使缓存与派生计划失效
- 🤖 **AI 测试工作台** — 自然语言测试意图 → 可审查的测试用例；每条生成内容标注"已从工程读取 / 推断建议 / 待确认"
- 🧩 **Test IR 平台中枢** — 与协议、厂商 SDK、脚本语言解耦的 JSON Schema 测试模型；同一 IR 可编译为离线模拟、ZLGCAN 执行计划或 ZXDoc Python 脚本
- 🔌 **插件化架构** — 协议插件、设备 SDK 适配器、文件解析器、脚本后端、模型供应商、报告导出六维解耦
- 🛡️ **分级安全执行** — L0 只读至 L4 破坏性五级风险管控；默认只读、动作白名单、一键急停、不可覆盖审计日志
- ⚙️ **进程隔离 Native Host** — 厂商 DLL 在独立 C/C++ 进程中加载（Named Pipe RPC），设备崩溃不拖垮 UI 与 AI 会话
- 📊 **证据完整报告** — 每个断言可定位原始帧、时间戳、解析值与规则版本；证据不足输出 Blocked 而非 Pass

### MVP 范围（v1.0）

DBC 驱动的 CAN/CAN FD 通信测试：报文周期、信号范围、枚举值、报文超时、受控周期发送；无硬件时全离线 Mock 运行，随后在确认的一款周立功 CAN FD 设备型号上完成实机闭环。

### 版本路线

| 版本 | 主要内容 |
|---|---|
| **1.0 (MVP)** | CAN/CAN FD、DBC、Test IR、离线 Mock、ZLGCAN 基础收发、HTML/PDF 报告 |
| **1.1** | ZXDoc 第三方 API 适配器、ZXDoc Python 脚本导出、记录回放 |
| **1.2** | UDS / DoCAN、ODX/PDX、诊断用例与 NRC 分析 |
| **2.0** | LIN/DoLIN、车载以太网/DoIP、SOME/IP、ARXML |
| **2.x** | XCP/CCP、A2L、E2E、J1939、企业知识库 |
| **3.0** | 多厂商设备（Vector/PEAK/Kvaser/NI…）、CAPL 后端、CI 执行 |

### 项目文档

- `ZLG_AI_Test_Engineer_产品需求与技术架构及项目开发计划_V1.0.docx` — 产品需求、总体架构与开发计划
- `ZLG_AI_Test_Engineer_开发任务拆解_V1.0.md` — P0–P6 开发任务拆解
- `ZLG_AI_Test_Engineer_Trae_可执行开发任务书_V1.0.md` — 分阶段可执行任务书

### 开发

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\test.ps1        # 运行全部测试
.\scripts\dev.ps1         # 启动桌面应用（Windows）
```

进度：P1 进行中（ARCH-01、CORE-01、PLAT-01/02/03、MOCK-01、HOST-01 已完成，见 `docs/tasks/task_status.md`，116 项测试）；P0 厂商验证完成前不接入真实硬件 SDK。

### 许可

AGPL-3.0-only。不随仓库分发厂商 SDK 二进制（zlgcan.dll、驱动、kerneldlls），用户自行安装官方组件。
