"""AI-02：工程上下文快照、知识源元数据与外发过滤。

对应任务书 AI-02：
- 快照记录 文件 ID/哈希/解析器版本/资产状态 → 生成时引用具体版本，避免"工程更新后
  继续用旧信号"；
- 知识源带 来源/版本/适用型号协议/许可/保密标记，过期或不匹配不得无提示进入上下文；
- 本地优先：``snapshot_allows_upload`` 按策略过滤，机密知识默认不外发；
- 资产哈希变化 → 快照过期（stale），供 UI 提示重新生成。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeEntry(BaseModel):
    """知识源元数据（厂商手册/企业规范/历史脚本/已验证用例…）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: str = Field(min_length=1, description="来源标识（文件/文档/库）")
    version: str = ""
    applies_protocols: list[str] = Field(default_factory=list)
    applies_models: list[str] = Field(default_factory=list, description="适用设备型号（空=不限）")
    license: str = ""
    confidential: bool = Field(default=False, description="机密：默认禁止外发")
    sha256: str = ""
    imported_at: str = ""


class AssetView(BaseModel):
    """进入快照的资产视图（来自 ProjectService 资产元数据）。"""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    sha256: str
    status: str = "active"  # active/stale/missing
    parser_version: str = ""
    type: str = ""


@dataclass(frozen=True)
class ContextSnapshot:
    """一次生成所依据的工程上下文快照（内容寻址：id 即内容哈希）。"""

    snapshot_id: str
    created_at: str
    assets: tuple[AssetView, ...]
    knowledge: tuple[KnowledgeEntry, ...] = ()
    network_model_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "assets": [a.model_dump() for a in self.assets],
            "knowledge": [k.model_dump() for k in self.knowledge],
            "network_model_hash": self.network_model_hash,
        }

    def stale_assets(self, assets_now: Sequence[Mapping[str, Any]]) -> list[str]:
        """与当前资产对比：哈希变化/丢失 → 过期资产 id 列表。"""
        now = {str(a.get("asset_id")): a for a in assets_now}
        stale: list[str] = []
        for snap in self.assets:
            current = now.get(snap.asset_id)
            if current is None:
                stale.append(snap.asset_id)
                continue
            if str(current.get("sha256", "")) != snap.sha256:
                stale.append(snap.asset_id)
            elif str(current.get("status", "active")) == "missing":
                stale.append(snap.asset_id)
        return stale

    def is_expired(self, assets_now: Sequence[Mapping[str, Any]]) -> bool:
        return bool(self.stale_assets(assets_now))


def build_snapshot(
    assets: Iterable[Mapping[str, Any]] | Iterable[AssetView],
    *,
    knowledge: Sequence[KnowledgeEntry] = (),
    network_model: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> ContextSnapshot:
    """构建内容寻址快照：同一输入 → 同一 snapshot_id（可缓存、可追溯）。"""
    views: list[AssetView] = []
    for item in assets:
        views.append(item if isinstance(item, AssetView) else AssetView.model_validate(dict(item)))
    views.sort(key=lambda a: a.asset_id)

    model_hash = ""
    if network_model is not None:
        model_hash = hashlib.sha256(
            json.dumps(network_model, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    payload = json.dumps(
        {
            "assets": [a.model_dump() for a in views],
            "knowledge": [k.model_dump() for k in knowledge],
            "network_model_hash": model_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    snapshot_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return ContextSnapshot(
        snapshot_id=snapshot_id,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        assets=tuple(views),
        knowledge=tuple(knowledge),
        network_model_hash=model_hash,
    )


def snapshot_allows_upload(
    snapshot: ContextSnapshot,
    *,
    local_only: bool = True,
    protocols: Sequence[str] = (),
    device_models: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """按 AI-02 外发策略过滤可进入云模型请求的知识引用。

    Returns:
        (allowed_refs, excluded_ids)：allowed_refs 为最小化引用（id/source/version），
        excluded_ids 记录被排除的知识（机密 / local_only / 协议或型号不匹配）。

    规则：
    - ``local_only=True``（默认，企业文件本地优先）→ 全部知识不外发；
    - 机密（confidential）→ 永不外发；
    - 声明了 applies_protocols/applies_models 且与请求不匹配 → 不得无提示进入
      （排除并记录，供日志/审计）。
    """
    allowed: list[dict[str, Any]] = []
    excluded: list[str] = []
    for entry in snapshot.knowledge:
        if entry.confidential:
            excluded.append(entry.id)
            continue
        if local_only:
            excluded.append(entry.id)
            continue
        if entry.applies_protocols and protocols and not (
            set(entry.applies_protocols) & set(protocols)
        ):
            excluded.append(entry.id)
            continue
        if entry.applies_models and device_models and not (
            set(entry.applies_models) & set(device_models)
        ):
            excluded.append(entry.id)
            continue
        allowed.append({"id": entry.id, "source": entry.source, "version": entry.version})
    return allowed, excluded
