"""REPORT-01：结果与证据归档 + HTML/PDF 报告导出测试。

验收覆盖：
1. build_run_result：真实 RunRecord → JSON 可序列化、keys 齐全、evidence 有值；
2. EvidenceStore：3 文件、append-only（旧内容前缀保留）、run 目录隔离、meta 可解析、
   session_factory 写 EvidenceArtifact 行；
3. HtmlReportExporter：存在/sha256/必需文案、导出前后证据哈希不变、非法与空 results 不崩溃；
4. PdfReportExporter：offscreen 生成 %PDF；图形环境不可用分支的异常文案；
5. 删除报告后证据仍在（派生报告与原始证据独立）。
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from zlg_ai_test_engine.application import reporting as reporting_mod
from zlg_ai_test_engine.application.pipeline import build_mock_orchestrator
from zlg_ai_test_engine.application.reporting import (
    EvidenceStore,
    HtmlReportExporter,
    PdfReportExporter,
    build_run_result,
)
from zlg_ai_test_engine.domain.execution.judge import Verdict
from zlg_ai_test_engine.domain.execution.orchestrator import RunRecord, RunState
from zlg_ai_test_engine.infrastructure.persistence import (
    create_engine_and_session,
    init_db,
)
from zlg_ai_test_engine.infrastructure.persistence.models import (
    EvidenceArtifact,
    ExecutionRun,
)
from zlg_ai_test_engine.plugins.mock_adapter import (
    MockAdapter,
    MockScenario,
    PeriodicFrameSpec,
)

CAN_ID = 0x2C6
EVIDENCE_SOURCE = "REQ_CYC_100"


def _ir(*, duration_ms: int) -> dict:
    """listen + assert_cycle 小 IR（L0 只读，无未确认项）。"""
    return {
        "schema_version": "1.0",
        "case_id": "REPORT_001",
        "title": "报告导出用例",
        "suite_id": "REPORT_SUITE",
        "protocol": {"bus": "CAN", "database": "vehicle.dbc"},
        "steps": [
            {"id": "S1", "action": "listen_bus", "duration_ms": duration_ms},
            {
                "id": "S2",
                "action": "assert_cycle",
                "message": "VehicleSpeed",
                "expected_period_ms": 20,
                "tolerance_ms": 2,
                "evidence_source": EVIDENCE_SOURCE,
            },
        ],
        "execution_policy": {
            "mode": "offline_only",
            "risk_level": "L0",
            "requires_approval": False,
            "max_duration_ms": 5000,
        },
        "unconfirmed_items": [],
    }


@pytest.fixture
def blocked_rec() -> RunRecord:
    """无场景帧：listen 100ms → assert_cycle 证据不足 → Blocked。"""
    orch = build_mock_orchestrator(message_ids={"VehicleSpeed": CAN_ID})
    rec = orch.submit(_ir(duration_ms=100))
    assert rec.state == RunState.QUEUED, rec.error
    orch.start(rec.run_id)
    assert rec.state == RunState.BLOCKED, rec.error
    return rec


@pytest.fixture
def completed_rec() -> RunRecord:
    """有周期帧场景：listen 300ms → assert_cycle Pass → Completed（带 AI 解释）。"""
    adapter = MockAdapter(
        MockScenario(
            periodic_frames=(
                PeriodicFrameSpec(can_id=CAN_ID, data=b"\x11\x22", period_ms=20),
            )
        )
    )
    orch = build_mock_orchestrator(
        adapter=adapter,
        message_ids={"VehicleSpeed": CAN_ID},
        ai_explainer=lambda _results, _ir: "AI 解读：周期稳定，建议复核首帧时刻。",
    )
    rec = orch.submit(_ir(duration_ms=300))
    assert rec.state == RunState.QUEUED, rec.error
    orch.start(rec.run_id)
    assert rec.state == RunState.COMPLETED, rec.error
    assert rec.results and rec.results[0].verdict is Verdict.PASS
    assert rec.evidence is not None and len(rec.evidence.frames) >= 2
    return rec


@pytest.fixture
def session_factory(tmp_path):
    engine, factory = create_engine_and_session(tmp_path / "report.db")
    init_db(engine)
    yield factory
    engine.dispose()


def _file_hashes(paths) -> dict[str, str]:
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


class TestBuildRunResult:
    """验收 1：真实 RunRecord → JSON 可序列化、keys 齐全、evidence 有值。"""

    def test_mock_run_json_serializable_and_complete(self, blocked_rec: RunRecord):
        data = build_run_result(blocked_rec)
        json.dumps(data, ensure_ascii=False)  # 不抛即通过

        assert set(data) == {"run", "ir", "plan", "summary", "results", "evidence"}

        run = data["run"]
        for key in (
            "run_id", "state", "adapter_id", "device_id", "runner_status",
            "approval", "approver", "created_at", "started_at", "ended_at",
            "error", "cleanup_errors", "ai_explainer", "clock",
        ):
            assert key in run, key
        assert run["run_id"] == blocked_rec.run_id
        assert run["state"] == "Blocked"
        assert run["created_at"] == blocked_rec.created_at.isoformat()
        assert run["started_at"] and run["ended_at"]
        assert isinstance(run["cleanup_errors"], list)
        assert isinstance(run["clock"], str) and run["clock"]

        ir = data["ir"]
        for key in (
            "case_id", "title", "suite_id", "protocol", "risk_level",
            "requires_approval", "unconfirmed_count",
        ):
            assert key in ir, key
        assert ir["case_id"] == "REPORT_001"
        assert ir["suite_id"] == "REPORT_SUITE"
        assert ir["protocol"] == {"bus": "CAN", "database": "vehicle.dbc"}
        assert ir["risk_level"] == "L0"
        assert ir["requires_approval"] is False
        assert ir["unconfirmed_count"] == 0

        plan = data["plan"]
        assert plan is not None
        assert set(plan) == {"compiler_version", "ir_hash", "plan_hash"}
        assert plan["ir_hash"] and plan["plan_hash"]

        assert data["summary"] == {"Pass": 0, "Fail": 0, "Blocked": 1, "NotEvaluated": 0}
        assert data["results"] == [blocked_rec.results[0].to_dict()]
        assert data["results"][0]["verdict"] == "Blocked"

        evidence = data["evidence"]
        assert evidence is not None  # evidence 挂载点已实现
        assert isinstance(evidence["frames"], list)
        assert isinstance(evidence["signals"], list)
        window = evidence["window_ns"]
        assert isinstance(window, list) and len(window) == 2
        assert window[1] > window[0]

    def test_plan_and_evidence_none_when_absent(self):
        rec = RunRecord(
            run_id="run-none",
            state=RunState.DRAFT,
            ir={},
            created_at=datetime.now(timezone.utc),
        )
        data = build_run_result(rec)
        json.dumps(data, ensure_ascii=False)
        assert data["plan"] is None
        assert data["evidence"] is None
        assert data["run"]["clock"] is None
        assert data["results"] == []
        assert data["summary"] == {"Pass": 0, "Fail": 0, "Blocked": 0, "NotEvaluated": 0}


class TestEvidenceStore:
    """验收 2：三文件、append-only、目录隔离、meta 可解析、DB 索引行。"""

    def test_persist_creates_three_files_and_appends(self, completed_rec, tmp_path):
        store = EvidenceStore(tmp_path / "ev")
        first = store.persist_run(completed_rec)

        assert set(first["sha256"]) == {"frames.jsonl", "signals.jsonl", "meta.json"}
        assert len(first["paths"]) == 3
        run_dir = tmp_path / "ev" / completed_rec.run_id
        assert all(Path(p).exists() for p in first["paths"])
        # 返回哈希与实际文件一致
        for name, digest in first["sha256"].items():
            assert digest == hashlib.sha256((run_dir / name).read_bytes()).hexdigest()

        frames_path = run_dir / "frames.jsonl"
        lines1 = frames_path.read_text(encoding="utf-8").splitlines()
        assert len(lines1) == len(completed_rec.evidence.frames) >= 2
        first_frame = json.loads(lines1[0])
        for key in ("t_ms", "t_ns", "channel", "can_id", "data_hex", "is_fd"):
            assert key in first_frame, key

        meta_path = run_dir / "meta.json"
        meta1 = json.loads(meta_path.read_text(encoding="utf-8"))  # 可解析
        assert set(meta1) == {"run", "ir", "plan"}
        assert meta1["run"]["run_id"] == completed_rec.run_id
        assert meta1["plan"]["plan_hash"]

        frames_bytes1 = frames_path.read_bytes()
        meta_bytes1 = meta_path.read_bytes()
        assert first["sha256"]["frames.jsonl"] == hashlib.sha256(frames_bytes1).hexdigest()

        # 第二次调用同一 run：JSONL 追加行、旧内容前缀保留、meta 不被覆盖
        second = store.persist_run(completed_rec)
        frames_bytes2 = frames_path.read_bytes()
        lines2 = frames_path.read_text(encoding="utf-8").splitlines()
        assert len(lines2) == 2 * len(lines1)
        assert frames_bytes2.startswith(frames_bytes1)  # 旧哈希前缀不丢
        assert len(frames_bytes2) > len(frames_bytes1)
        assert second["sha256"]["frames.jsonl"] == hashlib.sha256(frames_bytes2).hexdigest()
        assert meta_path.read_bytes() == meta_bytes1  # append-only：绝不截断覆盖

    def test_two_runs_use_isolated_directories(self, blocked_rec, completed_rec, tmp_path):
        store = EvidenceStore(tmp_path / "ev")
        store.persist_run(blocked_rec)
        store.persist_run(completed_rec)

        b_dir = tmp_path / "ev" / blocked_rec.run_id
        c_dir = tmp_path / "ev" / completed_rec.run_id
        assert b_dir != c_dir
        for run_dir, rec in ((b_dir, blocked_rec), (c_dir, completed_rec)):
            assert (run_dir / "frames.jsonl").exists()
            assert (run_dir / "signals.jsonl").exists()
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            assert meta["run"]["run_id"] == rec.run_id

        # 无场景帧的 run 不会混入另一 run 的帧
        assert (b_dir / "frames.jsonl").read_text(encoding="utf-8") == ""
        assert len(
            (c_dir / "frames.jsonl").read_text(encoding="utf-8").splitlines()
        ) == len(completed_rec.evidence.frames)

    def test_session_factory_writes_artifact_rows(
        self, completed_rec, tmp_path, session_factory
    ):
        store = EvidenceStore(tmp_path / "ev", session_factory=session_factory)
        first = store.persist_run(completed_rec)

        with session_factory() as session:
            rows = list(
                session.scalars(
                    select(EvidenceArtifact).where(
                        EvidenceArtifact.run_id == completed_rec.run_id
                    )
                )
            )
            assert len(rows) == 3
            assert {r.type for r in rows} == {"raw_frame", "signal_sample", "run_meta"}
            assert all(r.run_id == completed_rec.run_id for r in rows)
            assert {Path(r.path).name: r.sha256 for r in rows} == first["sha256"]
            # 外键父行（append-only 补建）
            assert session.get(ExecutionRun, completed_rec.run_id) is not None

        store.persist_run(completed_rec)
        with session_factory() as session:
            count = len(
                list(
                    session.scalars(
                        select(EvidenceArtifact).where(
                            EvidenceArtifact.run_id == completed_rec.run_id
                        )
                    )
                )
            )
            assert count == 6  # 索引行同样只增不改

    def test_without_session_factory_skips_db(self, blocked_rec, tmp_path):
        result = EvidenceStore(tmp_path / "ev").persist_run(blocked_rec)
        assert len(result["paths"]) == 3


class TestHtmlReportExporter:
    """验收 3：HTML 自包含导出、必需文案、只读证据、容错。"""

    def test_identity(self):
        exporter = HtmlReportExporter()
        assert exporter.exporter_id == "html_report"
        assert exporter.report_format == "html"

    def test_export_contains_required_content_and_keeps_evidence(
        self, completed_rec, tmp_path
    ):
        store = EvidenceStore(tmp_path / "ev")
        info = store.persist_run(completed_rec)
        run_result = build_run_result(completed_rec)
        before = _file_hashes(info["paths"])

        out = tmp_path / "report.html"
        export = HtmlReportExporter().export(run_result, info["paths"], out)

        assert export.exporter_id == "html_report"
        assert Path(export.output_path) == out
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert export.sha256 == hashlib.sha256(out.read_bytes()).hexdigest()

        # 必需内容
        assert "REPORT_001" in content  # case_id
        assert "REPORT_SUITE" in content
        assert completed_rec.run_id in content
        assert "Pass" in content  # verdict 文本
        assert "inputs_digest" in content
        assert "阈值来源" in content and EVIDENCE_SOURCE in content  # 阈值来源
        assert "不覆盖机器判定" in content  # AI 角标
        assert "AI 解读：周期稳定" in content  # ai_explanation 已呈现
        # 证据区：路径 + 帧/窗口统计
        for path in info["paths"]:
            assert html_mod.escape(path, quote=True) in content
        assert "帧数 frames" in content and "监听窗口 window_ns" in content
        # 头部版本信息
        assert "report-1.0" in content
        assert "compiler_version" in content and "plan_hash" in content
        # 自包含：无外部资源
        assert "<link" not in content and "<script" not in content

        # 只读证据：导出前后哈希不变
        assert _file_hashes(info["paths"]) == before

    def test_export_tolerates_empty_and_invalid_results(self, tmp_path):
        exporter = HtmlReportExporter()

        empty_out = tmp_path / "empty.html"
        export = exporter.export({}, [], empty_out)
        assert empty_out.exists() and empty_out.stat().st_size > 0
        assert export.sha256 == hashlib.sha256(empty_out.read_bytes()).hexdigest()

        weird = {
            "run": {"run_id": "weird-run"},
            "results": ["broken", None, 42],
            "summary": None,
            "evidence": None,
            "plan": None,
            "ir": None,
        }
        weird_out = tmp_path / "weird.html"
        exporter.export(weird, [], weird_out)
        weird_html = weird_out.read_text(encoding="utf-8")
        assert "weird-run" in weird_html and "broken" in weird_html

        # results 不是列表也不崩溃
        not_list_out = tmp_path / "not_list.html"
        exporter.export({"results": {"a": 1}}, [], not_list_out)
        assert not_list_out.exists() and not_list_out.stat().st_size > 0

    def test_blocked_run_renders_blocked_verdict(self, blocked_rec, tmp_path):
        store = EvidenceStore(tmp_path / "ev")
        info = store.persist_run(blocked_rec)
        out = tmp_path / "blocked.html"
        HtmlReportExporter().export(build_run_result(blocked_rec), info["paths"], out)
        content = out.read_text(encoding="utf-8")
        assert "Blocked" in content and "证据不足" in content
        assert "evidence_source" in content


class TestPdfReportExporter:
    """验收 4：offscreen 生成 %PDF；图形环境不可用的异常文案。"""

    def test_identity(self):
        exporter = PdfReportExporter()
        assert exporter.exporter_id == "pdf_report"
        assert exporter.report_format == "pdf"

    def test_offscreen_pdf_created(self, completed_rec, tmp_path):
        store = EvidenceStore(tmp_path / "ev")
        info = store.persist_run(completed_rec)
        before = _file_hashes(info["paths"])

        out = tmp_path / "report.pdf"
        export = PdfReportExporter().export(
            build_run_result(completed_rec), info["paths"], out
        )

        data = out.read_bytes()
        assert len(data) > 0
        assert data[:4] == b"%PDF"
        assert export.exporter_id == "pdf_report"
        assert export.sha256 == hashlib.sha256(data).hexdigest()
        assert _file_hashes(info["paths"]) == before  # 证据只读

    def test_gui_unavailable_raises_canonical_message(self, monkeypatch, tmp_path):
        def boom() -> None:
            raise RuntimeError("no gui here")

        monkeypatch.setattr(reporting_mod, "_ensure_qt_application", boom)
        with pytest.raises(RuntimeError, match="PDF 导出需要图形环境"):
            PdfReportExporter().export({"results": []}, [], tmp_path / "x.pdf")


class TestReportsAreDerivedArtifacts:
    """验收 5：报告是派生产物，删除后原始证据不受影响且可再导出。"""

    def test_deleting_reports_keeps_evidence(self, completed_rec, tmp_path):
        store = EvidenceStore(tmp_path / "ev")
        info = store.persist_run(completed_rec)
        before = _file_hashes(info["paths"])
        run_result = build_run_result(completed_rec)

        html_out = tmp_path / "r.html"
        pdf_out = tmp_path / "r.pdf"
        HtmlReportExporter().export(run_result, info["paths"], html_out)
        PdfReportExporter().export(run_result, info["paths"], pdf_out)
        assert html_out.exists() and pdf_out.exists()

        html_out.unlink()
        pdf_out.unlink()
        for path in info["paths"]:
            assert Path(path).exists()
        assert _file_hashes(info["paths"]) == before

        # 证据仍在 → 可以再次导出
        HtmlReportExporter().export(run_result, info["paths"], html_out)
        assert html_out.exists()
