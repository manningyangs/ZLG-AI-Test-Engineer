# ZLG AI Test Engineer
## Trae 可执行开发任务书

**版本：** V1.0  
**日期：** 2026-11-18  
**依据文件：**《ZLG_AI_Test_Engineer_产品需求与技术架构及项目开发计划_V1.0》及《ZLG_AI_Test_Engineer_开发任务拆解_V1.0》  
**产品状态：** 独立产品方案 / 立项与技术验证阶段，不代表周立功官方立项、合作或授权。

---



# 目录

1. 项目目标与产品边界  
2. 技术路线与仓库组织  
3. Trae 全局执行指令  
4. P0：技术预研与可行性验证  
5. P1：架构骨架与工程底座  
6. P2：DBC 解析与 AI 离线闭环  
7. P3：ZLGCAN 硬件执行闭环  
8. P4：ZXDoc 高层集成与 UDS  
9. P5：协议与工程格式扩展  
10. P6：产品化与企业试点  
11. 界面开发任务总规范  
12. 数据模型、日志和证据要求  
13. 安全分级与动作审批  
14. 测试策略与验收用例  
15. 阶段依赖与并行安排  
16. Trae 分阶段执行提示词  
17. 任务完成后的复盘与变更管理  
18. 首轮建议执行顺序



## 文档用途与执行规则

本文件用于直接交给 Trae，指导其在当前项目仓库内分阶段实施 ZLG AI Test Engineer。它不是让 Trae 一次性生成全部产品，而是把工程拆成可独立验收、可回滚、可持续扩展的任务。

**必须遵守：**

1. 按 P0 → P1 → P2 → P3 → P4 → P5 → P6 的阶段推进。可以并行的任务按依赖表并行；有真实 SDK 或硬件前置条件的任务不得用猜测代替验证。
2. 每次只执行本轮指定的一个任务 ID 或一个明确任务组。完成后先提交变更摘要、文件清单、测试结果、未解决问题和验收证据，等待下一项任务指令。
3. 开始修改前先阅读仓库现状、已有架构和 Git 状态。不得覆盖、删除或重写已有功能；不得擅自重构与当前任务无关的模块。
4. 每个任务开始前建立可回退基线：确认工作区干净；创建任务分支或按项目现有 Git 流程提交基线；任务完成后形成独立提交。禁止使用 `git reset --hard`、强制推送或改写共享历史。
5. 不得虚构 ZLGCAN / ZXDoc 函数签名、结构体布局、设备类型号、属性路径、枚举值或能力支持情况。必须以当前版本官方头文件、官方示例和实机验证为准；资料不足时实现接口占位/Mock，并登记阻塞项。
6. AI 只能生成受 JSON Schema 约束的 Test IR 草案。任何设备操作必须经规则校验、安全策略、执行编排器和 Adapter；禁止 AI 直接调用 DLL、任意 Shell、任意 Python、任意文件系统写操作。
7. 源工程文件、原始报文、原始日志不得被覆盖。原始证据只追加保存；派生文件必须记录来源与哈希。
8. 任务验收以可运行代码、自动化测试、运行日志和可复现步骤为准，不以“代码已写完”作为完成标准。
9. 所有新增依赖必须说明用途、许可证、版本和替代方案；优先使用成熟、可维护的开源依赖。不得擅自将厂商 DLL、驱动、头文件和示例代码打包进仓库或发布物。
10. 每次交付必须更新相关文档、配置示例、测试和变更记录；不允许只改代码不补测试。

---

# 1. 项目目标与产品边界

## 1.1 产品目标

开发一套 Windows 桌面端 AI 汽车总线测试工程助手，首期围绕周立功 ZLGCAN SDK 与 ZXDoc 软件生态，完成：

**工程文件导入 → 工程语义解析 → 自然语言测试需求 → 测试用例设计 → Test IR → 规则/安全校验 → Mock 或设备执行 → 确定性判定 → 原始证据归档 → 测试报告。**

核心价值不是“生成一段看似正确的 Python”，而是让测试任务可审查、可执行、可复现、可追溯。

## 1.2 不可改变的产品原则

- **Test IR 是核心资产。** 脚本、执行计划和报告都是从 Test IR 派生，不把任意脚本作为系统唯一事实来源。
- **协议与 SDK 解耦。** 协议插件负责协议语义、数据编码/解码和规则；设备 SDK Adapter 负责真实设备、通道、收发和设备能力。
- **协议与文件格式解耦。** CAN/CAN FD 是总线协议；UDS 是应用层诊断服务；DoCAN 是 UDS over CAN 的承载路径；DBC 是数据库描述格式；zlgcan.dll 是设备 SDK。不得把它们合并成一个插件概念。
- **设备能力显式声明。** 每种型号、固件、驱动、SDK 组合形成版本化 Capability Profile。官方声明、SDK 可见和实机验证必须区分。
- **AI 与执行进程隔离。** UI/AI 不得直接加载厂商 DLL；ZLGCAN DLL 应由独立 Native Host 进程管理。
- **执行与判定确定性。** AI 可规划、解释和提出修复建议；时间控制、帧收发、数据解析、断言和 Pass/Fail 由确定性组件承担。
- **本地优先。** 未经用户显式启用，不上传完整 DBC、ODX、原始报文、项目工程和企业规范。
- **先离线后硬件。** P1/P2 使用 Mock 完成完整软件闭环，不等待设备样机或厂商接口资料。
- **安全默认只读。** 发送需确认；故障注入默认关闭；诊断写入/标定写入需高等级授权；刷写等高风险操作不属于 MVP。

## 1.3 MVP 范围

首期范围：

- Windows 10/11 x64 桌面应用。
- 本地项目管理、文件导入、SHA-256 哈希和资产版本。
- DBC 解析：Message、Signal、CAN ID、DLC、字节序、Factor、Offset、单位、范围、值表、周期字段（存在时）。
- CAN/CAN FD 基础测试：监听、有限次数发送、周期发送（设备/Mock 支持时）、周期监控、信号范围、枚举值、报文超时。
- Test IR v1、规则校验、Mock Adapter、ZLGCAN 基础 Adapter。
- AI Provider 抽象、自然语言需求转测试用例草案、待确认项标记。
- 测试用例编辑、脚本/执行计划预览、运行监控、确定性判定、HTML/PDF 报告。
- 设备管理、能力清单、运行与审计日志。

MVP 不做：

- CANoe 替代品或完整汽车测试平台。
- ECU 刷写、Seed-Key 自动破解、生产设备操作。
- 默认自动执行故障注入、UDS 写入、标定写入。
- 完整 ODX/PDX、LDF、ARXML、A2L 解析（后续阶段）。
- 多厂商硬件的正式兼容承诺。
- 云端工程管理或多人协作平台。

---

# 2. 技术路线与仓库组织

## 2.1 推荐默认技术栈

以下是本项目的工程实施建议，不是周立功厂商规定。Trae 应先检查现有仓库；若仓库已有稳定技术栈，应优先沿用，并在架构决策记录（ADR）中说明差异，不得无理由整体换栈。

| 层 | 推荐方案 | 约束 |
|---|---|---|
| 桌面 UI | Python + PySide6 | Windows 桌面优先；UI 不直接调用 DLL；DPI 100/125/150% 可用 |
| 应用与领域逻辑 | Python 3.11+、Pydantic v2 | 类型明确、模块边界清楚；核心业务不依赖 Qt |
| Test IR | JSON + JSON Schema Draft 2020-12 | 有 schema_version；每次变更必须有兼容策略 |
| 本地数据库 | SQLite + SQLAlchemy 2.x + Alembic | 元数据、项目、用例、运行索引和审计存 SQLite |
| DBC 解析 | 优先评估 cantools | 先用样本对照；不支持字段必须显式提示 |
| Native Host | C++17 x64 | 按官方 `zlgcan.h` 和官方 Demo 编译；不手写猜测结构体 |
| UI/Host IPC | Windows Named Pipe + 长度前缀 JSON 消息 | 本机通信；定义 request_id、event_id、版本和错误码 |
| 自动化测试 | pytest；UI 测试按项目框架选择 | 单元、契约、集成、端到端分层 |
| 打包 | P0/P1 先开发运行；产品化阶段评估 PyInstaller/安装器 | 厂商 DLL/驱动默认不打包，除非取得书面许可 |
| AI Provider | OpenAI-compatible Provider 抽象 | 支持国内兼容服务/本地端点；API Key 安全保存 |

如需要变更技术栈，先提交 ADR，说明：变更原因、替代选项、风险、迁移成本和对 Native Host/插件契约的影响。未经批准，不得因个人偏好改用 Web 前后端架构或重写桌面端。

## 2.2 建议仓库目录

