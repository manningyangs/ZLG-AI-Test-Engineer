# 变更日志

本项目遵循语义化版本；对用户可见的变更记录于此。格式参考 [Keep a Changelog]。

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
