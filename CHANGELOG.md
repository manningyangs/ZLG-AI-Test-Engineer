# 变更日志

本项目遵循语义化版本；对用户可见的变更记录于此。格式参考 [Keep a Changelog]。

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
