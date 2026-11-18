"""集成测试：Alembic 初始迁移可升级、可回滚（任务书第 17 章变更审批规则）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*args: str, db_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ZLG_ATE_DB"] = str(db_path)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _tables(db_path: Path) -> set[str]:
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {r[0] for r in rows}


@pytest.fixture
def temp_db(tmp_path):
    return tmp_path / "migrate.db"


def test_upgrade_creates_expected_tables(temp_db):
    result = _run_alembic("upgrade", "head", db_path=temp_db)
    assert result.returncode == 0, result.stderr
    tables = _tables(temp_db)
    for expected in ("project", "asset", "network_model", "test_step", "audit_event", "alembic_version"):
        assert expected in tables, f"缺少表: {expected}"


def test_downgrade_base_removes_tables(temp_db):
    assert _run_alembic("upgrade", "head", db_path=temp_db).returncode == 0
    result = _run_alembic("downgrade", "base", db_path=temp_db)
    assert result.returncode == 0, result.stderr
    tables = _tables(temp_db)
    assert "project" not in tables and "audit_event" not in tables
    assert "alembic_version" in tables  # 版本表保留