```text
zlg-ai-test-engineer/
├─ README.md
├─ CHANGELOG.md
├─ pyproject.toml
├─ .env.example                  # 仅变量名/示例占位，不放真实 Key
├─ docs/
│  ├─ architecture/              # 架构图、ADR、插件契约
│  ├─ api/                       # 本地 IPC、领域接口
│  ├─ test-ir/                   # IR Schema、动作字典、样例
│  ├─ device/                    # 设备能力矩阵、实机验证报告
│  ├─ security/                  # 风险等级、审批策略
│  ├─ decisions/                 # ADR
│  └─ tasks/                     # Trae 任务执行记录
├─ schemas/
│  └─ test-ir/v1/
├─ src/zlg_ai_test_engine/
│  ├─ app/                       # 应用启动、依赖注入、配置
│  ├─ ui/                        # PySide6 页面与组件
│  ├─ application/               # Use Cases / Application Services
│  ├─ domain/
│  │  ├─ project/
│  │  ├─ asset/
│  │  ├─ network/                # Protocol-neutral Network Model
│  │  ├─ requirement/
│  │  ├─ test_ir/
│  │  ├─ validation/
│  │  ├─ execution/
│  │  ├─ evidence/
│  │  └─ security/
│  ├─ plugins/
│  │  ├─ protocols/
│  │  ├─ parsers/
│  │  ├─ device_adapters/
│  │  ├─ script_backends/
│  │  ├─ llm_providers/
│  │  └─ report_exporters/
│  ├─ infrastructure/
│  │  ├─ persistence/
│  │  ├─ ipc/
│  │  ├─ logging/
│  │  ├─ secrets/
│  │  └─ file_storage/
│  └─ resources/
├─ native/
│  └─ zlgcan_host/                # C++ Native Host；SDK 获得后再接真实 DLL
├─ plugins/
│  ├─ protocol_can/
│  ├─ parser_dbc/
│  ├─ adapter_mock/
│  └─ adapter_zlgcan/
├─ examples/
│  ├─ dbc/
│  ├─ projects/
│  └─ test-ir/
├─ tests/
│  ├─ unit/
│  ├─ contract/
│  ├─ integration/
│  ├─ e2e/
│  └─ fixtures/
└─ scripts/
   ├─ dev.ps1
   ├─ test.ps1
   └─ package.ps1
```

目录可根据现有代码做小幅调整，但必须保留 domain、application、adapter/infrastructure、UI 和 native host 之间的职责隔离。

## 2.3 六个扩展维度（必须从第一版设计）

| 扩展维度 | 契约 | 新增能力时的动作 |
|---|---|---|
| 通信协议 | ProtocolPlugin | 添加协议语义、解码/编码、参数校验和测试动作；不直接加载 DLL |
| 硬件 SDK/设备 | DeviceSDKAdapter + CapabilityProfile | 添加厂商 Adapter、型号能力配置和实机验证 |
| 工程文件/记录格式 | AssetParser | 新增 DBC/ODX/LDF/ARXML/A2L/ASC/BLF/MF4 等解析器 |
| 脚本/执行后端 | TestCompiler + ScriptBackend/ExecutionBackend | 同一 IR 生成 ZLGCAN 执行计划、ZXDoc Python 或离线动作 |
| 模型/知识来源 | LLMProvider + KnowledgeConnector | 更换模型、接本地模型/企业文档，不改测试语义 |
| 报告/外部集成 | ReportExporter + ExternalConnector | 新增 HTML/PDF/Word/Excel/CI/ALM/QMS 导出或连接 |

**架构红线：** ProtocolPlugin 不允许直接调用厂商 DLL；Parser 不允许操作设备；LLMProvider 不允许执行工具动作；UI 不允许判定 ECU 结果；Native Host 不负责推理测试预期。

---

# 3. Trae 全局执行指令（每次任务开始时附带）

将以下内容作为每个 Trae 任务提示词的固定前缀：

> 你正在开发 ZLG AI Test Engineer。请先阅读仓库 README、docs/architecture、当前任务书和 Git 状态，再执行本任务。严格遵守任务边界，禁止顺手重构无关模块。先给出 5～10 行实施计划和将修改的文件清单；若发现前置资料不足，先实现 Mock/接口契约或输出阻塞项，不得猜测厂商 API。
>
> 修改前检查工作区和当前分支，建立可回退基线。按当前仓库既有风格实现。所有新增 API 都需要类型、异常处理、日志和测试。所有厂商 SDK 函数签名、结构体和常量以当前官方头文件/官方 Demo 为准；没有文件就只做抽象接口和 Fake，不得伪造真实实现。
>
> 完成后执行本任务列出的测试以及相关回归；更新 README/接口文档/CHANGELOG/任务执行记录。交付时按“实现内容、修改文件、测试命令及结果、验收证据、未完成事项、风险/阻塞、建议下一任务”格式汇报。没有实际执行的测试必须标注“未执行”，不得声称通过。

## 3.1 每个任务的统一交付格式

Trae 每次提交必须给出：

1. **任务 ID 与状态**：Not Started / In Progress / Blocked / Done。
2. **实现摘要**：实际完成的行为，不写空泛描述。
3. **修改文件清单**：新增、修改、删除分别列出；删除必须说明原因。
4. **设计决策**：新增接口/依赖/数据结构及原因；必要时附 ADR。
5. **测试证据**：执行命令、通过/失败数量、关键日志；不能只贴截图。
6. **边界核对**：明确本任务没有触碰哪些禁止范围。
7. **未解决项**：标注厂商确认/硬件/授权/算法/业务规则等阻塞。
8. **下一任务建议**：只能建议，不得自行开始下一任务。

## 3.2 Definition of Done（全任务通用完成定义）

- 代码可运行，且没有新增未处理的异常分支。
- 新增业务逻辑有单元测试；插件有契约测试；真实硬件能力有实机报告。
- 日志不输出 API Key、完整敏感工程内容或 Seed-Key 等敏感信息。
- 新动作有 IR Schema、权限级别、执行前校验和失败处理。
- 有证据的结果才可 Pass；证据缺失必须 Blocked/NotEvaluated。
- 修改过的页面具备加载、空态、成功、失败、取消等状态。
- 文档和示例与实现一致。

---

# 4. P0：技术预研与可行性验证

P0 与 P1/P2 软件开发可以并行。P0 阻塞真实硬件 P3，但不阻塞 Mock 离线开发。

## P0-01 厂商事项确认

**目标：** 把周立功 SDK、设备、ZXDoc API、授权和目标场景的外部依赖书面化。

**执行步骤：**
1. 建立 `docs/device/vendor_questionnaire.md`，列出问题、责任人、提出日期、答复、证据附件、状态和影响任务。
2. 向周立功获取当前版 `zlgcan.dll`、`zlgcan.h`、`.lib`、`kerneldlls`、完整 C++/Python Demo、版本说明和运行库要求。
3. 确认 MVP 参考设备具体型号、硬件版本、固件、驱动和可借测样机。
4. 获取设备能力表：CAN/CAN FD、硬件定时发送槽位、队列发送、延时启动、合并接收、TX Echo、时间戳、错误信息、UDS API。
5. 获取 ZXDoc `DataTransmit.py`、Python 脚本说明、第三方 API 完整签名、示例工程和 API 版本兼容范围。
6. 确认商业产品调用 ZXDoc API 的方式及 LGPLv3 影响；确认厂商 DLL、驱动、头文件、Demo 是否允许再分发。
7. 确认是否有可用于开发/演示的公开 DBC、ODX、历史日志和测试工程。
8. 形成厂商试点联系人及潜在客户场景清单。

**交付：** `vendor_questionnaire.md`、书面回复/邮件证据、资料清单、阻塞项列表。

**验收：** 每个问题具有 D（文档确认）/A（架构假设）/V（待验证）标记；未确认事项不进入“已支持”宣传。

## P0-02 SDK 开发包获取与环境验证

**依赖：** P0-01 中 SDK 包获取；目标设备和 Windows 环境。

**目标：** 验证官方 `zlgcan.dll` 能在目标 Windows x64 环境被正确加载，并明确 ABI/运行库问题。

**步骤：**
1. 建立隔离目录 `vendor_sdk_local/`（默认加入 `.gitignore`，不得提交未经授权的厂商二进制）。
2. 记录 DLL、头文件、库文件和 Demo 的 SHA-256、版本、位数、来源和授权状态。
3. 使用官方示例编译/运行，验证 x64 与 VS 运行库依赖。不要手工猜结构体对齐、调用约定或枚举常量。
4. 输出依赖检查结果，包括 Windows 版本、驱动、VC Runtime、DLL 依赖、编译器和架构。
5. 验证应用与 DLL 位数一致；如果目标包同时有 x86/x64，分别记录，不要求 MVP 同时支持。
6. 若开发包尚未获得，完成 `SDK_UNAVAILABLE` 阻塞记录，继续 Mock 开发，不创建伪 SDK。

**交付：** `docs/device/sdk_environment_report.md`、本地 SDK 校验脚本、依赖清单。

**验收：** 官方 Demo 在目标环境运行，或明确记录阻塞与所需资料；不能仅凭 PDF 文字声称 DLL 加载成功。

## P0-03 最小 C++ POC 收发闭环

**依赖：** P0-02、真实设备样机。

**目标：** 用最小 C++ 程序验证设备生命周期，不接入 AI，不开发复杂界面。

**必须按官方流程验证：** 打开设备 → 按要求初始化通道 → 启动通道 → 独立接收线程 → 发送 → 停止接收/发送 → 复位通道 → 关闭设备。

