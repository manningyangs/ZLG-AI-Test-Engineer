"""PLAT-02：SQLite 核心实体模型（SQLAlchemy 2.x 声明式）。

对应任务书 12.1 核心实体。原则：
- 源文件不入库（文件系统），本库只存元数据与派生索引；
- 原始证据仅追加（EvidenceArtifact 只增不改，无 update 服务）；
- 审计事件 Append-only（AuditEvent 无修改/删除接口）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: 当前工程/数据库 schema 版本（与 Test IR schema_version 独立）
SCHEMA_VERSION = "1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """全部实体的声明基类。"""


class Project(Base):
    """本地工程容器。"""

    __tablename__ = "project"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), default=SCHEMA_VERSION)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    assets: Mapped[list["Asset"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Asset(Base):
    """源文件资产（DBC/ODX/日志等）：路径为相对工程根的路径。"""

    __tablename__ = "asset"
    __table_args__ = (UniqueConstraint("project_id", "rel_path", name="uq_asset_project_path"),)

    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project_id"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # dbc/odx/log/...
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/stale/missing
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)

    project: Mapped[Project] = relationship(back_populates="assets")


class ParserRun(Base):
    """一次解析执行记录（诊断随记录保存）。"""

    __tablename__ = "parser_run"

    parser_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    parser_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # success/warning/failed
    diagnostics_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class NetworkModel(Base):
    """解析派生的统一网络模型文档（哈希变化 → Stale）。"""

    __tablename__ = "network_model"

    network_model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project_id"), nullable=False)
    asset_id: Mapped[str] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    parser_run_id: Mapped[str | None] = mapped_column(ForeignKey("parser_run.parser_run_id"), nullable=True)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="fresh")  # fresh/stale
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Requirement(Base):
    """测试需求与来源位置。"""

    __tablename__ = "requirement"

    requirement_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project_id"), nullable=False)
    source_asset_id: Mapped[str | None] = mapped_column(ForeignKey("asset.asset_id"), nullable=True)
    location: Mapped[str] = mapped_column(Text, default="")  # 页/行/单元格
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/confirmed/rejected
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TestSuite(Base):
    """测试套件（IR 结构化存储在 CORE-01 后接入，此处为元数据容器）。"""

    __tablename__ = "test_suite"

    suite_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    target_ecu: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TestCase(Base):
    """测试用例元数据。"""

    __tablename__ = "test_case"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suite.suite_id"), nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), default="P2")
    risk_level: Mapped[str] = mapped_column(String(4), default="L0")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    requirement_refs_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TestStep(Base):
    """测试步骤（params/assertions 为 IR 片段，Schema 校验由 CORE-01 提供）。"""

    __tablename__ = "test_step"
    __table_args__ = (UniqueConstraint("case_id", "sequence", name="uq_step_case_seq"),)

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("test_case.case_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(48), nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    assertions_json: Mapped[list[Any]] = mapped_column(JSON, default=list)


class DeviceSession(Base):
    """设备连接会话与能力快照（历史运行可复现）。"""

    __tablename__ = "device_session"

    device_session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sdk_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    driver_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capabilities_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ExecutionRun(Base):
    """一次执行记录（同用例重复运行生成不同 run_id，原数据不覆盖）。"""

    __tablename__ = "execution_run"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    adapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    device_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("device_session.device_session_id"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(32), default="Draft")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EvidenceArtifact(Base):
    """证据文件索引：只增不改（append-only 原则）。"""

    __tablename__ = "evidence_artifact"

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("execution_run.run_id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # raw_frame/log/waveform/report/...
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    time_range_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ModelInvocation(Base):
    """AI 调用审计（不含密钥与完整敏感工程内容）。"""

    __tablename__ = "model_invocation"

    invocation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_refs_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_prompt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_completion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuditEvent(Base):
    """审计事件：Append-only（本模型不提供任何 update/delete 服务）。"""

    __tablename__ = "audit_event"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    user: Mapped[str] = mapped_column(String(128), default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_ref: Mapped[str] = mapped_column(Text, default="")
    approval: Mapped[str | None] = mapped_column(String(32), nullable=True)  # granted/denied/...
    result: Mapped[str] = mapped_column(String(16), default="ok")  # ok/failed
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    refs_json: Mapped[list[Any]] = mapped_column(JSON, default=list)


Index("ix_asset_project", Asset.project_id)
Index("ix_run_adapter", ExecutionRun.adapter_id)
