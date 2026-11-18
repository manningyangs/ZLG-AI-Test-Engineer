"""PLAT-02：工程与资产管理服务。

职责：
- 新建/打开/复制/关闭工程（工程 = 根目录 + 数据库元数据）；
- 导入文件（拷贝入工程 assets/，源文件只读、绝不改写）；
- 记录相对路径、类型、大小、SHA-256；
- 资产哈希变化 → 派生数据（NetworkModel 等）标记 Stale。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from zlg_ai_test_engine.domain.plugins.contracts import Diagnostic, Severity
from zlg_ai_test_engine.infrastructure.file_storage import copy_into_project, sha256_file
from zlg_ai_test_engine.infrastructure.persistence.models import (
    Asset,
    NetworkModel,
    Project,
    utcnow,
)


class ProjectError(Exception):
    """工程操作错误（文件缺失、路径非法、重复导入等）。"""


def _new_id() -> str:
    return str(uuid.uuid4())


def _detect_type(path: Path) -> str:
    return path.suffix.lower().lstrip(".") or "unknown"


class ProjectService:
    """工程生命周期与资产管理。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # -- 工程 ---------------------------------------------------------------

    def create_project(self, name: str, root_path: str | Path) -> Project:
        root = Path(root_path)
        if not root.is_absolute():
            raise ProjectError(f"工程根路径必须为绝对路径: {root_path}")
        root.mkdir(parents=True, exist_ok=True)
        (root / "assets").mkdir(exist_ok=True)
        (root / "evidence").mkdir(exist_ok=True)
        project = Project(project_id=_new_id(), name=name, root_path=str(root))
        with self._session_factory() as session, session.begin():
            session.add(project)
        return project

    def open_project(self, project_id: str) -> Project:
        with self._session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectError(f"工程不存在: {project_id}")
            root = Path(project.root_path)
            if not root.is_dir():
                raise ProjectError(f"工程根目录缺失或不可访问: {root}")
            return project

    def list_projects(self) -> list[Project]:
        with self._session_factory() as session:
            return list(session.scalars(select(Project).order_by(Project.updated_at.desc())))

    def copy_project(self, project_id: str, new_name: str, new_root: Path) -> Project:
        """复制工程：拷贝根目录下文件并新建元数据（不共享资产行）。"""
        import shutil

        source = self.open_project(project_id)
        src_root = Path(source.root_path)
        new_root = Path(new_root)
        if new_root.exists() and any(new_root.iterdir()):
            raise ProjectError(f"目标目录非空: {new_root}")
        shutil.copytree(src_root, new_root, dirs_exist_ok=True)
        created = self.create_project(new_name, new_root)
        # 复制资产元数据（哈希沿用，导入即校验）
        with self._session_factory() as session, session.begin():
            for asset in session.scalars(
                select(Asset).where(Asset.project_id == project_id)
            ):
                session.add(
                    Asset(
                        asset_id=_new_id(),
                        project_id=created.project_id,
                        type=asset.type,
                        rel_path=asset.rel_path,
                        sha256=asset.sha256,
                        size_bytes=asset.size_bytes,
                        parser_version=asset.parser_version,
                        status=asset.status,
                    )
                )
        return created

    # -- 资产 ---------------------------------------------------------------

    def import_asset(
        self,
        project_id: str,
        source_path: str | Path,
        *,
        asset_type: str | None = None,
    ) -> Asset:
        """导入文件：拷贝到工程 assets/ 下，记录哈希与元数据。

        源文件保持只读（本服务绝不写入 source_path）。
        """
        source = Path(source_path)
        if not source.is_file():
            raise ProjectError(f"导入失败，文件不存在: {source}")
        project = self.open_project(project_id)
        root = Path(project.root_path)

        target = copy_into_project(source, root)
        rel = target.relative_to(root).as_posix()
        sha = sha256_file(target)
        asset = Asset(
            asset_id=_new_id(),
            project_id=project_id,
            type=asset_type or _detect_type(source),
            rel_path=rel,
            sha256=sha,
            size_bytes=target.stat().st_size,
            status="active",
        )
        with self._session_factory() as session, session.begin():
            existing = session.scalar(
                select(Asset).where(Asset.project_id == project_id, Asset.rel_path == rel)
            )
            if existing is not None:
                raise ProjectError(f"资产已存在: {rel}")
            session.add(asset)
        return asset

    def list_assets(self, project_id: str) -> list[Asset]:
        with self._session_factory() as session:
            return list(session.scalars(select(Asset).where(Asset.project_id == project_id)))

    def refresh_asset_status(self, project_id: str) -> list[Diagnostic]:
        """扫描资产状态：文件缺失 → missing；哈希变化 → stale 并联动派生数据。"""
        diagnostics: list[Diagnostic] = []
        with self._session_factory() as session, session.begin():
            assets = list(
                session.scalars(select(Asset).where(Asset.project_id == project_id))
            )
            root = Path(self.open_project(project_id).root_path)
            for asset in assets:
                path = root / asset.rel_path
                if not path.is_file():
                    if asset.status != "missing":
                        asset.status = "missing"
                        diagnostics.append(
                            Diagnostic(
                                "asset_missing",
                                f"资产文件缺失: {asset.rel_path}",
                                Severity.ERROR,
                                asset.rel_path,
                            )
                        )
                    continue
                current = sha256_file(path)
                if current != asset.sha256:
                    diagnostics.append(
                        Diagnostic(
                            "asset_hash_changed",
                            f"资产文件已变化，派生数据失效: {asset.rel_path}",
                            Severity.WARNING,
                            asset.rel_path,
                        )
                    )
                    # 源文件哈希变化 → 关联派生数据 Stale（任务书 PLAT-02 第 5 条）
                    asset.status = "stale"
                    asset.sha256 = current
                    asset.size_bytes = path.stat().st_size
                    for model in session.scalars(
                        select(NetworkModel).where(NetworkModel.asset_id == asset.asset_id)
                    ):
                        model.status = "stale"
                elif asset.status in ("stale", "missing"):
                    asset.status = "active"
        return diagnostics

    def mark_derived_stale(
        self, *, asset_id: str | None = None, project_id: str | None = None
    ) -> int:
        """手动将派生数据标记 Stale（解析器更新、AI 上下文过期等场景）。"""
        count = 0
        with self._session_factory() as session, session.begin():
            stmt = select(NetworkModel)
            if asset_id is not None:
                stmt = stmt.where(NetworkModel.asset_id == asset_id)
            if project_id is not None:
                stmt = stmt.where(NetworkModel.project_id == project_id)
            for model in session.scalars(stmt):
                if model.status != "stale":
                    model.status = "stale"
                    count += 1
        return count

    def record_network_model(
        self, *, project_id: str, asset_id: str, content: dict, parser_run_id: str | None = None
    ) -> NetworkModel:
        """保存解析派生的网络模型（同一资产重复解析覆盖派生行，原始资产不动）。"""
        import hashlib
        import json

        content_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        with self._session_factory() as session, session.begin():
            existing = session.scalar(
                select(NetworkModel).where(NetworkModel.asset_id == asset_id)
            )
            if existing is not None:
                existing.content_json = content
                existing.content_hash = content_hash
                existing.status = "fresh"
                existing.parser_run_id = parser_run_id
                existing.updated_at = utcnow()
                return existing
            model = NetworkModel(
                network_model_id=_new_id(),
                project_id=project_id,
                asset_id=asset_id,
                parser_run_id=parser_run_id,
                content_json=content,
                content_hash=content_hash,
                status="fresh",
            )
            session.add(model)
            return model