**步骤：**
1. 依据官方 `zlgcan.h` 和 Demo 建立最小 Console POC。
2. 实现打开/失败错误输出、设备信息读取、通道初始化、通道启动。
3. 启动独立接收线程，连续读取接收数据；避免在主/UI 线程轮询阻塞。
4. 在隔离总线或批准的回环环境下发送有限测试帧；发送数量、ID、数据和目标通道固定在配置中。
5. 记录 API 返回值、接收数据、时间戳、设备/SDK/驱动信息。
6. 通过 RAII/作用域清理保障失败时复位通道并关闭设备。
7. 对设备未连接、通道初始化失败、发送失败、线程退出异常进行测试。

**安全边界：** 只在无真实执行器风险的回环或实验台架进行；默认监听优先；禁止向车辆/生产 ECU 发送未知帧。

**交付：** POC 源码、编译步骤、运行日志、设备信息和问题记录。

**验收：** 至少一次完整安全收发闭环；重新运行 POC 可以再次成功打开设备，无残留句柄。

## P0-04 设备能力实测矩阵

**目标：** 建立 Capability Profile 的事实依据。

**验证项目：**
- 设备型号、序列号、硬件/固件/驱动/SDK 版本、通道数量。
- CAN 与 CAN FD 的仲裁域/数据域波特率配置。
- 硬件定时发送是否支持、槽位数量、间隔范围。
- 队列发送是否支持、间隔单位和模式切换规则。
- 延时启动是否支持。
- 合并接收是否支持、是否要求通道启动前配置。
- TX Echo、错误信息、时间戳单位和时间语义。
- UDS Request/Control 能力与传输路径。

**步骤：**
1. 先逐项对照 V1.24 手册表格和目标 SDK 头文件。
2. 将每项能力记录为 `official_declared`、`sdk_visible`、`verified_on_device` 三类证据。
3. 对可安全验证的能力执行实机测试；无法测试的标记为 Unknown，而不是 Unsupported 或 Supported。
4. 对定时发送测量精度及长期稳定性；区分设备时钟、主机单调时钟和系统墙上时间。
5. 记录设备固件、驱动和 SDK 版本，能力文件必须绑定版本范围。

**交付：** `docs/device/capability_matrix_v1.csv` 和 JSON Profile 样例。

**验收：** 每项能力有证据来源；未知能力默认禁止执行依赖它的动作。

## P0-05 ZXDoc 集成材料获取

**目标：** 为 1.1 版本的 ZXDoc 高层 Adapter 确认接口边界。

**步骤：** 获取并登记：`DataTransmit.py`、ZXDoc Python 脚本说明、第三方 API 文档和 Demo；分别确认启动/停止测量、发送、接收回调、仿真 Message 激活、DBC Signal 读写、UDS 和标定读写接口。

**交付：** `docs/device/zxdoc_api_gap.md`，每个能力记录“已确认/有示例/待实测/不支持”。

**验收：** P0 不要求 ZXDoc 实机集成成功；要求资料缺口可被清楚识别，不允许凭手册摘要实现假 API。

## P0-06 分发合规结论

**目标：** 确保独立产品开发及后续商业发布不错误分发厂商软件。

**步骤：**
1. 盘点 ZXDoc 使用声明中的 LGPLv3 要求。
2. 盘点 ZLGCAN DLL、驱动、头文件、示例、运行库的许可证/分发条款。
3. 设计默认方案：用户自行安装 ZXDoc/驱动/SDK，产品只检测并调用；除非获得明确许可才将厂商文件纳入安装包。
4. 对模型服务的数据政策、用户工程文件外发、API Key 存储进行合规检查。

**交付：** `docs/security/licensing_and_distribution.md`。

**验收：** 未获得书面授权的厂商二进制不进入源码仓库和最终安装包。

---

# 5. P1：架构骨架与工程底座

P1 的目标是“没有任何 ZLG 硬件也可以打开应用、建项目、导入文件、手工创建 Test IR、运行 Mock 并查看结果”。

## ARCH-01 插件与能力模型契约

**目标：** 为六类扩展维度建立稳定接口和版本管理。

**实施：**
1. 定义 `ProtocolPlugin`：协议 ID/版本、适用传输、帧/载荷解码、编码、动作参数 Schema、校验器、设备能力要求。
2. 定义 `DeviceSDKAdapter`：发现设备、读取设备信息、打开/关闭、通道初始化/启动/停止、收发、时间戳、设备能力、健康状态和紧急停止。
3. 定义 `AssetParser`：文件类型、解析器版本、解析入口、诊断/警告、统一模型输出。
4. 定义 `ScriptBackend` 与 `ExecutionBackend`：前者生成可审查代码/计划；后者执行 IR；不要将“导出脚本”和“运行设备”混成一个接口。
5. 定义 `LLMProvider`：模型列表/调用/结构化输出/错误分类/用量信息；密钥由 SecretStore 提供。
6. 定义 `ReportExporter`：输入标准化 RunResult/Evidence，输出 HTML/PDF 等。
7. 为每个插件定义 manifest：`id`、`version`、`api_version`、`license`、`capabilities`、`dependencies`、`entrypoint`。
8. 增加插件版本兼容检查和启动时注册日志；未知插件不得阻塞应用启动。
9. 为每个契约建立 Fake 和 contract tests。

**建议文件：** `domain/plugins/contracts.py`、`plugins/registry.py`、`plugins/manifest.schema.json`、`tests/contract/`、`docs/architecture/plugin_contracts.md`。

**验收：** Mock Protocol/Adapter/Parser/LLM/Report 插件均通过契约测试；新增插件不要求修改 Test IR 核心对象。

## CORE-01 Test IR v1 Schema

**目标：** 建立协议、SDK 和脚本目标无关的统一测试任务模型。

**对象：** TestSuite、TestCase、TestStep、Assertion、Provenance、ExecutionPolicy、ResourceRequirement、ExpectedEvidence。

**MVP 动作：**
- `listen_bus`
- `send_frame`
- `send_periodic`
- `monitor_signal`
- `wait_for_frame`
- `assert_cycle`
- `assert_range`
- `assert_enum`
- `assert_timeout`

`inject_fault`、`uds_request` 仅预留动作类型或后续扩展空间；MVP 默认禁止执行，不得因为枚举预留而开放调用。

**关键字段要求：**
- `schema_version`、`suite_id`、`case_id`、`step_id`、版本号。
- `project_id`、`asset_refs`、数据库 SHA-256、协议及通道引用。
- 动作类型、参数、超时、持续时间、最大次数、停止条件。
- 预期结果、Assertion、单位、容差、来源。
- 风险等级 L0-L4、审批要求、执行模式、目标 Adapter/能力。
- Provenance：DBC 文件、Message/Signal 字段、需求文档页/行/单元格、人工确认、模型和规则版本。
- `unconfirmed_items`：未确认参数必须可见且执行阻断。

**步骤：**
1. 创建 JSON Schema v1；Pydantic Model 与 Schema 需保持一致。
2. 为每种 action 定义参数 Schema、最小/最大值、必填字段和安全等级。
3. 定义兼容规则：新增可选字段兼容；删除/改义字段需要 schema major version。
4. 给出至少 5 个示例 IR 和非法 IR 样例。
5. 实现 IR 序列化、反序列化、校验和版本迁移接口。

**验收：** 有效样例通过；缺少来源、未确认关键阈值、未知 action、非法 DLC/payload、负周期等非法样例被阻止。

## PLAT-01 桌面应用壳与导航

**目标：** 构建可启动的 Windows 桌面外壳，不要求一期所有页面完成业务。

**全局布局：**
- 顶栏：当前项目、设备/Adapter、SDK 版本、在线/离线、安全模式。
- 左侧：项目资源树/一级导航。
- 中央：主要工作区。
- 右侧：工程上下文、属性、来源与约束。
- 底部抽屉：任务队列、日志、Trace、问题、安全审批。
- 常驻紧急停止控件；在无执行任务时显示禁用态。

**一级导航：** 工作台、工程管理、AI 测试设计、测试管理、脚本中心、执行中心、结果中心、设备与适配器、知识与模板、系统设置。

**实施：** 主窗口、Dock 面板布局、页面路由、空态/错误态组件、浅色/深色主题、DPI 测试、布局保存。

**验收：** 应用启动后十个导航可访问；空数据不崩溃；DPI 100/125/150% 无关键控件裁切；页面切换不丢失未保存状态（有提示）。

## PLAT-02 项目服务与本地存储

**目标：** 管理项目、资产和解析派生数据。

**实体：** Project、Asset、ParserRun、NetworkModel、Requirement、TestSuite、TestCase、TestStep、DeviceSession、ExecutionRun、EvidenceArtifact、ModelInvocation。

**实施：**
1. 创建 SQLite 数据库和迁移机制。
2. 新建/打开/复制/关闭项目。
3. 导入文件，记录相对路径、类型、大小、SHA-256、时间和来源。
4. 源文件只读；导入后不自动修改文件。
5. 文件哈希变化后标记解析结果、AI 上下文快照和测试计划为 Stale。
6. 支持工程资源树与资产清单。
7. 文件缺失、路径移动、文件重复、权限不足时有明确提示。

**验收：** 新建工程、导入两个 DBC 文件、关闭再打开后元数据和哈希一致；文件变化会使关联派生数据失效；不丢失原文件。

## PLAT-03 配置中心与审计日志

