"""PLAT-02：SQLite 引擎与会话工厂。

数据库位置：``%LOCALAPPDATA%/zlg-ai-test-engine/app.db``（可用环境变量
``ZLG_ATE_DB`` 覆盖，便于测试与便携部署）。
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from zlg_ai_test_engine.infrastructure.persistence.models import Base


def app_data_dir() -> Path:
    """应用数据目录（本机可写）。"""
    override = os.environ.get("ZLG_ATE_HOME")
    if override:
        base = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        base = base / "zlg-ai-test-engine"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_db_path() -> Path:
    override = os.environ.get("ZLG_ATE_DB")
    if override:
        return Path(override)
    return app_data_dir() / "app.db"


def create_engine_and_session(db_path: Path | None = None) -> tuple[Engine, sessionmaker[Session]]:
    """创建引擎与会话工厂（WAL 模式，允许 UI 与服务并发读）。"""
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, session_factory


def init_db(engine: Engine) -> None:
    """创建表结构（开发期入口；Alembic 迁移见 alembic/ 目录）。"""
    Base.metadata.create_all(engine)
