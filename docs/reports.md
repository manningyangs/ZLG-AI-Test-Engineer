# REPORT-01：结果与证据归档 / HTML·PDF 报告导出

实现：`src/zlg_ai_test_engine/application/reporting.py`（测试 `tests/unit/test_reporting.py`）。
入口：`build_run_result` / `EvidenceStore` / `HtmlReportExporter` / `PdfReportExporter`。

## 证据归档结构（EvidenceStore，append-only）

```
base_dir/<run_id>/
├── frames.jsonl    # 每行一帧：t_ms / t_ns / channel / can_id / data_hex / is_fd（含时钟字段原样保留）
├── signals.jsonl   # 每行一个信号采样：t_ms / t_ns / can_id / data_hex
└── meta.json       # {"run": …, "ir": …, "plan": …} 摘要（首次归档快照）
```

- **只追加，绝不截断覆盖**：同一 run 重复 `persist_run` 只向 JSONL 追加行（旧内容前缀保留）；
  `meta.json` 已存在则不重写；不同 run 各自目录，互不混入。
- 三个文件缺数据也生成，保证目录结构完整；`persist_run` 返回
  `{"paths": [三个绝对路径], "sha256": {文件名: 哈希}}`。
- 提供 `session_factory` 时追加 `EvidenceArtifact` 索引行（`type` ∈ raw_frame /
  signal_sample / run_meta，含 `path`、`sha256`、`run_id`）；`execution_run` 父行缺失时
  按当前运行状态补建（外键约束；已存在绝不更新，索引行同样只增不改）。
- 报告文件是**派生产物**：删除 HTML/PDF 不影响任何证据文件；导出器对证据只读。

## 报告字段（`build_run_result(rec)`，JSON 可序列化）

```jsonc
{
  "run":     { run_id, state, adapter_id, device_id, runner_status, approval, approver,
               created_at/started_at/ended_at（ISO 8601 或 null）, error, cleanup_errors,
               ai_explainer, clock },
  "ir":      { case_id, title, suite_id, protocol: {bus, database}, risk_level,
               requires_approval, unconfirmed_count },
  "plan":    { compiler_version, ir_hash, plan_hash } | null,   // rec.plan 为 None → null
  "summary": { "Pass": n, "Fail": n, "Blocked": n, "NotEvaluated": n },
  "results": [ AssertionResult.to_dict() … ],
  "evidence": { frames: [...], signals: [...], window_ns: [start, end] | null } | null
}
```

HTML 导出（`html_report`，自包含：内联 CSS、UTF-8 中文、无外部资源）：头部为
run/ir/plan/版本信息；summary 四色卡片（Pass 绿 `#16a34a`、Fail 红 `#dc2626`、
Blocked 橙 `#ea580c`、NotEvaluated 灰）；断言表列出 verdict / reason / threshold /
computed / evidence_refs / **阈值来源 evidence_source** / inputs_digest；证据区列
证据文件路径与帧·信号·窗口统计；AI 解释常驻角标“**建议/推测，不覆盖机器判定**”。

PDF 导出（`pdf_report`）：PySide6 `QTextDocument + QPdfWriter` 渲染与 HTML **同一内容**；
进程内无 GUI 应用时先离屏创建 QApplication（`QT_QPA_PLATFORM=offscreen` 兜底），
仍不可用则抛 `RuntimeError("PDF 导出需要图形环境")`——HTML 永远可用。
两个导出器返回 `ExportResult(output_path, sha256=输出文件哈希)`。

## 复算说明

- `inputs_digest = sha256(canonical JSON(判定输入 + threshold))[:16]`（JUDGE-01 `_digest`）；
  同一证据、同一 `threshold`、同一 `rule_version` 重放判定必须得到相同 `verdict`，
  报告中并列展示 threshold / computed / inputs_digest 供人工或脚本比对。
- `evidence_source`（阈值来源）由 Test IR 步骤声明（需求/规范编号），报告只呈现、不改写。
- 判定由 JUDGE-01 确定性产生；AI 解释仅附加说明，**不覆盖机器判定**。
- 报告与日志不得包含任何 API Key / 密钥。