**目标：** 安全管理模型配置、路径、运行模式和审计记录。

**实施：**
- API Key 使用 Windows Credential Manager 或受支持安全存储；项目文件、日志、报告不得包含密钥。
- 配置项包括 Provider、模型、API Base URL、超时、并发、默认离线模式、默认安全级别、项目路径、证据目录。
- 审计事件包括模型生成、IR 修改、用例审批、设备连接、运行、取消、急停、导出。
- 审计日志采用追加记录，不提供 UI 修改历史记录功能。

**验收：** 密钥不落入明文配置文件；审计记录可以按 run_id 检索；日志脱敏测试通过。

## MOCK-01 Mock/Offline Adapter v1

**目标：** 在没有厂商 SDK/设备时提供确定性执行后端。

**功能：**
- 模拟设备发现、通道配置、打开/关闭。
- 模拟发送和接收帧。
- 可配置时间推进（实时/虚拟时间）。
- 可配置周期报文、缺帧、延迟、错误帧、信号变化。
- 注入设备离线、接收失败、发送失败、任务取消等故障。
- 实现 DeviceSDKAdapter 契约；不依赖 ZLG DLL。

**验收：** 同一个 IR 多次运行结果确定；可稳定复现 Pass/Fail/Blocked；取消和故障后 Adapter 状态恢复。

## HOST-01 Native Host 进程框架

**目标：** 建立 Python/UI 与 C++ Native Host 的隔离通信骨架；真实 SDK 集成放在 P0 完成后。

**实施：**
1. 使用 Windows Named Pipe；消息采用长度前缀 JSON 或明确的 message framing，不依赖脆弱的换行截断。
2. 消息包括 `protocol_version`、`request_id`、`command`、`payload`；事件包括 `event_id`、`event_type`、`payload`。
3. 定义命令：ping、get_host_info、list_devices、open_device、close_device、configure_channel、start_channel、stop_channel、send_frame、subscribe_rx、unsubscribe_rx、emergency_stop。
4. 先实现 Fake Native Host，可响应模拟设备，不加载 DLL。
5. 加入心跳、请求超时、重复 request_id 防护、Host 崩溃检测、重启和清理。
6. 对 IPC 消息做 schema 验证；拒绝未知命令和任意路径/函数调用。

**验收：** UI/Python 进程可与 Fake Host 通信；Host 崩溃时 UI 不崩溃；消息协议版本不兼容时给出明确错误。

---

# 6. P2：DBC 解析与 AI 离线闭环

## DATA-01 DBC Parser

**目标：** 将 DBC 输出为统一 NetworkModel，作为 AI 和测试设计可信的工程上下文。

**解析字段：**
- Message：名称、CAN ID、扩展帧标志、DLC、发送节点、周期属性（存在时）。
- Signal：名称、起始位、长度、Byte Order、Signed、Factor、Offset、Min/Max、单位、Receiver、值表。
- 文件元信息：SHA-256、Parser 版本、编码、解析时间、警告。

**步骤：**
1. 先评估 `cantools` 对目标 DBC 样本覆盖情况；不满足时封装补充解析器，不要把外部库对象直接暴露到领域层。
2. 定义 Protocol-neutral NetworkModel。
3. 处理同名 Message/Signal、重复 ID、无效 DLC、超范围信号、非法值表、注释和未知属性。
4. 未识别字段生成 ParserWarning；不允许静默丢弃后仍标记完整解析。
5. 提供查询接口：按 Message 名称/ID 查找、按 Signal 查找、列出节点。
6. 对编码/解码进行 Golden Tests，覆盖 Intel/Motorola、Signed/Unsigned、跨字节、Factor/Offset 和边界值。
7. 选取授权样本，与基准工具逐项对照。

**验收：** 解析字段对照报告；无静默漏项；同样本解析结果确定；损坏 DBC 有定位清楚的错误和警告。

## AI-01 需求 → Test IR 生成

**目标：** 将用户自然语言转换为有来源、有待确认项的 Test IR 草案。

**生成流程：**
1. 识别测试意图：周期、信号范围、枚举、报文超时、有限次数发送。
2. 从工程 NetworkModel 检索 Message/Signal，不允许凭名称猜测不存在的字段。
3. 识别参数来源：DBC 读取值、用户明确输入、企业规范检索、模型建议。
4. 将 Test IR 草案输出为符合 Schema 的 JSON。
5. 输出 `read_from_asset`、`inferred_suggestion`、`requires_confirmation` 状态；不确定项进入待确认列表。
6. 不完整需求应主动提出最少且明确的补充问题；未确认关键阈值时阻断执行。
7. Provider 适配 OpenAI-compatible 接口和国内/本地端点；模型超时、限额、格式错误和鉴权错误可恢复。

**固定评测提示集至少覆盖：**
- 检查指定报文周期。
- 检查信号上下限。
- 检查枚举值合法性。
- 检查报文超时。
- 发送指定报文 N 次（只有用户提供帧数据/引用和授权时）。

**验收：** 结构化输出通过 Schema；不存在的 Signal 被拒绝；没有来源的容差不被模型编造；错误模型输出可自动重试有限次数或提示修正。

## AI-02 RAG 与上下文管理

**目标：** 让模型只使用当前项目、当前版本且被允许使用的知识。

**知识源：** 厂商手册、SDK 示例、项目测试规范、历史脚本、错误案例、已验证测试用例。

**每份知识必须带：** 来源、版本、适用协议/设备型号/SDK 版本、导入日期、文件哈希、许可/保密标记。

**步骤：**
1. 为当前工程建立上下文快照，记录文件 ID、哈希、解析器版本、目标设备能力。
2. 本地优先建立检索索引；不得默认上传完整工程。
3. 生成时记录实际用到的检索片段和来源。
4. 资产哈希改变或知识版本不匹配时，将相关上下文标为过期。
5. 机密/不可上传知识在云模型请求中自动过滤；只有用户显式许可才可外发。

**验收：** 每条知识可追溯来源；过期/不适用内容不会静默参与生成；请求日志不包含完整机密工程。

## VALID-01 规则校验器

**目标：** 执行前集中检查工程、协议、设备、参数、安全和计划完备性。

**至少检查七类：**
1. 工程引用：Asset、Message、Signal、ECU、DID 存在，哈希版本匹配。
2. 协议能力：所需 ProtocolPlugin 已注册，传输路径完整。
3. 设备能力：Adapter/型号能力支持所需 CAN/CAN FD、定时槽位、队列、时间戳等。
4. 参数合法性：波特率、帧格式、DLC、payload 长度、周期、延迟、超时等范围合法。
5. 安全授权：动作风险级别符合当前用户审批和项目环境。
6. 资源冲突：设备/通道未占用；定时发送和队列发送互斥规则处理完毕。
7. 计划完备性：停止条件、最大持续时间、最大帧数、错误阈值、清理动作齐备。

**验收：** 不存在的 Signal、超出 DLC 的 payload、超能力的 timer slots、未确认阈值、无审批的 L1 操作全部在执行前阻断；错误信息说明修正办法。

## COMP-01 IR 编译器（Mock 后端）

**目标：** 将有效 Test IR 转换成 Mock 执行计划。

**步骤：**
1. 为每种 MVP action 编写编译规则和参数映射。
2. 编译结果包含命令序列、资源需求、时间约束、停止条件、证据订阅和清理动作。
3. 编译不得调用 LLM；同一 IR 与同一版本配置必须生成稳定计划。
4. 保存 compiler_version、IR hash 和计划 hash。

**验收：** 同一 IR 多次编译计划一致；非法 IR 不可编译；生成计划能被 Mock Adapter 执行。

## EXEC-01a 执行编排器（Mock 版）

**目标：** 实现完整执行状态机。

**状态建议：** Draft → ClarificationRequired → Validated → AwaitingApproval → Queued → PreparingDevice → Running → CollectingEvidence → DeterministicEvaluation → AIExplanation → Completed；异常状态 Failed、Cancelled、Blocked。

**步骤：**
1. 所有执行使用唯一 `run_id`，每步使用 `step_id`。
2. 支持启动、查询状态、取消、超时和结果获取。
3. 取消时停止新动作并调用 Adapter 清理。
4. 所有转换写审计日志。
5. 不允许 AI 直接改变运行状态或将失败改写为通过。
6. 重复运行同一用例生成不同 run_id，保留原运行数据。

**验收：** 各状态转换有单测；超时/取消可恢复；清理失败单独记录；运行任务不可被覆盖。

## JUDGE-01 确定性断言引擎

**目标：** 对实际数据执行可复算的 Pass/Fail 判定。

**支持：** `assert_cycle`、`assert_range`、`assert_enum`、`assert_timeout`。

**原则：**
- 断言需定义输入证据、阈值来源、时间语义、容差和计算方式。
- 没有足够数据/阈值/原始帧时输出 Blocked 或 NotEvaluated，不得 Pass。
- 结果类型区分 DUT Failure、Test Script Failure、Adapter/Device Failure、Environment Failure、Inconclusive。
- AI 只能对结果提供解释，不能覆盖机器判定。

**验收：** 边界值单测；无证据断言不会 Pass；原始输入可重新计算相同结果。

## UI-01 AI 工作台与用例编辑器

