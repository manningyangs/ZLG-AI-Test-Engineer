# 变更日志

本项目遵循语义化版本；对用户可见的变更记录于此。格式参考 [Keep a Changelog]。

## [0.5.0] - 2026-11-18

P2 评测前置：AI-01 固定提示评测集（离线可跑，LLM 接入后直接复跑对比）。

### 新增

- **评测集** `examples/ai_eval/eval_set.json`（12 用例）：5 类基础意图正向
  （周期/范围/枚举/超时/发送）+ 7 负向（上下文外信号/报文、冲突阈值、MVP 预留动作、
  超范围 LIN、无来源阈值、未授权发送）；期望为生成器无关真值，含 prompt_target、
  来源抗编造、待确认阻断、发送审批等检查开关。
- **评测框架** `src/zlg_ai_test_engine/ai_eval/`：用例模型（pydantic 结构校验）、
  12 项命名检查（produced/refused/conflict_flagged/schema_valid/action_match/
  target_from_prompt/refs_in_context/no_fabricated_source/draft_unconfirmed/
  send_requires_approval/confirmed_executable…）、逐检查项明细报告；
  StubGenerator/RefusingGenerator 适配器；Generator 协议供未来 LLMProvider 复用。
- **跑批 CLI** `scripts/eval_ai.py`：表格+JSON 报告，失败 ⊆ 已知缺口返回 0，--strict 全过才算。
- **文档** `docs/ai/eval_set.md`：格式、检查字典、基线、LLM 接入方法。

### 基线与钉住

- 离线桩基线 **9/12（75%）**；3 个已知缺口 = 关键词桩不读工程上下文/不识别冲突
  （`STUB_KNOWN_GAPS` 由测试钉住，桩行为变化或 AI-01 补齐即提示）。
- 评测器方向自检：oracle 全过、拒绝型生成器正负向分区正确（15 项测试）。

## [0.4.1] - 2026-11-18

P0 文档线：P0-01 + P0-05 + P0-06（外部依赖书面化，纯文档交付）。

### 新增

- **P0-01 厂商问题清单** `docs/device/vendor_questionnaire.md`：Q1~Q10（SDK 包、样机、
  设备信息读取、逐型号能力表、UDS 支持、ZXDoc API 材料、工程上下文复用、书面分发许可、
  演示样本、试点意向），带状态/证据级/影响任务列与处理规则；**待人工发往周立功**。
- **P0-05 ZXDoc API 缺口登记** `docs/device/zxdoc_api_gap.md` + `manual_evidence_notes.md`：
  基于本地 ZXDoc 手册 V1.1（141 页，SHA-256 9a4676c9…）页级提取，40 项四态登记
  （19 已确认 / 16 待实测 / 5 未找到）；决定性发现：**第三方 API 无任何完整函数签名**、
  DataTransmit.py 手册内无本体、手册(1.2.14)与本地安装包(1.3.14)版本错位；
  仍缺材料 M1~M9 清单；页码经逐条校验未编造。
- **P0-06 分发合规** `docs/security/licensing_and_distribution.md`：LGPLv3 使用声明 p1 原文
  （D 级回填）、SDK 二进制条款 V 级待 Q8 书面确认、5 条已固化默认方案、数据/密钥政策、
  发布前审查清单；**已审计 Git 全部历史提交：零厂商二进制**。

### 工具

- 分析工具 pypdf 7.1.0 装入开发 venv（仅文档证据提取，不列入运行时依赖）。

## [0.4.0] - 2026-11-18

P2-D 批次：UI-01 + UI-02 + REPORT-01（离线闭环的界面与报告层）+ 集成门面。

### 新增

- **UI-01 AI 工作台与用例编辑器**：三态分组（已读取/推断橙标/待确认勾选），全部待确认
  确认后方可生成草案（离线桩 generation_stub，徽标注明 AI-01 未接入）；工作台→编辑器联动；
  编辑器步骤表/属性面板（ACTION_SPECS 风险等级、来源标注）、dirty 管理与
  主窗口关闭"当前页+设置页"泛化确认。
- **UI-02 运行监控与结果页**：RunController（QThread 后台 start、跨线程 state_changed 信号、
  线程安全 cancel）；审批弹窗、Failed/Clarification 原因展示、步骤树 verdict 叠加、
  Trace（evidence.frames）、四色统计；急停按钮运行中启用并接 orchestrator.cancel；
  结果页四色卡片、断言表着色、AI 解释"建议/推测，不覆盖机器判定"角标、HTML 导出
  （reporting 缺失时降级不崩）。
