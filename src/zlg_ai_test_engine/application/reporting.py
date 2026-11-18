"""REPORT-01：运行结果标准化 + 证据归档（append-only）+ HTML/PDF 报告导出。

设计红线（任务书 10.5 / 14）：
- 判定由 JUDGE-01 确定性产生，报告只**呈现**结果；AI 解释仅作建议/推测，
  一律带“建议/推测，不覆盖机器判定”角标，绝不改写 verdict；
- 证据 append-only：EvidenceStore 对已存在文件只追加、绝不截断覆盖；
  导出器（HTML/PDF）**只读**证据，报告是可随时删除的派生产物；
- 报告自包含（内联 CSS、无外部资源、UTF-8 中文），展示 inputs_digest 与
  阈值来源（evidence_source）以支持复算比对；
- 报告内容不得包含任何 API Key / 密钥。

PDF 依赖 PySide6（QTextDocument + QPdfWriter）：进程内无 GUI 应用时先尝试
离屏创建 QApplication（QT_QPA_PLATFORM=offscreen 兜底），仍不可用则抛
``RuntimeError("PDF 导出需要图形环境")``——HTML 导出永远可用。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from zlg_ai_test_engine.domain.execution.orchestrator import RunRecord
from zlg_ai_test_engine.domain.plugins.contracts import ExportResult, ReportExporter
from zlg_ai_test_engine.infrastructure.persistence.models import (
    EvidenceArtifact,
    ExecutionRun,
)

#: 报告格式版本（写入报告头部，保证可追溯）
REPORT_VERSION = "report-1.0"

#: AI 说明角标文案（验收：报告必须出现“不覆盖机器判定”）
AI_BADGE_TEXT = "AI 解释仅为建议/推测，不覆盖机器判定"

#: 无 clock 属性时的口径回退（与 EXEC-01a RunnerOutcome.clock 默认一致）
_CLOCK_FALLBACK = "host_monotonic_ns(mock_virtual)"

#: summary 四色卡片（任务书配色）
_SUMMARY_COLORS: dict[str, str] = {
    "Pass": "#16a34a",
    "Fail": "#dc2626",
    "Blocked": "#ea580c",
    "NotEvaluated": "#6b7280",
}

_VERDICT_CSS: dict[str, str] = {
    "Pass": "v-pass",
    "Fail": "v-fail",
    "Blocked": "v-blocked",
    "NotEvaluated": "v-noteval",
}

#: 证据文件 → EvidenceArtifact.type
_ARTIFACT_TYPES: dict[str, str] = {
    "frames.jsonl": "raw_frame",
    "signals.jsonl": "signal_sample",
    "meta.json": "run_meta",
}

#: run_id → 目录名安全字符（防止路径注入）
_SAFE_DIR_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None if value is None else str(value)


def _combined_window(evidence: Any) -> list[int] | None:
    combiner = getattr(evidence, "combined_window", None)
    if not callable(combiner):
        return None
    window = combiner()
    if isinstance(window, (list, tuple)) and len(window) == 2:
        return [int(window[0]), int(window[1])]
    return None


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 1) 标准运行结果（UI / 证据归档 / 报告共用）
# ---------------------------------------------------------------------------


def build_run_result(rec: RunRecord) -> dict[str, Any]:
    """把 RunRecord 归一为 JSON 可序列化的标准运行结果。

    结构::

        {"run": {...}, "ir": {...}, "plan": {...}|None,
         "summary": {...}, "results": [...], "evidence": {...}|None}
    """
    evidence = rec.evidence
    ir = _mapping(rec.ir)
    policy = _mapping(ir.get("execution_policy"))
    protocol = _mapping(ir.get("protocol"))
    unconfirmed = ir.get("unconfirmed_items")

    run: dict[str, Any] = {
        "run_id": rec.run_id,
        "state": rec.state,
        "adapter_id": rec.adapter_id,
        "device_id": rec.device_id,
        "runner_status": rec.runner_status,
        "approval": rec.approval,
        "approver": rec.approver,
        "created_at": _iso(rec.created_at),
        "started_at": _iso(rec.started_at),
        "ended_at": _iso(rec.ended_at),
        "error": rec.error,
        "cleanup_errors": list(rec.cleanup_errors or []),
        "ai_explainer": rec.ai_explanation,
        "clock": (
            None
            if evidence is None
            else (getattr(evidence, "clock", None) or _CLOCK_FALLBACK)
        ),
    }

    ir_out: dict[str, Any] = {
        "case_id": ir.get("case_id"),
        "title": ir.get("title"),
        "suite_id": ir.get("suite_id"),
        "protocol": {"bus": protocol.get("bus"), "database": protocol.get("database")},
        "risk_level": policy.get("risk_level"),
        "requires_approval": bool(policy.get("requires_approval")),
        "unconfirmed_count": (
            len(unconfirmed) if isinstance(unconfirmed, (list, tuple)) else 0
        ),
    }

    plan: dict[str, Any] | None = None
    if rec.plan is not None:
        src = _mapping(rec.plan)
        plan = {
            "compiler_version": src.get("compiler_version"),
            "ir_hash": src.get("ir_hash"),
            "plan_hash": src.get("plan_hash"),
        }

    evidence_out: dict[str, Any] | None = None
    if evidence is not None:
        evidence_out = {
            "frames": list(getattr(evidence, "frames", None) or []),
            "signals": list(getattr(evidence, "signals", None) or []),
            "window_ns": _combined_window(evidence),
        }

    return {
        "run": run,
        "ir": ir_out,
        "plan": plan,
        "summary": dict(rec.summary),
        "results": [r.to_dict() for r in rec.results],
        "evidence": evidence_out,
    }


# ---------------------------------------------------------------------------
# 2) 证据归档（append-only）
# ---------------------------------------------------------------------------


def _run_dirname(run_id: str) -> str:
    """run_id → 安全目录名（空白/分隔符替换为 `_`，防路径注入）。"""
    name = _SAFE_DIR_RE.sub("_", str(run_id)).strip("._")
    return name or "_run"


def _append_jsonl(path: Path, items: Sequence[Mapping[str, Any]]) -> None:
    """追加写 JSONL：仅以追加模式打开，绝不截断既有内容。"""
    with path.open("a", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvidenceStore:
    """原始证据归档：``base_dir/<run_id>/`` 下的追加式文件 + 可选索引行。

    文件（缺数据也生成，保证目录结构完整）：
    - ``frames.jsonl``  每行一帧（t_ms/t_ns/channel/can_id/data_hex/is_fd 等）
    - ``signals.jsonl`` 每行一个信号采样（t_ms/t_ns/can_id/data_hex）
    - ``meta.json``     ``{"run", "ir", "plan"}`` 摘要（首次归档快照，不覆盖）

    append-only：同一 run 重复调用只向 JSONL 追加行；meta.json 已存在绝不
    覆盖；不同 run 各自目录互不干扰。提供 session_factory 时追加
    EvidenceArtifact 索引行（type/path/sha256/run_id；父行 execution_run
    缺失时按当前状态补建——外键约束所需，已存在则不改）。
    """

    def __init__(self, base_dir: Path, session_factory: Any = None) -> None:
        self._base_dir = Path(base_dir)
        self._session_factory = session_factory

    def persist_run(self, rec: RunRecord) -> dict[str, Any]:
        """归档一次运行的证据，返回 ``{"paths": [...], "sha256": {文件名: 哈希}}``。"""
        run_dir = self._base_dir / _run_dirname(rec.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        evidence = rec.evidence
        frames = list(getattr(evidence, "frames", None) or []) if evidence is not None else []
        signals = list(getattr(evidence, "signals", None) or []) if evidence is not None else []

        frames_path = run_dir / "frames.jsonl"
        signals_path = run_dir / "signals.jsonl"
        meta_path = run_dir / "meta.json"

        _append_jsonl(frames_path, frames)
        _append_jsonl(signals_path, signals)
        if not meta_path.exists():  # 已存在绝不覆盖（append-only）
            run_result = build_run_result(rec)
            meta = {
                "run": run_result["run"],
                "ir": run_result["ir"],
                "plan": run_result["plan"],
            }
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        paths = [frames_path, signals_path, meta_path]
        hashes = {p.name: _sha256_file(p) for p in paths}
        if self._session_factory is not None:
            self._record_artifacts(rec, paths, hashes)
        return {"paths": [str(p) for p in paths], "sha256": hashes}

    def _record_artifacts(
        self,
        rec: RunRecord,
        paths: Sequence[Path],
        hashes: Mapping[str, str],
    ) -> None:
        with self._session_factory() as session, session.begin():
            # EvidenceArtifact.run_id 外键指向 execution_run：父行缺失时补建
            #（append-only：已存在绝不更新）。两模型间无 relationship()，
            # 同一 flush 内插入顺序不保证，故显式 flush 先落父行。
            if session.get(ExecutionRun, rec.run_id) is None:
                session.add(
                    ExecutionRun(
                        run_id=rec.run_id,
                        case_id=_mapping(rec.ir).get("case_id"),
                        adapter_id=rec.adapter_id,
                        state=rec.state,
                        created_at=rec.created_at,
                        started_at=rec.started_at,
                        ended_at=rec.ended_at,
                        meta_json={"device_id": rec.device_id},
                    )
                )
                session.flush()
            for path in paths:
                session.add(
                    EvidenceArtifact(
                        artifact_id=str(uuid.uuid4()),
                        run_id=rec.run_id,
                        type=_ARTIFACT_TYPES.get(path.name, "raw_frame"),
                        path=str(path),
                        sha256=hashes.get(path.name, ""),
                    )
                )


# ---------------------------------------------------------------------------
# 3) HTML 渲染（HTML/PDF 导出共用同一内容）
# ---------------------------------------------------------------------------

_STYLE = """
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
       margin: 0; padding: 24px; background: #f3f4f6; color: #111827;
       line-height: 1.55; }