**AI 工作台布局：** 左侧工程上下文树；中央自然语言对话；右侧约束面板；底部解析/检索/生成/校验进度。

**关键交互：**
1. 用户选择 DBC、Message/Signal 或需求文档作为上下文。
2. 用户描述测试目标。
3. AI 按“已读取/推断建议/待确认”分组展示内容。
4. 未确认项可逐项确认或编辑；用户确认前不能进入 Validated。
5. 生成 Test IR 后可切换为步骤编辑器。

**用例编辑器：** 左侧用例列表；中间步骤表；右侧显示参数来源、单位、边界、原始字段；支持调整步骤顺序、复制、分组和版本差异。

**验收：** 从选择 DBC 到生成可审查 IR 的全流程可用；刷新/切换项目不会意外丢失未保存草稿。

## UI-02 运行监控（Mock）与结果页

**运行监控：** 顶部 run 状态、步骤树、Trace、曲线/统计、底部日志。

**结果页：** Pass/Fail/Blocked/NotEvaluated 汇总；用例版本；运行时长；证据清单；原始帧定位；需求追溯；AI 解释必须标注“建议/推测”。

**验收：** Mock 测试可实时显示状态；点击失败断言可定位相关帧/时间戳；证据不足显示 Blocked。

## REPORT-01 结果与证据归档

**目标：** 形成可追溯的测试证据包。

**至少保存：** 原始帧（append-only）、设备/通道信息、三个时间基准、DBC 解码值、断言输入、判定结果、IR/Parser/Compiler/Adapter 版本、AI 解释、日志、报告。

**报告：** 首期 HTML/PDF；每个 Assertion 链接到帧/时间戳/规则来源。

**验收：** 删除派生报告不影响原始证据；同一证据可重新计算；报告含项目、用例、设备/Mock、版本和结果。

---

# 7. P3：ZLGCAN 硬件执行闭环

本阶段只有在 P0-02/P0-03/P0-04 已完成，官方开发包、目标样机和函数签名可用时才启动真实 SDK 实现。

## SDK-01 ZLG Native Host 设备管理

**目标：** 由 C++ Native Host 独占加载 zlgcan.dll 并管理设备生命周期。

**步骤：**
1. 使用官方头文件和 Demo；统一封装所有厂商函数名、结构体、常量和属性路径。
2. 启动时报告 Host 架构、SDK 版本和 DLL 加载状态。
3. 实现设备发现和设备信息读取：型号、序列号、硬件/固件/驱动、通道数（实际可读字段按 SDK 提供情况）。
4. 实现单实例设备锁；设备被 ZXDoc/其他软件占用时给出明确提示。
5. 通道初始化参数在调用 SDK 前校验：CAN/CAN FD 类型、仲裁/数据波特率、模式、过滤器、只听模式等。
6. 多通道按官方开发流程线性初始化；部分失败时逆序清理。
7. 所有设备操作具备超时、取消和错误码转换。
8. 正常关闭与异常退出都释放通道、线程、设备句柄。

**禁止：** 在 Python 主进程通过 ctypes 直接加载真实 DLL；在业务代码中散落厂商函数名和魔法数字。

**验收：** 实机发现/打开/配置/关闭；设备被占用时提示；初始化中途失败后可重新连接。

## SDK-02 RX Pipeline

**目标：** 稳定接收并保留原始总线数据。

**实施：**
- 独立接收线程；按官方要求持续接收。
- 支持每通道独立接收；合并接收仅在型号/SDK/启动时配置确认后启用。
- Native Host 事件队列有界；背压策略明确；统计丢弃/积压数量。
- 时间戳保留原始值和单位；统一转换时保留 `device_timestamp`、`host_monotonic`、`wall_clock` 三字段，不混用。
- 原始帧追加落盘；协议解码在独立消费者执行，不阻塞 SDK RX 线程。
- 线程停止有超时和 join；退出后不留下后台线程。

**验收：** 持续接收、高负载、关闭、异常拔设备测试通过；丢包和队列积压可观测。

## SDK-03 Capability Profile 运行时

**目标：** 构建版本化设备能力注册与运行时兼容性检查。

**字段至少包含：** 型号、device_type、SDK 最低/最高版本、架构、总线类型、定时发送槽位/间隔、队列发送模式/单位、合并接收、时间戳、TX Echo、错误信息、UDS 传输能力、已知限制。

**合并原则：** 官方声明 + SDK 能力可见 + 实机验证；最终有效能力不能超过证据支持范围。未知值保留 Unknown。

**验收：** Mock 与实机 Profile 可加载；不支持的定时/队列/UDS 场景在编译或执行前阻断；设备能力页能展示证据等级。

## SDK-04 TX 调度

**目标：** 提供普通发送、硬件定时发送和队列发送，并处理模式互斥。

**实施：**
1. 普通发送记录 API 返回码、请求数量和可用时的 TX Echo。
2. 周期发送优先使用设备硬件定时；能力不足则阻断或降级为主机调度，并在报告注明精度等级和不保证实时性。
3. 队列发送按目标设备模式、间隔单位和容量限制编译；队列模式与定时发送冲突时由校验器拒绝或先执行明确的清理/模式切换。
4. 取消时尽可能清除设备侧定时列表和队列（按型号能力）；每种清理结果单独记录。
5. 对单帧/多帧发送设置最大次数、最大持续时间和发送白名单。

**验收：** 目标设备定时发送/队列发送实测报告；不支持能力场景被阻断；取消后无残留发送。

## COMP-02 IR → ZLGCAN 执行计划编译

**目标：** 把同一 Test IR 编译成 Native Host 结构化命令，不生成任意 C++ 代码。

**步骤：** 根据 action 类型、设备 Profile 和参数 Schema 生成命令序列；添加资源锁、时间策略、证据订阅和清理命令；计划文件包含 IR hash、compiler_version、adapter_version 和能力快照。

**验收：** 同一 IR 可分别编译 Mock 与 ZLGCAN；结果语义一致；ZLGCAN 计划只包含白名单命令。

## EXEC-01b 硬件执行编排接入

**目标：** 将真实设备纳入统一执行状态机。

**实施：**
- `PreparingDevice` 阶段检查设备/通道/能力/占用。
- 设备/通道资源锁；同一通道不允许冲突任务并发。
- 急停链路：阻止新命令、停止发送、清空定时/队列（支持时）、停止诊断、停止接收、复位通道、关闭设备。
- 热拔插/Native Host 崩溃后 Run 标记 Failed，记录资源清理状态；允许用户手动恢复。

**验收：** TC-07 运行中拔设备/模拟 Host 崩溃，Run 可定位失败原因；设备释放后可重新连接。

## UI-03 设备与适配器管理页

显示设备型号、序列号（必要时脱敏）、SDK/驱动/固件版本、通道、在线状态、占用者和能力清单。每个能力标记“官方声明/SDK 可见/实机验证/未知”。

**验收：** 页面与 Capability Profile 一致；不得把未知能力显示为支持。

## P3 实机验收

在确定型号上执行 TC-02、TC-03、TC-05、TC-06、TC-07；报告包含设备、SDK、驱动、固件、Windows 版本、通道配置和实测证据。P3 不以 Mock 通过替代实机验收。

---

# 8. P4：ZXDoc 高层集成与 UDS

本阶段为 MVP 后的 1.1/1.2；ZXDoc API 文档/示例未获取时，只完成接口设计和 Mock，不得猜测函数签名。

## ZXDOC-01 ZXDoc API Adapter

**目标：** 通过 ZXDoc 正式第三方 API 调用其高层功能。

**能力候选：** 测量启动/停止、普通发送、接收回调、仿真 Message 激活、DBC Signal 读写、UDS、标定信号读写。具体每项以实际 API Demo 验证为准。

**要求：** 独立 Adapter；运行时检查 ZXDoc 版本和接口能力；设备被其他进程占用时清晰提示；不能同时由 ZLGCAN Adapter 和 ZXDoc Adapter 打开同一设备。

## ZXDOC-02 ZXDoc Python Script Backend

**目标：** 将同一 Test IR 导出为 ZXDoc 内部 Python 扩展脚本。

**要求：** 模板必须基于周立功提供的脚本说明和已验证 Demo；显式声明依赖模块、ZXDoc 版本、目标工程和参数；生成代码可预览、下载，不自动在未经批准的工程运行。

## ZXDOC-03 ZCP/ZCPC 工程导入

**目标：** 导入 ZXDoc 项目配置与项目容器。

**要求：** ZCP 数据库文件依赖原路径，需做路径检查；ZCPC 包含工程关联数据库，需检查解包路径和文件完整性；不得修改原项目文件。

## UDS-01 ODX/PDX Parser

**目标：** 输出诊断 ECU、服务、子服务、DID、参数、请求/响应结构和 DTC 等统一诊断模型。先支持明确目标用例所需字段，不承诺完整 ODX 规范覆盖。

## UDS-02 DoCAN 诊断执行

**目标：** 通过经验证的 ZLG UDS API 封装 DoCAN 请求、响应、NRC、超时和取消。

**要求：**
- UDS Request/Control/EX 的使用依据当前 SDK 头文件和实测。
- P2/P2* 超时受厂商文档提示约束，配置小于建议最小值时必须告警或阻断；不能将 PC 计时精度宣传为亚毫秒。
- 支持会话保持、寻址、请求/响应数据长度和错误结果记录。
- 0x27、0x2E、RoutineControl 等写入类动作按 L3 授权；MVP 不自动执行。

