"""PLAT-02：工程与资产管理验收测试。

覆盖任务书 PLAT-02 验收：新建工程、导入两个 DBC、关闭再打开后元数据和哈希
一致；文件变化使关联派生数据 Stale；不丢失原文件。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from zlg_ai_test_engine.application.project_service import ProjectError, ProjectService
from zlg_ai_test_engine.infrastructure.persistence import (
    create_engine_and_session,
    init_db,
)
from zlg_ai_test_engine.infrastructure.persistence.models import Asset, NetworkModel, Project


@pytest.fixture
def service(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ZLG_ATE_DB", str(db_path))
    engine, session_factory = create_engine_and_session(db_path)
    init_db(engine)
    yield ProjectService(session_factory)
    engine.dispose()


@pytest.fixture
def dbc_file(tmp_path):
    """两份内容不同的 DBC 命名样本（内容为 JSON 形式仅作资产管理测试）。"""
    f1 = tmp_path / "vehicle.dbc"
    f1.write_text('{"messages": [{"name": "VehicleSpeed", "id": 0x123}]}', encoding="utf-8")
    f2 = tmp_path / "bms.dbc"
    f2.write_text('{"messages": [{"name": "BMS_Status_1", "id": 0x456}]}', encoding="utf-8")
    return f1, f2


class TestProjectLifecycle:
    def test_create_open_close_reopen(self, service, tmp_path):
        project = service.create_project("demo", tmp_path / "proj")
        assert project.project_id
        assert (Path(project.root_path) / "assets").is_dir()
        assert (Path(project.root_path) / "evidence").is_dir()
        reopened = service.open_project(project.project_id)
        assert reopened.name == "demo" and reopened.root_path == project.root_path

    def test_create_requires_absolute_root(self, service):
        with pytest.raises(ProjectError, match="绝对路径"):
            service.create_project("x", "relative/path")

    def test_open_missing_project_raises(self, service):
        with pytest.raises(ProjectError, match="不存在"):
            service.open_project("no-such-id")

    def test_open_project_with_missing_root_raises(self, service, tmp_path):
        project = service.create_project("gone", tmp_path / "p1")
        import shutil

        shutil.rmtree(project.root_path)
        with pytest.raises(ProjectError, match="缺失"):
            service.open_project(project.project_id)


class TestAssetImport:
    def test_import_two_dbc_and_reopen_consistency(self, service, tmp_path, dbc_file):
        """验收：导入两个 DBC → 关闭（新会话）再打开 → 元数据与哈希一致。"""
        f1, f2 = dbc_file
        project = service.create_project("p", tmp_path / "proj")
        a1 = service.import_asset(project.project_id, f1, asset_type="dbc")
        a2 = service.import_asset(project.project_id, f2, asset_type="dbc")

        expected1 = hashlib.sha256(f1.read_bytes()).hexdigest()
        expected2 = hashlib.sha256(f2.read_bytes()).hexdigest()
        assert a1.sha256 == expected1 and a2.sha256 == expected2
        assert a1.rel_path == "assets/vehicle.dbc"
        assert a2.size_bytes == f2.stat().st_size

        # “关闭再打开”：全新会话读取
        assets = service.list_assets(project.project_id)
        assert len(assets) == 2
        by_path = {a.rel_path: a for a in assets}
        assert by_path["assets/vehicle.dbc"].sha256 == expected1
        assert by_path["assets/bms.dbc"].sha256 == expected2
        assert all(a.status == "active" for a in assets)

    def test_source_file_never_modified(self, service, tmp_path, dbc_file):
        """验收：不丢失、不改写原文件。"""
        f1, _ = dbc_file
        original = f1.read_bytes()
        project = service.create_project("p", tmp_path / "proj")
        service.import_asset(project.project_id, f1)
        assert f1.exists() and f1.read_bytes() == original
        # 工程内拷贝独立存在
        copied = Path(project.root_path) / "assets" / f1.name
        assert copied.exists() and copied.read_bytes() == original

    def test_import_missing_file_raises(self, service, tmp_path):
        project = service.create_project("p", tmp_path / "proj")
        with pytest.raises(ProjectError, match="不存在"):
            service.import_asset(project.project_id, tmp_path / "ghost.dbc")

    def test_duplicate_rel_path_rejected(self, service, tmp_path, dbc_file):
        f1, _ = dbc_file
        project = service.create_project("p", tmp_path / "proj")
        service.import_asset(project.project_id, f1)
        # 同名文件二次导入 → 拷贝加序号，不覆盖已有资产
        again = service.import_asset(project.project_id, f1)
        assert again.rel_path == "assets/vehicle_1.dbc"

    def test_copy_project_duplicates_assets(self, service, tmp_path, dbc_file):
        f1, f2 = dbc_file
        project = service.create_project("p", tmp_path / "src_proj")
        service.import_asset(project.project_id, f1, asset_type="dbc")
        service.import_asset(project.project_id, f2, asset_type="dbc")
        copied = service.copy_project(project.project_id, "p-copy", tmp_path / "copy_proj")
        assert len(service.list_assets(copied.project_id)) == 2
        assert copied.root_path != project.root_path


class TestStalePropagation:
    def test_hash_change_marks_asset_and_network_model_stale(self, service, tmp_path, dbc_file):
        """验收：文件变化会使关联派生数据失效（Stale）。"""
        f1, _ = dbc_file
        project = service.create_project("p", tmp_path / "proj")
        asset = service.import_asset(project.project_id, f1, asset_type="dbc")
        service.record_network_model(
            project_id=project.project_id, asset_id=asset.asset_id, content={"messages": []}
        )

        # 无变化：无诊断
        assert service.refresh_asset_status(project.project_id) == []

        # 修改工程内文件（模拟外部改动）
        target = Path(project.root_path) / asset.rel_path
        target.write_text('{"messages": [{"name": "Changed"}]}', encoding="utf-8")
        diags = service.refresh_asset_status(project.project_id)
        codes = [d.code for d in diags]
        assert "asset_hash_changed" in codes

        refreshed = service.list_assets(project.project_id)[0]
        assert refreshed.status == "stale"
        with service._session_factory() as session:
            from sqlalchemy import select

            model = session.scalar(
                select(NetworkModel).where(NetworkModel.asset_id == asset.asset_id)
            )
            assert model.status == "stale"

    def test_missing_file_marks_missing(self, service, tmp_path, dbc_file):
        f1, _ = dbc_file
        project = service.create_project("p", tmp_path / "proj")
        asset = service.import_asset(project.project_id, f1)
        target = Path(project.root_path) / asset.rel_path
        target.unlink()
        diags = service.refresh_asset_status(project.project_id)
        assert any(d.code == "asset_missing" for d in diags)
        assert service.list_assets(project.project_id)[0].status == "missing"

    def test_manual_mark_derived_stale(self, service, tmp_path, dbc_file):
        f1, _ = dbc_file
        project = service.create_project("p", tmp_path / "proj")
        asset = service.import_asset(project.project_id, f1)
        service.record_network_model(
            project_id=project.project_id, asset_id=asset.asset_id, content={}
        )
        assert service.mark_derived_stale(project_id=project.project_id) == 1
        # 已是 stale 不重复计数
        assert service.mark_derived_stale(project_id=project.project_id) == 0
