# P2 验收对照报告（acceptance_report）

**依据：** 任务书 §14.2 演示用例 TC-01~07、§14.3 发布验收门槛、§15.1 MVP 验收指标、§16.2 P2 退出条件
**日期：** 2026-11-18｜**测试基线：** `pytest tests` = 389 passed（v0.6.0）
**结论：P2 离线闭环验收通过（实现与自动化证据）**；两条待办（真实模型跑批、人工 UI 走查）不阻塞离线闭环判定。

## 1. 七个演示用例对照（TC-01~07）

| 用例 | 判定 | 证据（测试/产物） |
|---|---|---|
| TC-01 DBC 导入 | **✅ 通过** | `tests/unit/test_dbc_parser.py`（51）：字段覆盖、损坏文件定位、两次解析确定、sha256 稳定；样例 `examples/dbc/demo_vehicle.dbc`；覆盖报告 [docs/parsers/dbc.md](../parsers/dbc.md)。**限制**：与 CANdb++ 等外部基准工具的逐项对照待人工（V 级，已如实标注） |
| TC-02 周期监控 | **✅ 通过（Mock）** | `test_exec_pipeline::test_example_ir_passes_validation_and_executes`：10s@20ms ≈500 帧 → assert_cycle Pass；DBC 接线用例证明 GenMsgCycleTime 驱动阈值；周期偏差 → Fail(DUT) 用例 |
| TC-03 信号范围 | **✅ 通过（判定层）** | `test_judge::TestJudgeRange`（边界含 0/250、越界 Fail+offender 证据、空集 Blocked、倒置阈值 Blocked）；信号解码接线 `test_dbc_decode_available_for_signal_wiring`（motorola 100.00 km/h）。**限制**：run 级 range 全链路需 decoder 注入装配（UI 演示时接入） |
| TC-04 枚举值 | **✅ 通过（判定层）** | `test_judge::TestJudgeEnum`（全合法 Pass、未知值 Fail 列举、空集 Blocked）；DBC 值表解析（51 项 Golden 含 3 组 value table）。**限制**：同上，run 级需 decoder 装配 |
| TC-05 报文超时 | **✅ 通过** | `test_judge::TestJudgeTimeout`（空窗 Fail+起止时刻、窗口<阈值 Blocked 禁误判、全窗无帧 Fail、缺窗口 Blocked）+ 集成样例 `valid_timeout_watch.json` |
| TC-06 周期发送 | **✅ 通过（Mock）** | `test_exec_pipeline::test_message_ref_resolved_and_approved_send_executes`：审批前零帧 → 批准后 5 帧、周期精确 20ms；`unknown 能力阻断`用例（Mock hardware_periodic_tx=unknown → 执行前 Failed）；`test_send_without_approval_never_reaches_device` |
| TC-07 异常退出清理 | **✅ 通过（Mock）** | 跨线程取消→Cancelled+资源释放（orchestrator）；Mock 故障注入五类+急停后可重开（17 项）；IPC kill-host 崩溃恢复+restart（11 项，无孤儿进程实测）；设备离线→Failed 定位 |

> 任务书注意项落实：TC-05 为**纯监听判定**，未用"取消本机发送"模拟 ECU 丢帧（`requires_fault_injection` 语义已隔离）。

## 2. P2 退出条件（任务书 16.2）

| 条件 | 判定 | 证据 |
|---|---|---|
| 固定样本集覆盖至少 5 类测试 | **✅** | `examples/test-ir/valid_*.json` 5 类 + `examples/ai_eval` 12 用例（5 正向意图全可执行） |
| 错误引用阻止执行或提出澄清 | **✅** | VALID-01 chk_project_ref（32 测试）；AI 管线幻觉引用服务级拒绝（13 测试）；评测集 N1/N2 拒绝（12/12） |
| 不完整需求能阻止执行 | **✅** | `unconfirmed_items` → ClarificationRequired/FAILED（EXEC 测试）；AI 管线"草案必确认"门；评测 N6/N7 |

## 3. §15.1 MVP 验收指标

| 指标 | 判定 | 证据 |
|---|---|---|
| DBC 解析无静默漏项 | **✅** | 8 类诊断带行:列（含上游静默的 VAL_ 悬引用交叉抓取）；未知/不支持字段显式警告 |
| ≥5 类意图可生成、阈值有来源或待确认 | **✅** | AI 评测 5 正向（真实管线 12/12）；no_fabricated_source 检查 |
| 不存在引用/超 DLC/超能力/未审批被阻止 | **✅** | VALID-01 七类 + CORE-01 结构层 + 审批门（各自独立测试） |
| 同一 IR Mock 重复执行确定性 | **✅** | `test_repeat_execution_deterministic`（摘要+输入摘要+plan_hash 一致）+ COMP 确定性哈希测试 |
| 选定设备实机…（硬件项） | **⏸ 阻塞** | P0 未完成 Go/No-Go（Q1/Q2 未答复）——不属 P2 |
| 断言可追溯原始帧、无证据不 Pass | **✅** | JUDGE inputs_digest/evidence_refs + `无证据绝不 Pass` 测试 + REPORT 断言表含来源与复算说明 |
| 取消/拔设备/初始化失败无残留 | **✅（Mock）** | 取消清理、IPC 崩溃恢复、Mock 急停后重开；**实机项随 P3** |
| 许可与分发审查 | **🟡 部分** | P0-06 初版完成+历史零厂商二进制实审；Q8 书面答复未到（发布前必须闭环） |

## 4. P2 相对发布门槛（14.3）的缺口清单

1. **真实模型评测跑批**（AI-01 验收证据）——等 API Key，命令就绪：`scripts/eval_ai.py --generator llm`；
2. **人工 UI 走查**（有显示器：DPI 100/125/150%、四页交互、脏关闭对话框）——离屏自动化已覆盖结构与流程；
3. **range/enum 的 run 级全链路演示**——decoder 注入装配已在接口层就绪，缺 UI 演示场景接线（P2-D 已留缝）；
4. **DBC 外部基准工具对照**（V 级）——本机无授权工具，待人工；
5. 硬件相关门槛（实机发现/收发/关闭、SDK/驱动/固件入报告）→ **P3**；许可审查闭环 → **Q8**。

## 5. 结论

P2 任务书范围内的**全部可离线验证项均已通过自动化证据闭环**（389 测试）；缺口 1/2 属验收动作（非功能缺失），3 属演示接线，4/5 属外部依赖。P2 可判定 **Done（实现层）**，满足进入"待实测补齐 + P3 预研（随 P0 答复）"状态。