## UDS-03 UDS 用例库与台架验证

首批围绕 0x10、0x22、0x19 等服务建立有明确 ODX/规范来源的正响应/负响应/超时模板。目标 ECU 的预期 NRC 和会话行为不得由模型臆造。

**P4 验收：** 同一 Test IR 至少可在 Mock 与 ZLGCAN/ZXDoc 两个后端运行或导出；UDS 案例必须在安全台架实测。

---

# 9. P5：协议与工程格式扩展

每个扩展必须单独立项，不因为接口预留就默认实现。新增协议需要：插件 Manifest、Parser/Codec、动作 Schema、离线测试集、目标设备/软件能力矩阵、示例工程、兼容说明和安全等级。

| 任务 | 范围 | 重点边界 |
|---|---|---|
| PROTO-LIN | LIN/DoLIN + LDF | 主从、调度表、唤醒、响应；需要设备能力和 LDF Parser |
| PROTO-ETH | Ethernet/DoIP/SOME/IP + ARXML | 拆分网卡/网络接口、DoIP 传输、SOME/IP 服务层；不能混成单一“ETH 插件” |
| PROTO-XCP | XCP/CCP + A2L | 先测量只读，写标定需 L3 权限；内存写入需专项授权 |
| PROTO-J1939 | J1939 | 按商用车项目需求和数据库样本验证 |
| PROTO-E2E | E2E 保护算法 | CRC/Counter/Data ID 等按项目配置和算法验证 |
| REPLAY-01 | 历史记录回放 | 先选 ASC 或 MF4 一个格式完成端到端；其他格式按需求扩展 |

**扩展完成标准：** 离线回归 + 设备/软件实测（如适用）+ Capability Profile + 契约测试 + 示例工程 + 文档。

---

# 10. P6：产品化与企业试点

## PROD-01 安装与运维

安装包、卸载、配置备份/恢复、运行库和驱动检测、日志打包、设备诊断工具。未获得许可不得打包厂商 DLL/驱动。

## PROD-02 企业权限与脱敏

角色权限、用户审批、项目数据脱敏、批量执行限制、危险动作二次确认和报告模板权限。

## PROD-03 知识库与模板

厂商文档、企业规范、已验证用例和脚本知识库；支持版本、设备型号、协议、许可和保密标记；模板发布前经过专家确认。

## PROD-04 许可合规审查

ZXDoc/LGPLv3、ZLGCAN DLL/驱动/头文件/示例的再分发权限、第三方依赖许可证、模型 API 数据政策和客户工程数据处理政策。

## PROD-05 多厂商设备适配预留

仅验证 Adapter 契约和抽象可扩展性；Vector/PEAK/Kvaser/NI 等不属于 MVP 兼容承诺。具体 SDK、许可证、硬件和客户需求齐备后另立任务。

## PROD-06 企业试点

至少一个真实企业场景试点，明确客户授权、设备、ECU 台架、验收用例、数据处理约定和试点结论。

---

# 11. 界面开发任务总规范

## 11.1 全局布局

- 顶栏：项目、Adapter/SDK、设备、通道、在线/离线、安全模式。
- 左侧：项目资源树和一级导航。
- 中央：项目工作区、AI 对话、用例编辑、脚本或运行内容。
- 右侧：当前对象详情、参数、单位、来源和约束。
- 底部抽屉：日志、Trace、任务队列、问题、安全审批。

## 11.2 页面建设顺序

| 页面 | 阶段 | 必需状态 |
|---|---|---|
| 项目首页 | P1 | 空项目、最近项目、设备状态、导入入口 |
| 工程资源管理 | P1/P2 | 解析中、成功、警告、失败、资产过期 |
| AI 测试工作台 | P2 | 输入、生成中、待确认、失败、草稿 |
| 用例编辑器 | P2 | 未保存、已校验、待确认、版本差异 |
| 脚本中心 | P2 | 预览、静态检查、差异、导出；MVP 不默认自动执行任意代码 |
| 执行监控 | P2/P3 | Pending/Running/Pass/Fail/Blocked/Cancelled、急停、日志 |
| 结果中心 | P2 | 结果摘要、证据、追溯、报告导出 |
| 设备管理 | P3 | 发现、连接、占用、能力证据等级、断开/故障 |
| 知识与模板 | P6 | 来源/版本/许可/适用范围 |
| 设置 | P1 | 模型、密钥、文件路径、隐私、安全、日志 |

## 11.3 UI 交互硬要求

- 每个耗时操作有进度和取消。
- 每个页面有 Loading、Empty、Success、Error、PermissionDenied 状态。
- 风险操作显示设备、通道、帧 ID、数据、周期、持续时间和目标 ECU，并二次确认。
- 急停入口在运行页面始终可见；空闲时禁用并解释原因。
- AI 推断与事实采用不同视觉样式；用户可以查看来源。
- 未保存更改离开时提示保存/丢弃/取消。
- 运行时 UI 只订阅事件，不直接轮询 DLL。

---

# 12. 数据模型、日志和证据要求

## 12.1 核心实体

- Project：工程 ID、名称、根路径、创建/修改时间、Schema 版本。
- Asset：资产 ID、项目 ID、类型、路径、SHA-256、大小、解析器版本、状态。
- NetworkModel：Bus/Channel/Node/Message/Signal 等统一语义数据。
- Requirement：来源资产、页/行/单元格位置、原文、确认状态。
- TestSuite/TestCase/TestStep：测试套件、用例版本、步骤序列、动作和断言。
- DeviceSession：型号、序列号、SDK/驱动/固件、能力快照。
- ExecutionRun：run_id、用例版本、Adapter、设备会话、状态、开始/结束时间。
- EvidenceArtifact：原始帧、日志、波形、报告的路径、哈希、时间范围和元数据。
- ModelInvocation：Provider、模型、Prompt 版本、上下文引用、输出哈希、Token/费用（可选）。
- AuditEvent：用户、时间、动作、对象、审批、结果和证据引用。

## 12.2 时间字段

必须分别保存：
- `device_timestamp`：设备时间戳及原始单位。
- `host_monotonic_timestamp`：主机单调时钟，用于本机时序测量。
- `wall_clock_timestamp`：系统墙上时间，用于报告显示。

不得把这三种时间混为一个时间字段，也不得在缺少设备同步信息时声称跨设备时间绝对可比。

## 12.3 证据存储

- 原始报文和原始日志 append-only。
- 派生解析值、统计和报告记录源文件/原始数据哈希与 Parser/Rule 版本。
- 每个 Assertion 指向输入证据。
- 原始证据不因重新运行、重新解析或报告导出而覆盖。
- 证据不足时状态为 Blocked/NotEvaluated。

---

# 13. 安全分级与动作审批

| 等级 | 示例 | 默认策略 |
|---|---|---|
| L0 只读 | 设备发现、监听、读信号、文件导入、离线分析 | 允许；新设备默认只听模式 |
| L1 受控发送 | 单帧/有限次数发送、周期发送 | 明确设备/通道/帧/持续时间/最大帧数，用户确认 |
| L2 故障注入 | 报文丢失、异常 Data、周期加速/延迟 | 默认关闭；仅离线模拟或批准台架并二次确认 |
| L3 诊断写入/标定 | 写 DID、RoutineControl、Seed-Key、XCP 写 | 强授权、环境白名单、二次确认和完整审计 |
| L4 高风险 | ECU 刷写、生产设备操作、未知安全算法 | MVP 禁止；未来单独安全评审 |

**全局安全要求：**
- AI 只能调用白名单 IR action，不可输出任意 shell/Python/DLL 函数供执行。
- 新设备默认只听模式。
- 发送前展示目标总线、通道、帧、payload、数量、周期、停止条件。
- 急停后停止新发送；清理发送队列/定时表（设备支持时）；停止线程并释放资源。
- API Key 只存系统安全存储，不进入工程、脚本、日志和报告。
- 企业文件默认本地；云模型请求前展示上下文外发范围。

---

# 14. 测试策略与验收用例

## 14.1 测试层级

- Unit：IR Schema、Parser、Decoder、Assertion、状态机、规则校验。
- Contract：ProtocolPlugin、AssetParser、DeviceSDKAdapter、LLMProvider、ReportExporter。
- Integration：SQLite、IPC、Mock Adapter、Native Host Fake。
- Hardware-in-the-loop：真实 ZLG 设备、隔离总线、目标 ECU 台架。
- AI Evaluation：固定提示集、错误引用集、缺失阈值集、冲突知识集。
- Safety：未审批发送、超能力参数、取消、急停、设备拔出、Host 崩溃。

## 14.2 MVP 七个演示用例

