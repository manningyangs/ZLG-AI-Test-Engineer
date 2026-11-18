# 插件契约说明（ARCH-01）

**状态：** 已实现（API v1.0）｜**日期：** 2026-11-18｜**对应任务：** 任务书 ARCH-01

## 总览

六维扩展解耦（任务书 2.3）通过 `src/zlg_ai_test_engine/domain/plugins/contracts.py` 中的抽象基类表达，
注册与版本管理在 `src/zlg_ai_test_engine/plugins/registry.py`，清单 Schema 在
`src/zlg_ai_test_engine/plugins/manifest.schema.json`，确定性 Fake 在 `src/zlg_ai_test_engine/plugins/fakes.py`。

| 扩展维度 | 契约 | 位置 | 禁止事项（架构红线） |
|---|---|---|---|
| 通信协议 | `ProtocolPlugin` | contracts.py | 不得调用厂商 DLL |
| 硬件 SDK/设备 | `DeviceSDKAdapter` | contracts.py | UI/AI 不得绕过本接口触碰设备 |
| 工程文件/记录格式 | `AssetParser` | contracts.py | 不得操作设备 |
| 脚本后端 | `ScriptBackend` | contracts.py | 生成阶段不得执行代码 |
| 执行后端 | `ExecutionBackend` | contracts.py | 编译必须确定性（禁用 LLM） |
| 模型/知识 | `LLMProvider` | contracts.py | 不得执行工具动作/访问文件系统 |
| 报告/集成 | `ReportExporter` | contracts.py | 只读证据，不得覆盖原始文件 |

> 注：任务书 2.3 的"模型与知识来源"维度拆分为 LLMProvider（本任务）与 KnowledgeConnector（AI-02）两个契约。

## 契约清单（PluginManifest）

```json
{
  "id": "protocol_can",
  "version": "1.0.0",
  "api_version": "1.0",
  "license": "AGPL-3.0-only",
  "capabilities": ["can", "can_fd"],
  "dependencies": [],
  "entrypoint": "pkg.module:ClassName"
}
```

- 校验：JSON Schema（Draft 2020-12）+ `api_version` major 必须等于宿主 `PLUGIN_API_VERSION`（当前 1.0）。
- **不兼容/非法/重复的插件注册返回 False 并记录到 `PluginRegistry.rejected`，绝不阻塞应用启动**。
- 同一 id 可在不同 PluginKind 下共存。

## 关键值对象

- `Diagnostic(code, message, severity, location)` — 解析/校验问题，禁止静默丢弃。
- `Frame` — 帧数据 + **三个时间字段**（`device_timestamp(+unit)` / `host_monotonic_ns` / `wall_clock`），不得混用。
- `ChannelConfig` — 通道配置，调用前须过 VALID-01。
- `SendResult` — API 接收数 ≠ ECU 响应；`EmergencyStopReport` — 急停各清理项单独记录。
- `RiskLevel` — L0–L4（与任务书第 13 章一致）。

## 能力语义（重要）

`DeviceSDKAdapter.is_capable()`：**未知能力必须返回 False**（Unknown ≠ Supported）。
证据等级三态（`official_declared` / `sdk_visible` / `verified_on_device`）由 Capability Profile（P3 SDK-03）维护。

## 兼容策略

- 新增可选字段/新插件类别成员 → minor（向后兼容）；
- 删除/改语义 → major，需同步迁移与兼容测试（任务书第 17 章）。

## 测试

`tests/contract/test_plugin_contracts.py`：
注册/拒绝/重复/api 兼容、协议编解码往返、设备生命周期与急停释放、解析诊断、
脚本与计划编译确定性、LLM 错误分类、报告导出 —— 共 24 项契约断言。
