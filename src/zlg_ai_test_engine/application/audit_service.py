"""PLAT-03：追加式审计日志服务。

任务书 14.2：所有生成、修改、审批、运行、取消和设备控制动作写入
不可随意覆盖的审计日志；只提供追加与查询，不提供修改/删除接口。
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from zlg_ai_test_engine.infrastructure.persistence.models import AuditEvent


class AuditService:
    """审计事件追加与查询。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        action: str,
        *,
        user: str = "system",
        object_ref: str = "",
        result: str = "ok",
        approval: str | None = None,
        run_id: str | None = None,
        refs: Sequence[dict[str, Any]] | Sequence[str] = (),
    ) -> int:
        """追加一条审计事件，返回 event_id。"""
        with self._session_factory() as session, session.begin():
            event = AuditEvent(
                action=action,
                user=user,
                object_ref=object_ref,
                result=result,
                approval=approval,
                run_id=run_id,
                refs_json=list(refs),
            )
            session.add(event)
            session.flush()
            return int(event.event_id)

    def list_events(
        self, *, run_id: str | None = None, action: str | None = None, limit: int = 200
    ) -> list[AuditEvent]:
        """按 run_id / action 检索（任务书验收：审计可按 run_id 检索）。"""
        stmt = select(AuditEvent).order_by(AuditEvent.event_id.desc()).limit(limit)
        if run_id is not None:
            stmt = stmt.where(AuditEvent.run_id == run_id)
        if action is not None:
            stmt = stmt.where(AuditEvent.action == action)
        with self._session_factory() as session:
            return list(session.scalars(stmt))