- **REPORT-01 证据与报告**：`build_run_result`（run/ir/plan/summary/results/evidence 全量
  JSON 化）；EvidenceStore append-only 归档（frames/signals JSONL + meta 快照 + EvidenceArtifact
  入库，重复调用只追加）；HtmlReportExporter（自包含、阈值来源/inputs_digest 复算说明、
  四色卡片）；PdfReportExporter（QTextDocument+QPdfWriter，offscreen 兜底）。文档 docs/reports.md。
- **集成门面 application/pipeline.py**：build_mock_orchestrator（Mock+VALID+COMP+EXEC 一键装配、
  离线演示资产策略并注明真实项目走 ProjectService）+ load_example_irs；RunRecord.evidence
  原始证据挂载（取消/失败保留已采集部分）；状态迁移监听钩子（UI 实时刷新，回调异常不破坏状态机）。

### 已知限制

- 工作台生成为关键词离线桩，AI-01（真实 LLM）待模型凭据接入；L1 发送需注入授权参数
  （UI 不开权限，符合安全默认）。
- 编辑器"保存"为内存快照（持久化随工程存储接入）；PDF 导出需要图形环境（HTML 永远可用）。

## [0.3.0] - 2026-11-18

P2 首批任务：DATA-01 + VALID-01 + COMP-01 + EXEC-01a + JUDGE-01（离线生成-校验-执行-判定链路打通）。

### 新增

- **DATA-01 DBC Parser**：Protocol-neutral NetworkModel（pydantic）+ DbcParser（底层 cantools 45.2，
  MIT；输出不含任何三方对象）+ NetworkQueries（按名/ID 查找、decode/encode 纯位运算）；
  覆盖 intel/motorola、signed、跨字节、Factor/Offset、值表、GenMsgCycleTime/GenSigStartValue；
  未识别/悬引用等 8 类诊断带行:列定位（含 cantools 静默忽略的 VAL_ 悬引用交叉抓取）；
  合成样例 demo_vehicle.dbc + 损坏样例；51 项测试；文档 docs/parsers/dbc.md
  （与外部基准工具对照标注为待人工执行 V 级，未虚构）。
- **VALID-01 规则校验器**：ValidationContext + 七类执行前检查
  （工程引用/协议/设备能力/参数/安全授权/资源冲突/计划完备性），
  ERROR 必含修正建议；未知能力=阻断；L4 恒阻断；降级 WARNING 不升级；32 项测试。
- **COMP-01 计划编译器**：Test IR → 确定性 Mock 执行计划（10 顶层键固定形状、assert 殿后、
  cleanup 四件套、canonical json 哈希 ir_hash/plan_hash、无 LLM 无 IO）；23 项测试。
- **JUDGE-01 确定性断言引擎**：cycle/range/enum/timeout 四类纯函数判定；
  证据不足 Blocked 绝不 Pass（含"观察窗 < 超时阈值"禁误判）；失败类别
  DUT/TestScript/AdapterDevice/Environment/Inconclusive；inputs_digest 可复算；21 项测试。
- **EXEC-01a 执行编排器 + PlanRunner**：14 态状态机（迁移白名单+追加式审计）、
  审批门（L1+ 未审批不触达设备）、跨线程取消、message_ref→CAN ID 执行侧解析（禁猜测）、
  虚拟时间证据采集、判定接线（AI 只解释不改判）、run_id 唯一不覆盖、
  清理失败单独记录；21 项测试。
- **端到端集成**（tests/integration/test_exec_pipeline.py，9 项）：样例 IR → VALID → 真编译 →
  Mock 执行 → 判定全链路；DBC 周期属性（GenMsgCycleTime）直接驱动场景与断言阈值的接线验证。

### 依赖

- 新增 cantools>=39.0（MIT）：DBC 解析底层实现，用途/许可已在 pyproject 注明。

### 已知限制

- 信号类断言（range/enum）的 decoder 注入已就绪，NetworkModel→decoder 的完整装配属于 P2-D/UI 集成。
- 与 CANdb++ 等外部基准工具的 DBC 对照待人工执行（本机无授权工具）。
- SIG_VALTYPE_/复用信号等高级 DBC 特性按警告降级，不建模。

## [0.2.0] - 2026-11-18

