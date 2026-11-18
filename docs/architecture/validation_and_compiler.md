# 执行前校验与 Mock 计划编译（VALID-01 + COMP-01）

**状态：** 已实现（v1.0）｜**日期：** 2026-11-18｜**对应任务：** 任务书 10.2（VALID-01）、10.3（COMP-01）

## 总览

| 模块 | 位置 | 职责 |
|---|---|---|
| VALID-01 规则校验器 | `src/zlg_ai_test_engine/domain/validation/validator.py` | 执行前七类集中检查，ERROR 阻断、WARNING 降级 |
| COMP-01 编译器 | `src/zlg_ai_test_engine/domain/execution/compiler.py` | 有效 Test IR → 确定性 Mock 执行计划（禁用 LLM、无 IO） |

数据流：`Test IR dict` → `validate_execution(ctx)`（七类检查）→ `compile_to_mock_plan(ir, capabilities=...)` → 执行器（EXEC-01a）按固定计划形状消费。两者都复用 CORE-01 的 `ValidationResult` / `IRValidationError` 语义：**diagnostics 无 ERROR 即 ok**。

## VALID-01：七类检查

### 输入：ValidationContext（frozen dataclass）

| 字段 | 含义 |
|---|---|
| `ir` | 已过 CORE-01 draft 级校验的 IR dict |
| `asset_registry` | `asset_id -> {"sha256", "status": "active\|stale\|missing", "type"}` |
| `network_model` | 解析后的 NetworkModel；`None` → 引用检查降级出 WARNING |
| `capabilities` | `MockAdapter.device_capabilities()` 形状（`buses` + `evidence` 四级） |
| `approval` | `{"user_level": "L0".."L4", "approved_actions": [..], "environment": str}` |
| `resource_state` | `{"device_busy", "busy_channels", "periodic_active", "queue_active"}` |

### 检查清单（每类 ERROR 的 message 必含“修正建议”）

| # | code 前缀 | 检查内容 | 失败处置 |
|---|---|---|---|
| 1 | `chk_project_ref` | `protocol.database` / `database_refs.asset_id` 必须在注册表且 `status=="active"`、sha256 一致；message/signal 引用在 NetworkModel 中存在 | 未注册 / 非 active / 哈希失配 / 引用不存在 → ERROR；网络模型缺失或无对应清单 → WARNING（引用待核） |
| 2 | `chk_protocol` | `protocol.bus`（CAN/CAN_FD）必须出现在能力视图 `buses` | ERROR `bus_unsupported` |
| 3 | `chk_device_cap` | `execution_policy.target_capabilities` 每项在 `capabilities.evidence` 中的等级须为 `official_declared`/`sdk_visible`/`verified_on_device` | `unknown` 或缺失 → ERROR `unproven_capability`（**未知 ≠ 支持**） |
| 4 | `chk_param` | 防御性复查：`max_duration_ms`/`max_frames` 为正、`period_ms`/`start_delay_ms`/`can_id` 范围、payload 十六进制与非 FD ≤8 字节、`dlc` 与帧类型、`is_fd` 必须匹配 `protocol.bus`、duration/timeout 为正 | ERROR（即使 CORE-01 结构层漏过也在此拦截，如 `is_fd` on CAN、无 payload 的超限 dlc） |
| 5 | `chk_security` | 风险 L1+ 且 `requires_approval` 时：受控动作（`ACTION_SPECS` 默认风险 ≥ L1，即 send_*）必须在 `approved_actions`，且 `user_level ≥ risk_level`（L0<L1<…<L4 数值比较） | 缺审批动作 / 等级不足 / 等级无法确认 → ERROR；**L4 → 恒 ERROR**（MVP 禁止，与审批无关） |
| 6 | `chk_resource` | `device_busy`；IR 所需通道（`preconditions.device_ready.channel`，缺省 0）∈ `busy_channels`；IR 含 `send_periodic` 且 `periodic_active`/`queue_active`（互斥规则） | ERROR `device_busy` / `channel_busy` / `periodic_conflict` |
| 7 | `chk_plan` | `execution_policy` 缺 `max_duration_ms`（停止条件缺失）→ ERROR；无任何 `assert_*` 步骤 → WARNING；`unconfirmed_items` 非空 → ERROR（继承 executable 语义） | ERROR / WARNING |

### 关键约定

- **入口门禁**：`validate_execution` 先调 `validate_executable(ir)`，结构级失败（`ir_schema`/`ir_model`/`unconfirmed_items`/`reserved_action`）**原样透传**，不再进入七类检查。
- **降级不阻断**：`network_model is None` 或模型不含 messages/signals 清单时，对应引用检查只出 `chk_project_ref.references_pending` WARNING，**不得升级为 ERROR**。
- **NetworkModel 形状容错**：`messages`/`signals` 兼容 `{名: 条目}` 与 `[{条目}]` 两种容器，信号也可嵌套在报文的 `signals`/`signal_names` 中（DATA-01 输出形状未定时的稳定兼容层）。
- 纯函数：不触碰设备、不做 IO；检查顺序固定（1→7），诊断顺序确定。