header { margin-bottom: 16px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.subtitle { color: #6b7280; font-size: 12px; }
h2 { font-size: 16px; margin: 0 0 10px; color: #1f2937;
     border-left: 4px solid #2563eb; padding-left: 8px; }
section { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
          padding: 16px; margin-bottom: 16px; }
.kv { display: grid; grid-template-columns: 150px 1fr; gap: 4px 12px;
      font-size: 13px; }
.kv .k { color: #6b7280; }
.kv .val { word-break: break-all; font-family: Consolas, monospace; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; }
.card { flex: 1 1 140px; min-width: 130px; border-radius: 8px;
        padding: 14px 16px; color: #fff; }
.card .num { display: block; font-size: 28px; font-weight: 700; }
.card .lab { font-size: 13px; opacity: .92; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left;
         vertical-align: top; word-break: break-all; }
th { background: #f9fafb; color: #374151; white-space: nowrap; }
.v { display: inline-block; padding: 1px 8px; border-radius: 10px;
     color: #fff; font-size: 12px; }
.v-pass { background: #16a34a; }
.v-fail { background: #dc2626; }
.v-blocked { background: #ea580c; }
.v-noteval { background: #6b7280; }
.badge-ai { display: inline-block; background: #fef3c7;
            border: 1px solid #f59e0b; color: #92400e; border-radius: 10px;
            padding: 2px 10px; font-size: 12px; margin-bottom: 8px; }
.note { font-size: 12px; color: #6b7280; background: #f9fafb;
        border: 1px dashed #d1d5db; padding: 8px 10px; border-radius: 6px; }
.dim { color: #6b7280; font-size: 13px; }
code { font-family: Consolas, monospace; font-size: 12px; }
ul.ev { margin: 6px 0; padding-left: 20px; font-size: 13px; }
"""


def _esc(value: Any) -> str:
    """转义为 HTML 文本（None → 占位符）。"""
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _json_cell(value: Any) -> str:
    if value is None or value == {} or value == []:
        return "—"
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover — 非法结构兜底
        text = str(value)
    return html.escape(text, quote=True)


def _kv_rows(pairs: Sequence[tuple[str, Any]]) -> str:
    rows = "".join(
        f'<div class="k">{_esc(label)}</div><div class="val">{_esc(value)}</div>'
        for label, value in pairs
    )
    return f'<div class="kv">{rows}</div>'


def _summary_cards(counts: Mapping[str, int]) -> str:
    cards = []
    for key in ("Pass", "Fail", "Blocked", "NotEvaluated"):
        cards.append(
            f'<div class="card" style="background:{_SUMMARY_COLORS[key]}">'
            f'<span class="num">{_to_int(counts.get(key, 0))}</span>'
            f'<span class="lab">{key}</span></div>'
        )
    return f'<div class="cards">{"".join(cards)}</div>'


def _verdict_cell(item: Mapping[str, Any]) -> str:
    verdict = item.get("verdict")
    css = _VERDICT_CSS.get(str(verdict))
    if css is None:
        return _esc(verdict)
    return f'<span class="v {css}">{_esc(verdict)}</span>'


def _result_rows(results_list: Sequence[Any]) -> str:
    """断言明细行：verdict/reason/threshold/computed/evidence_refs/
    阈值来源 evidence_source/inputs_digest。非法条目原样转义，不崩溃。"""
    if not results_list:
        return '<tr><td colspan="9">（无断言结果）</td></tr>'
    rows: list[str] = []
    for item in results_list:
        if isinstance(item, Mapping):
            threshold = item.get("threshold")
            source = "—"
            if isinstance(threshold, Mapping):
                raw_source = threshold.get("evidence_source")
                if raw_source not in (None, ""):
                    source = _esc(raw_source)
            refs = item.get("evidence_refs")
            if isinstance(refs, (list, tuple)):
                refs_text = ", ".join(_esc(r) for r in refs) or "—"
            else:
                refs_text = _esc(refs)
            cells = [
                _esc(item.get("step_id")),
                _esc(item.get("action")),
                _verdict_cell(item),
                _esc(item.get("reason")),
                _json_cell(threshold),
                _json_cell(item.get("computed")),
                refs_text,
                source,
                f'<code>{_esc(item.get("inputs_digest"))}</code>',
            ]
        else:
            cells = ["—", "—", _esc(None), _esc(item), "—", "—", "—", "—", "—"]
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return "".join(rows)


def _recalc_note(results_list: Sequence[Any]) -> str:
    rule_versions = sorted(
        {
            str(item.get("rule_version"))
            for item in results_list
            if isinstance(item, Mapping) and item.get("rule_version")
        }
    )
    rules = f"（规则版本 {', '.join(rule_versions)}）" if rule_versions else ""
    return (
        '<p class="note">复算说明：inputs_digest = '
        "sha256(canonical JSON(判定输入 + threshold))[:16]；同一证据、同一阈值按"
        f"相同规则版本重放判定必须得到相同 verdict{rules}。"
        "阈值来源 evidence_source 由 Test IR 步骤声明，报告只呈现、不改写。</p>"
    )


def _evidence_stats(evidence: Mapping[str, Any] | None) -> str:
    if evidence is None:
        return '<p class="note">本次运行未采集到证据数据（evidence = null）。</p>'
    frames = evidence.get("frames")
    signals = evidence.get("signals")
    window = evidence.get("window_ns")
    if (
        isinstance(window, (list, tuple))
        and len(window) == 2
        and all(isinstance(x, (int, float)) for x in window)
    ):
        span_ms = (float(window[1]) - float(window[0])) / 1_000_000
        window_text = f"{window[0]} ~ {window[1]} ns（{span_ms:.3f} ms）"
    else:
        window_text = "—"
    pairs = [
        ("帧数 frames", len(frames) if isinstance(frames, (list, tuple)) else "—"),
        (
            "信号采样 signals",
            len(signals) if isinstance(signals, (list, tuple)) else "—",
        ),
        ("监听窗口 window_ns", window_text),
    ]
    return _kv_rows(pairs)


def render_run_result_html(
    run_result: Mapping[str, Any], evidence_paths: Sequence[str]
) -> str:
    """把标准运行结果渲染为自包含 HTML（HTML/PDF 导出共用；只读输入）。"""
    run = _mapping(run_result.get("run"))
    ir = _mapping(run_result.get("ir"))
    plan_raw = run_result.get("plan")
    plan = _mapping(plan_raw)
    results_raw = run_result.get("results")
    results_list = results_raw if isinstance(results_raw, list) else []
    summary_raw = run_result.get("summary")
    evidence_raw = run_result.get("evidence")
    evidence = evidence_raw if isinstance(evidence_raw, Mapping) else None

    case_id = ir.get("case_id")
    run_id = run.get("run_id")
    title = f"{case_id if case_id else '未命名用例'} · {run_id if run_id else '-'}"

    # -- 头部：版本信息 -------------------------------------------------------
    subtitle = (
        f'<div class="subtitle">报告版本 {REPORT_VERSION} · '
        f"确定性判定呈现，AI 仅解释</div>"
    )

    # -- run 摘要 -------------------------------------------------------------
    cleanup_errors = run.get("cleanup_errors")
    if isinstance(cleanup_errors, (list, tuple)) and cleanup_errors:
        cleanup_text = "；".join(str(e) for e in cleanup_errors)  # _kv_rows 统一转义
    else:
        cleanup_text = "（无）"
    run_pairs = [
        ("run_id", run.get("run_id")),
        ("状态 state", run.get("state")),
        ("适配器 adapter_id", run.get("adapter_id")),
        ("设备 device_id", run.get("device_id")),
        ("执行器状态 runner_status", run.get("runner_status")),
        ("审批 approval", run.get("approval")),
        ("审批人 approver", run.get("approver")),
        ("创建时间 created_at", run.get("created_at")),
        ("开始时间 started_at", run.get("started_at")),
        ("结束时间 ended_at", run.get("ended_at")),
        ("错误 error", run.get("error")),
        ("清理错误 cleanup_errors", cleanup_text),
        ("证据时钟 clock", run.get("clock")),
    ]

    # -- ir 摘要 --------------------------------------------------------------
    protocol = _mapping(ir.get("protocol"))
    ir_pairs = [
        ("case_id", ir.get("case_id")),
        ("title", ir.get("title")),
        ("suite_id", ir.get("suite_id")),
        ("协议 bus", protocol.get("bus")),
        ("数据库 database", protocol.get("database")),
        ("风险等级 risk_level", ir.get("risk_level")),
        (
            "需要审批 requires_approval",
            "是" if ir.get("requires_approval") else "否",
        ),
        ("未确认项 unconfirmed_count", ir.get("unconfirmed_count")),
    ]

    # -- plan / 版本 ----------------------------------------------------------
    if plan_raw is None:
        plan_pairs = [("编译计划", "（未编译：本次运行无计划信息）")]
    else:
        plan_pairs = [
            ("compiler_version", plan.get("compiler_version")),
            ("ir_hash", plan.get("ir_hash")),
            ("plan_hash", plan.get("plan_hash")),
        ]

    # -- summary 四色卡片 -----------------------------------------------------
    counts: dict[str, int] = {"Pass": 0, "Fail": 0, "Blocked": 0, "NotEvaluated": 0}
    if isinstance(summary_raw, Mapping):
        for key in counts:
            counts[key] = _to_int(summary_raw.get(key))
    else:  # 无 summary 时按 results 现算
        for item in results_list:
            if isinstance(item, Mapping):
                verdict = str(item.get("verdict") or "")
                if verdict in counts:
                    counts[verdict] += 1

    # -- 证据区 ---------------------------------------------------------------
    paths = [str(p) for p in (evidence_paths or ())]
    if paths:
        paths_html = '<ul class="ev">' + "".join(
            f"<li><code>{html.escape(p, quote=True)}</code></li>" for p in paths
        ) + "</ul>"
    else:
        paths_html = '<p class="dim">（未提供证据文件路径）</p>'

    # -- AI 解释（角标常驻：机器判定不被 AI 覆盖）-----------------------------
    ai_text = run.get("ai_explainer")
    ai_body = _esc(ai_text) if ai_text else '<span class="dim">本次运行未生成 AI 解释。</span>'

    sections = [
        "<header>",
        f"<h1>测试运行报告：{_esc(title)}</h1>",
        subtitle,
        "</header>",
        "<section>",
        "<h2>运行信息 Run</h2>",
        _kv_rows(run_pairs),
        "</section>",
        "<section>",
        "<h2>用例信息 IR</h2>",
        _kv_rows(ir_pairs),
        "</section>",
        "<section>",
        "<h2>编译计划与版本 Plan</h2>",
        _kv_rows(plan_pairs),
        "</section>",
        "<section>",
        "<h2>断言汇总 Summary</h2>",
        _summary_cards(counts),
        "</section>",
        "<section>",
        "<h2>断言明细 Results</h2>",
        "<table>",
        "<thead><tr>"
        "<th>步骤 step_id</th><th>动作 action</th><th>判定 verdict</th>"
        "<th>原因 reason</th><th>阈值 threshold</th><th>计算值 computed</th>"
        "<th>证据引用 evidence_refs</th><th>阈值来源 evidence_source</th>"
        "<th>复算摘要 inputs_digest</th>"
        "</tr></thead>",
        "<tbody>",
        _result_rows(results_list),
        "</tbody>",
        "</table>",
        _recalc_note(results_list),
        "</section>",
        "<section>",
        "<h2>证据归档 Evidence</h2>",
        paths_html,
        _evidence_stats(evidence),
        "</section>",
        "<section>",
        "<h2>AI 解释</h2>",
        f'<span class="badge-ai">{html.escape(AI_BADGE_TEXT, quote=True)}</span>',
        f"<p>{ai_body}</p>",
        "</section>",
    ]

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>测试运行报告 {_esc(title)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        + "\n".join(sections)
        + "\n</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# 4) HTML 导出器
# ---------------------------------------------------------------------------


class HtmlReportExporter(ReportExporter):
    """自包含 HTML 报告（内联 CSS、UTF-8 中文、只读证据）。"""

    @property
    def exporter_id(self) -> str:
        return "html_report"

    @property
    def report_format(self) -> str:
        return "html"

    def export(
        self,
        run_result: Mapping[str, Any],
        evidence_paths: Sequence[str],
        output_path: Path,
    ) -> ExportResult:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = render_run_result_html(run_result, evidence_paths).encode("utf-8")
        out.write_bytes(payload)
        return ExportResult(
            exporter_id=self.exporter_id,
            output_path=str(out),
            sha256=hashlib.sha256(payload).hexdigest(),
        )


# ---------------------------------------------------------------------------
# 5) PDF 导出器（PySide6：QTextDocument + QPdfWriter）
# ---------------------------------------------------------------------------


def _ensure_qt_application() -> None:
    """确保进程内存在 GUI 应用：复用既有应用，否则离屏创建 QApplication。"""
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is not None:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication(["zlg-ai-test-engineer"])


def _write_pdf(html_text: str, output_path: Path) -> None:
    from PySide6.QtGui import QPdfWriter, QTextDocument

    document = QTextDocument()
    document.setHtml(html_text)
    writer = QPdfWriter(str(output_path))
    writer.setResolution(96)
    document.print_(writer)
    # 函数返回时 writer 析构收尾（交叉引用表落盘），再由调用方计算哈希


class PdfReportExporter(ReportExporter):
    """PDF 报告：与 HTML 同源内容，经 QTextDocument 渲染为 PDF。

    无 GUI 环境时先尝试离屏创建 QApplication（QT_QPA_PLATFORM=offscreen
    兜底）；仍不可用则抛 ``RuntimeError("PDF 导出需要图形环境")``。
    """

    @property
    def exporter_id(self) -> str:
        return "pdf_report"

    @property
    def report_format(self) -> str:
        return "pdf"

    def export(
        self,
        run_result: Mapping[str, Any],
        evidence_paths: Sequence[str],
        output_path: Path,
    ) -> ExportResult:
        try:
            _ensure_qt_application()
        except Exception as exc:  # noqa: BLE001 — 图形环境失败统一收敛
            raise RuntimeError("PDF 导出需要图形环境") from exc

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_pdf(render_run_result_html(run_result, evidence_paths), out)
        payload = out.read_bytes()
        return ExportResult(
            exporter_id=self.exporter_id,
            output_path=str(out),
            sha256=hashlib.sha256(payload).hexdigest(),
        )


__all__ = [
    "AI_BADGE_TEXT",
    "REPORT_VERSION",
    "EvidenceStore",
    "HtmlReportExporter",
    "PdfReportExporter",
    "build_run_result",
    "render_run_result_html",
]