| 用例 | 验证内容 | 期望 |
|---|---|---|
| TC-01 DBC 导入 | Message/Signal/ID/DLC 等解析 | 与基准工具逐项对照；未知字段提示 |
| TC-02 周期监控 | 指定 Message 的到达周期 | 生成周期统计、异常间隔和原始帧证据 |
| TC-03 信号范围 | 物理值范围 | 阈值有来源；越界和边界样本有明确判定 |
| TC-04 枚举值 | 值表合法性 | 识别合法枚举和未定义值 |
| TC-05 报文超时 | 监听窗口内未收到指定帧 | 记录最后一帧、超时起止和恢复情况 |
| TC-06 周期发送 | 有审批的周期发送 | 发送次数/周期/停止状态符合计划；不支持则阻断 |
| TC-07 异常退出清理 | 取消、拔设备或 Host 错误 | 任务 Failed/Cancelled；线程/句柄/发送资源释放 |

**注意：** 报文超时测试本身是监听判定；若要验证被测 ECU 对丢帧的行为，需要专门的故障注入能力和批准台架，不能默认通过取消本机发送来模拟 ECU 输入丢失。

## 14.3 发布验收门槛

- DBC 样本解析无静默漏项。
- 5 类基础测试意图可生成；所有关键阈值有来源或待确认标记。
- 不存在引用、超 DLC、超设备能力和未审批的动作必须阻止执行。
- 同一 IR 在 Mock 重复执行产生确定性判定。
- 选定 ZLG 设备完成真实发现、打开、配置、发送/接收和关闭；报告记录 SDK/驱动/固件/OS。
- 每个断言可追溯到原始帧/时间戳/规则；无证据不得 Pass。
- 取消、拔设备、初始化失败后无残留句柄和发送任务。
- 许可与分发审查完成。

---

# 15. 阶段依赖与并行安排

| 阶段 | 主任务 | 可并行项 | 阻塞条件 |
|---|---|---|---|
| P0 | P0-01～P0-06 | ARCH-01、PLAT-01/02/03 可先启动 | P3 必须等待 SDK/样机和真实接口验证 |
| P1 | ARCH-01 → CORE-01、MOCK-01、HOST-01、PLAT-* | P0 硬件线并行 | P2 依赖 ARCH/CORE/项目存储 |
| P2 | DATA-01、AI-01/02、VALID-01、COMP-01、EXEC-01a、JUDGE-01、UI-01/02、REPORT-01 | SDK 预研继续 | 不依赖真实硬件 |
| P3 | SDK-01～04、COMP-02、EXEC-01b、UI-03 | P2 后半段回归 | P0 完成 + 选定设备 |
| P4 | ZXDOC-*、UDS-* | 可先整理接口资料 | 厂商 API 完整 Demo/签名 |
| P5 | PROTO-* | 按实际客户场景选择 | 插件契约、目标设备/软件能力 |
| P6 | PROD-* | 文档、合规、安装器可并行 | MVP 验收通过 |

## 15.1 关键路径

**离线 MVP：** ARCH-01 → CORE-01 → DATA-01 → AI-01 → VALID-01 → COMP-01 → MOCK-01/EXEC-01a → JUDGE-01 → UI-01/UI-02 → REPORT-01。

**硬件支线：** P0-02 → P0-03 → P0-04 → HOST-01/SDK-01 → SDK-02/03/04 → COMP-02/EXEC-01b → P3 实机验收。

**外部集成线：** P0-05 → ZXDOC-01/02/03；UDS 线：ODX/PDX 获取与能力确认 → UDS-01/02/03。

---

# 16. Trae 分阶段执行提示词

以下提示词可直接复制给 Trae。建议一次只发一个阶段或一个任务组；完成后人工检查报告，再继续。

## Prompt 00：仓库盘点与执行准备（先执行）

```text
你将参与 ZLG AI Test Engineer 项目开发。先不要编写业务代码。

请检查当前仓库：
1. 输出目录树、当前技术栈、依赖、启动方式、测试方式和 Git 状态。
2. 查找现有功能、未提交修改、配置文件、密钥风险和厂商 SDK 文件。
3. 对照《Trae 可执行开发任务书》与《开发任务拆解 V1.0》，建立任务追踪表，标记已存在/缺失/需验证。
4. 检查项目是否违反分层约束：UI/AI 是否直接加载 DLL、协议是否和 SDK 混合、是否存在任意脚本执行。
5. 不删除、不覆盖、不重构现有代码；不提交任何厂商二进制或真实 API Key。
6. 生成 docs/tasks/repo_baseline.md、docs/tasks/task_traceability.csv 和必要的 ADR 草案。

只输出盘点结果和建议实施顺序，不要自动开始下一任务。
```

## Prompt P0：技术验证线

```text
执行 P0 技术预研。仅在获得周立功当前官方 SDK 开发包、头文件、官方 Demo 和目标设备后实施真实 SDK 代码。

按顺序处理 P0-01 至 P0-06：
- P0-01：建立厂商问题清单和证据等级；
- P0-02：验证 DLL 位数、依赖、运行库和 x64 加载；
- P0-03：按官方开发流程完成 C++ 最小设备生命周期和安全收发 POC；
- P0-04：建立设备能力实测矩阵；
- P0-05：收集 ZXDoc DataTransmit.py / Python 脚本说明 / 第三方 API Demo；
- P0-06：记录 LGPLv3、SDK DLL、驱动和示例分发授权结论。

不得猜测 SDK 函数签名、结构体、常量或设备能力。缺少官方开发包时，P0-02/P0-03 必须标记 Blocked，并输出确切缺失资料；不要用自己编写的 fake DLL 声称 POC 成功。

每个子任务单独输出证据、日志、版本和结论。P0 结束时给出是否允许启动 P3 的 Go/No-Go 结论。
```

## Prompt P1-A：插件契约与 Test IR

```text
执行 ARCH-01 和 CORE-01，不实现真实 ZLG 硬件功能。

要求：
1. 建立 ProtocolPlugin、DeviceSDKAdapter、AssetParser、ScriptBackend、ExecutionBackend、LLMProvider、ReportExporter 契约及插件 Manifest 版本机制。
2. 将协议语义与设备 SDK、文件格式、脚本后端彻底分离。
3. 建立 Test IR v1 JSON Schema / Pydantic 模型，包含 TestSuite、TestCase、TestStep、Assertion、Provenance、ExecutionPolicy、ResourceRequirement。
4. MVP 动作只实现 listen_bus、send_frame、send_periodic、monitor_signal、wait_for_frame、assert_cycle、assert_range、assert_enum、assert_timeout。
5. inject_fault 和 uds_request 只能预留，默认禁用；不允许生成可直接执行任意代码字段。
6. 为所有契约建立 Fake 和 contract tests；建立版本兼容策略与样例。

禁止直接调用厂商 DLL。完成后运行 Schema、单测和契约测试，并给出目录及接口说明。
```

## Prompt P1-B：桌面应用壳与项目存储

```text
执行 PLAT-01、PLAT-02、PLAT-03。

实现 Windows 桌面应用基本壳：顶栏、左侧导航/工程树、中央工作区、右侧上下文面板、底部日志/任务抽屉。建立十个一级导航占位页和深浅主题。

实现本地 Project/Asset 管理：SQLite、迁移、文件导入、SHA-256、路径管理、哈希变化导致派生数据 Stale。源文件绝不覆盖。

实现配置中心：模型 Provider 配置、API Key 安全存储接口、路径、并发、默认安全模式。实现追加式审计事件。

要求：页面具备 Loading/Empty/Error/Success 状态；100/125/150% DPI 可用；API Key 不落入普通配置文件、项目和日志。

不要实现 AI 业务、真实硬件、复杂报表。完成后提供启动命令、测试命令和截图/日志证据。
```

## Prompt P1-C：Mock Adapter 与 Native Host 骨架

```text
执行 MOCK-01 和 HOST-01。

Mock Adapter 必须实现 DeviceSDKAdapter 契约，可模拟设备发现、打开/关闭、通道管理、收发、虚拟时间、周期帧、缺帧、延迟、错误和异常退出。相同输入应得到确定性结果。

Native Host 先实现 Fake Host：Python 侧通过 Windows Named Pipe 与 Native Host 通信，消息使用带长度边界的 JSON 协议，包含 protocol_version/request_id/command/payload；事件包含 event_id/event_type/payload。加入超时、心跳、Host 崩溃检测和重启。

在 SDK 开发包未取得前不得加载真实 zlgcan.dll，也不得伪造官方函数定义。真实 DLL 集成留给 P0 完成后的 SDK-01。

完成后验证 UI/业务进程不因 Host 崩溃而退出；Mock Adapter 通过契约测试。
```

## Prompt P2-A：DBC Parser 与 NetworkModel

```text
执行 DATA-01。

实现 DBC Parser 插件，输出统一 NetworkModel，解析 Message/Signal/ID/DLC/周期属性（存在时）/字节序/长度/Signed/Factor/Offset/单位/Min/Max/值表/节点和注释等可支持字段。

优先评估成熟 DBC 库，但对外只输出本项目 NetworkModel，不能把第三方库对象暴露为领域模型。所有未识别字段生成警告，不得静默丢弃。

准备 Golden Tests，覆盖 Intel/Motorola、Signed/Unsigned、跨字节、Factor/Offset、枚举和边界；使用授权 DBC 样本与基准工具逐项对照。

不要实现 ODX/LDF/ARXML/A2L。完成后输出解析覆盖报告和已知限制。
```

## Prompt P2-B：AI Provider、RAG 与 Test IR 生成

