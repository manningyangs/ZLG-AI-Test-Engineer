# Test IR v1.0

**任务：** CORE-01｜**状态：** 已实现｜**日期：** 2026-11-18

Test IR 是与协议、厂商 SDK、脚本语言无关的测试任务表示——本产品的核心可复用资产。
脚本、执行计划和报告都是从 Test IR 派生的（任务书 1.2“不可改变的产品原则”）。

## 文件

| 文件 | 用途 |
|---|---|
| `schemas/test-ir/v1/test_ir.schema.json` | JSON Schema（Draft 2020-12），仓库文档副本 |
| `src/zlg_ai_test_engine/resources/test_ir/v1/test_ir.schema.json` | 运行时加载的包内副本（有一致性测试保证同步） |
| `src/zlg_ai_test_engine/domain/test_ir/` | Pydantic 模型、动作字典、校验与迁移 |
| `examples/test-ir/valid_*.json` | 5 个合法样例（对应 TC-02~TC-06） |
| `examples/test-ir/invalid/*.json` | 非法/门控样例（每个都有测试断言其被阻止） |

## 两级校验

| API | 级别 | 阻断条件 |
|---|---|---|
| `validate_ir(doc)` | draft（设计级） | 结构非法：未知 action、负周期、缺 evidence_source（cycle/range/enum）、payload/DLC 不符、min≥max、未知字段 |
| `validate_executable(doc)` | executable（执行级） | draft 全部 + `unconfirmed_items` 非空 + 预留动作（inject_fault/uds_request）+ 任一断言缺 evidence_source |

辅助 API：`parse_ir`（强类型解析，失败抛 `IRValidationError`）、`to_json`（稳定序列化）、
`migrate_ir`（版本迁移接口，当前 1.0 恒等，未知版本抛 `IRMigrationError`）。

## 动作字典

| action | 默认风险 | MVP | 说明 |
|---|---|---|---|
| listen_bus | L0 | ✅ | 只读监听记录报文 |
| send_frame | L1 | ✅ | 单帧/有限次数发送，需确认 |
| send_periodic | L1 | ✅ | 周期发送，优先设备端硬件定时 |
| monitor_signal | L0 | ✅ | 信号物理值采样 |
| wait_for_frame | L0 | ✅ | 等待帧到达（timeout 必填） |
| assert_cycle | L0 | ✅ | 周期断言（evidence_source 必填） |
| assert_range | L0 | ✅ | 范围断言（min<max、evidence_source 必填） |
| assert_enum | L0 | ✅ | 枚举断言（evidence_source 必填） |
| assert_timeout | L0 | ✅ | 超时断言（executable 级 evidence_source 必填） |
| inject_fault | L2 | 🔒 预留 | 结构合法、执行默认阻断，需批准台架 |
| uds_request | L3 | 🔒 预留 | 1.2 版本按 L3 授权启用 |

机器可读字典：`domain/test_ir/actions.py` 的 `ACTION_SPECS`（有测试保证与 Schema 同步）。

## 关键语义

- **unconfirmed_items 非空 = 执行阻断**：AI 生成的待确认参数必须可见，未确认前不得进入执行。
- **evidence_source**：断言阈值的来源（需求 id/规范章节/人工确认）；无来源不得判 Pass。
- **三时钟**：`device_timestamp` / `host_monotonic_ns` / `wall_clock` 分列于证据帧，不得混用。
- **风险等级**：随 `execution_policy.risk_level`（L0–L4）进入安全审批链（VALID-01/审批流）。

## 版本与兼容策略

- `schema_version` 当前 `"1.0"`。
- **向后兼容变更**（新增可选字段、新增动作类型）→ minor：旧文档必须继续通过校验。
- **破坏性变更**（删除/改语义字段）→ major：`migrate_ir` 登记迁移链 + 兼容测试 + 示例更新（任务书第 17 章）。
- 根目录副本与包内副本由 `tests/unit/test_test_ir.py::test_packaged_schema_matches_repo_schema` 保证一致。