P1 第二批任务：CORE-01 + MOCK-01 + HOST-01。

### 新增

- **CORE-01 Test IR v1**：JSON Schema（Draft 2020-12，11 种动作、schema_version=1.0）+ Pydantic
  判别联合模型（补语义校验：min<max、payload/DLC 相等、FD 长度上限）；两级校验
  `validate_ir`（draft：未知 action/负周期/缺证据来源/DLC 非法等阻止）与 `validate_executable`
  （执行级：未确认项、预留动作 inject_fault/uds_request、断言缺 evidence_source 全部阻断）；
  动作字典 ACTION_SPECS（MVP 9 动作 + 2 预留，与 Schema 有同步测试）；版本迁移接口
  `migrate_ir`；5 个合法样例（对应 TC-02~06）+ 7 个门控/非法样例；文档 docs/test-ir/。
- **MOCK-01 Mock Adapter**：DeviceSDKAdapter 完整实现（810 行）；确定性虚拟时钟
  （advance/run，堆队列全序、seed 固定、不读系统时钟）；6 类场景配置（周期帧/定时帧/
  缺帧规则/延迟帧/错误帧事件/信号序列）；五类故障注入（离线/发送失败/接收中断/句柄占用/
  任务取消回滚）；四级证据能力视图（未知 ≠ 支持）；急停四项清理后可重开；17 项测试。
- **HOST-01 Named Pipe IPC**：4 字节大端长度前缀 + UTF-8 JSON 帧；命令白名单
  （12 命令，未知拒绝）；HostClient（request_id 匹配/超时/重复 id 防护/事件订阅/心跳/
  崩溃检测/restart 拉起子进程）；ctypes 原生 Named Pipe Fake Host（独立进程运行，
  设备状态机模拟）；PROTOCOL_VERSION_MISMATCH 明确报错；11 项集成测试
  （含 kill 子进程崩溃恢复、无孤儿进程）。文档 docs/api/ipc.md。

### 已知限制

- Mock Adapter 的 realtime 模式未实现（任务书标注可选）。
- Fake Host 单客户端串行（符合 Host 独占语义）；读路径为 PeekNamedPipe 5ms 轮询（≤5ms 检测延迟）。
- 仍无真实硬件能力（P0 Go/No-Go 前不接 DLL）。

## [0.1.0] - 2026-11-18

P1 阶段首批任务：ARCH-01 + PLAT-01/02/03（Prompt 00 后首个代码交付）。

### 新增

- **ARCH-01 插件契约**：ProtocolPlugin / DeviceSDKAdapter / AssetParser / ScriptBackend /
  ExecutionBackend / LLMProvider / ReportExporter 七类契约 + PluginManifest（JSON Schema 校验、
  api_version major 兼容检查、注册失败不阻塞启动）+ 确定性 Fake + 24 项契约测试。
- **PLAT-01 桌面壳**：PySide6 主窗口（顶栏/左侧导航+工程资源树/中央十页面/右侧上下文面板/
  底部五标签抽屉）、常驻急停（默认禁用）、浅/深双主题、系统设置页（API Key 只进 SecretStore）、
  未保存更改关闭提示 + 11 项离屏冒烟测试。
- **PLAT-02 工程存储**：SQLite（12 个核心实体）+ Alembic 迁移（含升级/回滚集成测试）、
  ProjectService（新建/打开/复制、SHA-256 导入、哈希变化 → 派生数据 Stale、文件缺失诊断）。
- **PLAT-03 配置与审计**：pydantic Settings（原子写入、不含密钥）、SecretStore
  （Windows Credential Manager/keyring，降级内存存储不落盘）、追加式审计日志（按 run_id 检索）、
  日志脱敏过滤器（sk-/Bearer/api_key 遮蔽）。
- 工程底座：pyproject.toml（src 布局、console entry `zlg-ai-test-engineer`）、
  ADR-0001（技术栈与目录结构，已批准）、docs/architecture/plugin_contracts.md。

### 已知限制

- 尚无 Test IR（CORE-01）、Mock Adapter（MOCK-01）、任何真实硬件能力（P3 前不接入 DLL）。
- UI 页面除“系统设置”外均为占位页（P2 填充）。
- 有显示器环境的真实启动仅做过 4 秒存活验证与离屏冒烟，未做人工 UI 走查。

[Keep a Changelog]: https://keepachangelog.com/zh-CN/1.1.0/