## COMP-01：Mock 执行计划编译

### 输入门禁

`compile_to_mock_plan` 先执行 `validate_executable(ir).raise_if_failed()`：结构错误、`unconfirmed_items` 非空、MVP 预留动作（`inject_fault`/`uds_request`）一律抛 `IRValidationError`。

### 计划形状（固定，执行器按此消费）

```json
{
  "plan_version": "1.0",
  "compiler_version": "1.0",
  "backend": "mock",
  "ir_hash": "<sha256(canonical_json(ir))>",
  "plan_hash": "<sha256(canonical_json(plan 剔除 plan_hash 字段))>",
  "device": {"device_id": "mock-0", "channel": 0, "bus_type": "CAN",
             "listen_only": false, "risk_level": "L1", "requires_approval": true},
  "resources": {"capabilities_required": ["basic_rx", "basic_tx"],
                "max_duration_ms": 10000, "max_frames": 10000},
  "commands": [ /* seq 连续、type 见下表 */ ],
  "evidence_subscriptions": [{"kind": "frames", "channel": 0},
                             {"kind": "signals", "names": ["S"]}],
  "cleanup": [{"type": "stop_sends"}, {"type": "stop_receiving"},
              {"type": "reset_channel", "channel_index": 0},
              {"type": "close_device"}]
}
```

### 命令映射与序列规则

| IR action | 命令 type | 参数来源 |
|---|---|---|
| （前置） | `configure_channel` / `start_channel` | 置顶；`channel_index` 来自 `device_ready`（缺省 0），`bus_type` 来自 `protocol.bus`，`listen_only` 见下 |
| `listen_bus` | `listen` | `duration_ms`（+可选 `filter`）按 IR 原样 |
| `send_frame` | `send_frame` | `message_ref`/`can_id`/`payload_hex`/`dlc`/`is_fd`(仅 true)/`count` + `channel` |
| `send_periodic` | `send_periodic` | 同上 + `period_ms`、`start_delay_ms`（缺省 0）、`count` 推导：显式 `count` → `duration_ms // period_ms` → 缺省省略（不限次，由 `max_duration_ms` 停止） |
| `monitor_signal` | `monitor` | `message`/`signal`/`duration_ms`（+可选 `sample_ms`） |
| `wait_for_frame` | `wait` | `timeout_ms`（+可选 `message`/`can_id`） |
| `assert_*` | `assert` | 步骤参数**原样**（剔除 `id`/`action`），`evidence_source` 提升到命令级 |

- **序列**：`configure_channel` → `start_channel` 置顶；采集/发送命令按 IR `steps` 顺序生成；**全部 `assert_*` 命令排在最后（所有采集命令之后）**，保证断言执行时证据已采集；`seq` 从 1 连续编号。
- **cleanup 恒为固定四件套**：`stop_sends` → `stop_receiving` → `reset_channel(channel_index=通道)` → `close_device`。
- **listen_only**：`risk_level == "L0" 且 IR 无 send_frame/send_periodic` → true（只读用例才允许只听模式）。
- **capabilities_required**：`execution_policy.target_capabilities` ∪ 动作隐含需求（`send_*` → `basic_tx`，listen/monitor/wait/assert → `basic_rx`），排序去重。
- **资源回退**：`max_duration_ms` = policy 值 → 各步骤时间跨度上界推导（listen/monitor/wait/assert_timeout/send_periodic 的 count×period 等）→ 默认 10000；`max_frames` = policy 值 → 默认 10000。
- **证据订阅**：恒含 `frames`（channel）；IR 中 `monitor_signal`/`assert_range`/`assert_enum` 的信号名去重排序后生成 `signals` 订阅（无信号类步骤则省略）。

### 确定性与哈希

- canonical json 定义（唯一基准）：`json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`，UTF-8 编码后取 SHA-256。
- `ir_hash` = canonical json(ir) 的哈希；`plan_hash` = 剔除 `plan_hash` 字段后的整个计划的哈希。
- 同一 IR + 同一 capabilities + 同一 `compiler_version` → 两次编译 dict 完全相等（无随机、无时间戳、无 LLM、无 IO）；IR 任一字段变化 → `ir_hash` 变化。

## 测试

| 文件 | 覆盖 |
|---|---|
| `tests/unit/test_validator.py` | 七类各 ≥1 正 1 反（反例断言 code 前缀 + message 含“修正建议”）；unknown 能力、stale/未注册/哈希失配资产、L1 未审批/已审批、L4 恒阻断、周期/队列互斥、最小 IR 七类全过、降级 WARNING 不升级、结构错误原样透传 |
| `tests/unit/test_compiler.py` | 计划形状与命令序列、四件套 cleanup、assert 殿后、参数/次数推导、listen_only、canonical 哈希复算、两次编译相等、IR 变动哈希变化、unconfirmed/预留动作抛 `IRValidationError` |