```text
执行 AI-01 和 AI-02。

实现 LLMProvider 抽象，先支持 OpenAI-compatible 接口，配置密钥从 SecretStore 读取。模型输出必须经过 JSON Schema 校验，不接受任意代码输出作为执行输入。

实现自然语言需求到测试意图再到 Test IR 草案的流程。检索当前项目 DBC NetworkModel，所有 Message/Signal 引用必须存在。输出每个字段的来源状态：读取到、用户输入、推断建议、待确认。

没有需求或规范来源的周期、容差、超时和 ECU 预期行为不得由模型编造；关键项不完整时提出澄清并阻断执行。

建立固定 Prompt Evaluation，至少覆盖周期、范围、枚举、超时、有限次数发送五类需求，以及不存在的信号、冲突阈值、无预期结果等负向样本。

知识源必须记录来源、版本、适用型号/协议、许可和文件哈希；默认不上传完整 DBC/ODX/总线日志。
```

## Prompt P2-C：校验、编译、Mock 执行和判定

```text
执行 VALID-01、COMP-01、EXEC-01a、JUDGE-01。

实现执行前七类校验：工程引用、协议插件、设备能力、参数范围、安全等级、资源冲突、停止条件/计划完备性。

实现 Test IR 到 Mock Execution Plan 的确定性编译器；相同 IR 和编译器版本必须生成稳定计划。

实现执行状态机 Draft/ClarificationRequired/Validated/AwaitingApproval/Queued/PreparingDevice/Running/CollectingEvidence/DeterministicEvaluation/Completed，以及 Failed/Cancelled/Blocked。

实现 assertion：cycle/range/enum/timeout。证据不完整必须 Blocked/NotEvaluated；机器判定不能由 AI 覆盖。结果错误分类区分 DUT、Script、Adapter/Device、Environment、Inconclusive。

完成取消、超时、故障注入 Mock、重复运行和清理测试。真实硬件接口不在此任务内。
```

## Prompt P2-D：AI 工作台、编辑器、运行与报告 UI

```text
执行 UI-01、UI-02、REPORT-01。

实现 AI 工作台：左侧工程上下文树、中央对话、右侧约束/来源面板、底部解析/检索/生成/校验状态。必须区分已读取/推断建议/待确认。

实现测试用例编辑器：用例列表、步骤表、属性面板、来源和版本差异；未保存退出时提示。

实现脚本/计划预览：只读预览、静态检查、导出；不得默认执行任意 Python。

实现 Mock 运行监控：步骤树、Trace、信号图、日志、任务状态和取消；结果页展示 Pass/Fail/Blocked/NotEvaluated、原始证据、需求追溯和报告导出。

结果解释中的 AI 内容标记为“建议/推测”，确定性判定保持独立。报告首期支持 HTML/PDF。
```

## Prompt P3-A：真实 ZLG Native Host 与 SDK Adapter

```text
只有在 P0-02/P0-03/P0-04 通过并已取得官方 SDK 开发包后执行本任务。

根据当前官方 zlgcan.h、官方 Demo 和目标设备实测结果，实现 SDK-01。禁止依据 PDF 摘要手写函数签名/结构体/常量。

Native Host 负责 x64 DLL 加载、设备发现、信息读取、单实例锁、打开/关闭、通道配置、线性初始化和逆序清理。Python/UI 不直接调用 DLL。

请先实现并测试设备生命周期，不要一次性实现全部高级功能。记录 SDK、驱动、固件、OS 和目标设备版本。

若官方开发包/样机缺失，保持 Fake Host 和 Adapter 接口，不得伪造硬件通过报告。
```

## Prompt P3-B：接收、能力矩阵、发送调度和实机验收

```text
执行 SDK-02、SDK-03、SDK-04、COMP-02、EXEC-01b、UI-03。

实现独立接收线程、有界队列、背压、丢包计数、原始帧落盘与三时钟时间戳模型。合并接收仅对确认支持的设备启用。

实现版本化 Capability Profile，合并官方声明、SDK 可见和实机验证证据；Unknown 不得当作 Supported。

实现普通发送、硬件定时发送、队列发送；优先设备侧时序。定时/队列冲突、设备容量不足、型号不支持时执行前阻断。取消时停止发送并尽可能清空设备队列/定时任务。

同一 Test IR 必须分别编译为 Mock 和 ZLGCAN 计划。实现资源锁、急停、拔设备异常恢复和设备管理页。

完成 TC-02/03/05/06/07 实机测试，报告附设备/SDK/驱动/固件/OS/通道配置和原始证据。
```

## Prompt P4：ZXDoc/UDS 集成

```text
仅在取得 ZXDoc 第三方 API 完整签名、DataTransmit.py、脚本说明和许可确认后执行。

分开实施 ZXDOC-01/02/03 与 UDS-01/02/03。ZXDoc API Adapter 和 ZLGCAN Native Adapter 是两个不同后端；同一物理设备不能被两者并发占用。

ZXDoc Python Script Backend 根据官方脚本说明生成扩展脚本；不得猜测 Python 模块函数名。

ODX/PDX Parser 先覆盖目标 UDS 测试用例需要的诊断服务、DID、请求/响应参数和 DTC 字段；DoCAN UDS 执行按目标 SDK 的 Request/Control 接口和真实响应结构封装。

P2/P2* 的厂商约束要校验；写 DID、Seed-Key、RoutineControl 和标定写入按 L3 安全审批，MVP 不自动执行。
```

## Prompt P5：协议扩展

```text
根据产品路线和真实客户需求，逐个执行 PROTO-LIN、PROTO-ETH、PROTO-XCP、PROTO-J1939、PROTO-E2E、REPLAY-01。一次只扩展一个协议/格式组。

新增协议必须包括：插件 manifest、协议语义模型、Parser/Codec、动作参数 Schema、设备/传输能力要求、离线回归、目标设备或软件实测、示例工程、兼容说明和安全等级。

严格区分物理/链路总线、传输层、应用/诊断层、描述文件和记录格式。例如 DoIP 不能等同于 Ethernet，DBC 不是设备 SDK，UDS 不是传输协议。

没有设备/规范/授权/测试样本时标为 Blocked/Deferred，不得将空壳插件宣传为已支持。
```

## Prompt P6：产品化与试点

```text
执行 PROD-01 至 PROD-06。

重点完成安装/卸载、驱动和运行库检测、配置备份恢复、日志诊断包、用户权限、脱敏、知识库版本管理、报告模板、许可审查和真实企业试点。

不得打包未经授权的周立功 DLL、驱动、头文件或示例；默认检测用户已安装的官方组件。发布前审查 LGPLv3、第三方依赖、模型 API 数据政策和客户工程文件外发。

至少一个客户/实验室场景试点通过真实验收，整理已知限制、兼容矩阵、运维文档和下一版本路线。
```

---

# 17. 任务完成后的复盘与变更管理

每个阶段完成后，Trae 必须更新：

- `docs/tasks/task_status.md`：任务 ID、状态、阻塞、提交号。
- `docs/architecture/`：架构/契约/ADR 更新。
- `docs/device/`：设备、SDK、驱动和能力验证证据。
- `docs/test-ir/`：Schema、动作字典和示例。
- `CHANGELOG.md`：对用户可见的变更。
- 回归测试清单和未解决风险。

**变更审批规则：**
- 新增协议、SDK 或 Parser：必须扩展插件契约和能力矩阵，不允许在核心流程写设备型号分支。
- 修改 Test IR：必须更新 Schema 版本、迁移逻辑、示例和兼容测试。
- 修改安全等级：必须有风险评估和专门测试，不能降低默认审批等级。
- 修改数据库：必须提供 Alembic migration 和升级/回滚测试。
- 修改 Native Host IPC：必须提升协议版本或提供向后兼容策略。
- 增加厂商二进制：必须有来源、哈希、许可和分发审查记录。

---

# 18. 首轮建议执行顺序

1. **先运行 Prompt 00**：让 Trae 盘点现有仓库，不写业务代码。
2. **并行启动 P0-01/P0-05/P0-06**：尽早向周立功索取 SDK、ZXDoc API 和许可答复。
3. **先做 ARCH-01、PLAT-01/02/03**：确认目录与技术栈后，搭建工程基础。
4. **完成 CORE-01、MOCK-01、HOST-01**：建立 IR 和 Mock/Host 边界。
5. **执行 DATA-01、AI-01、VALID-01、COMP-01**：打通离线生成链路。
6. **完成 EXEC-01a、JUDGE-01、UI-01/02、REPORT-01**：形成可演示离线闭环。
7. **P0 实机条件就绪后再做 P3**：不要提前写真实 SDK 绑定代码。

## 首轮结束标准

在交给 Trae 继续开发前，至少应取得以下输出：

- 仓库盘点报告和任务追踪表。
- 技术栈/目录结构 ADR。
- P0 外部依赖与阻塞清单。
- ARCH-01 契约草案。
- CORE-01 Test IR v1 Schema 草案。
- 第一版 Mock 执行计划。

**项目总原则：先让同一份 Test IR 在 Mock 上完成“生成—校验—执行—判定—报告”，再接真实硬件；真实硬件通过 ZLGCAN Adapter 接入；ZXDoc 作为独立高层 Adapter 后续接入。协议、SDK、Parser、脚本后端和模型服务都必须保持可替换。**
