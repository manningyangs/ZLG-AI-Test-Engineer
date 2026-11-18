"""AI-02 上下文快照与外发过滤测试。"""

from __future__ import annotations

from zlg_ai_test_engine.ai import (
    ContextSnapshot,
    KnowledgeEntry,
    build_snapshot,
    snapshot_allows_upload,
)

ASSETS = [
    {"asset_id": "dbc_vehicle", "sha256": "a" * 64, "status": "active",
     "parser_version": "1.0.0", "type": "dbc"},
    {"asset_id": "req_doc", "sha256": "b" * 64, "status": "active",
     "parser_version": "", "type": "doc"},
]


class TestSnapshot:
    def test_content_addressed_and_deterministic(self):
        s1 = build_snapshot(ASSETS, created_at="2026-11-18T00:00:00+00:00")
        s2 = build_snapshot(list(reversed(ASSETS)), created_at="2026-11-18T00:00:00+00:00")
        assert s1.snapshot_id == s2.snapshot_id  # 与顺序无关（内容寻址）
        s3 = build_snapshot(ASSETS, created_at="2026-11-18T00:00:00+00:00",
                            network_model={"messages": []})
        assert s3.snapshot_id != s1.snapshot_id  # 网络模型变化 → 快照变化

    def test_stale_detection_on_hash_change_and_missing(self):
        snap = build_snapshot(ASSETS, created_at="t0")
        unchanged = list(ASSETS)
        assert snap.stale_assets(unchanged) == []
        assert not snap.is_expired(unchanged)

        changed = [dict(ASSETS[0]), {**ASSETS[1], "sha256": "c" * 64}]
        assert snap.stale_assets(changed) == ["req_doc"]

        missing = [dict(ASSETS[0])]
        assert snap.stale_assets(missing) == ["req_doc"]

        removed_now: list = []
        assert set(snap.stale_assets(removed_now)) == {"dbc_vehicle", "req_doc"}

    def test_snapshot_serializable(self):
        snap = build_snapshot(ASSETS)
        data = snap.to_dict()
        assert data["snapshot_id"] and len(data["assets"]) == 2


class TestUploadFiltering:
    def _snap(self, knowledge: list[KnowledgeEntry]) -> ContextSnapshot:
        return build_snapshot(ASSETS, knowledge=knowledge)

    def test_local_only_excludes_everything(self):
        entries = [
            KnowledgeEntry(id="k1", source="vendor_manual", version="V1.1"),
            KnowledgeEntry(id="k2", source="corp_spec", confidential=True),
        ]
        allowed, excluded = snapshot_allows_upload(self._snap(entries), local_only=True)
        assert allowed == []
        assert set(excluded) == {"k1", "k2"}

    def test_confidential_never_uploaded_even_when_cloud_allowed(self):
        entries = [
            KnowledgeEntry(id="k_pub", source="vendor_manual", version="V1.1"),
            KnowledgeEntry(id="k_sec", source="corp_secret", confidential=True),
        ]
        allowed, excluded = snapshot_allows_upload(
            self._snap(entries), local_only=False, protocols=["CAN"]
        )
        assert [a["id"] for a in allowed] == ["k_pub"]
        assert excluded == ["k_sec"]

    def test_protocol_and_model_mismatch_excluded_not_silent(self):
        entries = [
            KnowledgeEntry(id="k_can", source="s", applies_protocols=["CAN"]),
            KnowledgeEntry(id="k_lin", source="s", applies_protocols=["LIN"]),
            KnowledgeEntry(id="k_dev200", source="s", applies_models=["USBCANFD-200U"]),
        ]
        allowed, excluded = snapshot_allows_upload(
            self._snap(entries), local_only=False,
            protocols=["CAN"], device_models=["USBCANFD-100U"],
        )
        allowed_ids = {a["id"] for a in allowed}
        assert allowed_ids == {"k_can"}
        assert set(excluded) == {"k_lin", "k_dev200"}  # 不匹配必须显式排除并记录
