"""JUDGE-01：确定性断言引擎测试。

覆盖任务书验收：边界值单测；无证据断言不会 Pass；原始输入可重新计算相同结果。
"""

from __future__ import annotations

from zlg_ai_test_engine.domain.execution.judge import (
    FailureClass,
    Verdict,
    judge_cycle,
    judge_enum,
    judge_range,
    judge_step,
    judge_timeout,
)

MS = 1_000_000


def _arrivals(start_ms: int, count: int, gap_ms: float) -> list[int]:
    return [int((start_ms + i * gap_ms) * MS) for i in range(count)]


class TestJudgeCycle:
    def test_pass_within_tolerance(self):
        result = judge_cycle(
            "S2",
            {"expected_period_ms": 20, "tolerance_ms": 2, "evidence_source": "REQ_X"},
            _arrivals(100, 10, 20),
        )
        assert result.verdict is Verdict.PASS
        assert result.computed["frames"] == 10
        assert result.computed["worst_deviation_ms"] == 0.0
        assert result.threshold["evidence_source"] == "REQ_X"

    def test_fail_when_period_drifts(self):
        result = judge_cycle(
            "S2",
            {"expected_period_ms": 20, "tolerance_ms": 2, "evidence_source": "REQ_X"},
            _arrivals(0, 5, 35),
        )
        assert result.verdict is Verdict.FAIL
        assert result.failure_class is FailureClass.DUT
        assert "超出容差" in result.reason

    def test_boundary_tolerance_is_inclusive(self):
        # 偏差恰好等于容差 → 通过（> 才失败）
        result = judge_cycle(
            "S",
            {"expected_period_ms": 20, "tolerance_ms": 2, "evidence_source": "R"},
            _arrivals(0, 3, 22),  # 偏差正好 2ms
        )
        assert result.verdict is Verdict.PASS

    def test_insufficient_frames_blocked_not_pass(self):
        result = judge_cycle(
            "S", {"expected_period_ms": 20, "tolerance_ms": 2, "evidence_source": "R"}, []
        )
        assert result.verdict is Verdict.BLOCKED
        assert "证据不足" in result.reason

    def test_missing_threshold_blocked(self):
        result = judge_cycle("S", {"tolerance_ms": 1}, _arrivals(0, 5, 20))
        assert result.verdict is Verdict.BLOCKED

    def test_recompute_identical(self):
        params = {"expected_period_ms": 20, "tolerance_ms": 2, "evidence_source": "REQ_X"}
        arrivals = _arrivals(0, 8, 21)
        a = judge_cycle("S", params, arrivals)
        b = judge_cycle("S", params, list(arrivals))
        assert a.to_dict() == b.to_dict()
        assert a.inputs_digest == b.inputs_digest


class TestJudgeRange:
    def test_pass_including_boundaries(self):
        result = judge_range(
            "S", {"signal": "VS", "min": 0, "max": 250, "evidence_source": "R"}, [0, 125, 250]
        )
        assert result.verdict is Verdict.PASS

    def test_fail_reports_offenders(self):
        result = judge_range(
            "S", {"signal": "VS", "min": 0, "max": 250, "evidence_source": "R"}, [10, 251, -1]
        )
        assert result.verdict is Verdict.FAIL
        assert result.failure_class is FailureClass.DUT
        assert result.computed["offender_count"] == 2
        assert len(result.computed["offenders"]) == 2

    def test_empty_values_blocked(self):
        result = judge_range("S", {"signal": "VS", "min": 0, "max": 1, "evidence_source": "R"}, [])
        assert result.verdict is Verdict.BLOCKED

    def test_inverted_threshold_blocked(self):
        result = judge_range(
            "S", {"signal": "VS", "min": 300, "max": 100, "evidence_source": "R"}, [200]
        )
        assert result.verdict is Verdict.BLOCKED


class TestJudgeEnum:
    def test_pass_all_allowed(self):
        result = judge_enum(
            "S", {"signal": "WS", "allowed_values": [0, 1, 2], "evidence_source": "R"}, [0, 2, 1]
        )
        assert result.verdict is Verdict.PASS

    def test_unknown_value_fail(self):
        result = judge_enum(
            "S", {"signal": "WS", "allowed_values": [0, 1], "evidence_source": "R"}, [0, 7]
        )
        assert result.verdict is Verdict.FAIL
        assert result.computed["unknown_values"] == [7]
        assert result.failure_class is FailureClass.DUT

    def test_empty_blocked(self):
        result = judge_enum("S", {"signal": "WS", "allowed_values": [1], "evidence_source": "R"}, [])
        assert result.verdict is Verdict.BLOCKED


class TestJudgeTimeout:
    def test_timeout_gap_detected_fail(self):
        # 窗口 1000ms，第 300ms 起断流 400ms（> 300ms 阈值）
        arrivals = _arrivals(0, 3, 100) + _arrivals(700, 3, 100)
        result = judge_timeout(
            "S",
            {"message": "M", "timeout_ms": 300, "evidence_source": "R"},
            arrivals,
            (0, 1000 * MS),
        )
        assert result.verdict is Verdict.FAIL
        assert result.failure_class is FailureClass.DUT
        assert result.computed["max_gap_ms"] >= 300
        assert "最后一帧" in result.reason

    def test_no_timeout_within_sufficient_window_pass(self):
        arrivals = _arrivals(0, 20, 50)  # 每 50ms 一帧，窗口 1000ms
        result = judge_timeout(
            "S",
            {"message": "M", "timeout_ms": 300, "evidence_source": "R"},
            arrivals,
            (0, 1000 * MS),
        )
        # 首帧 0ms 前窗口起点到首帧为 0，末帧 950→1000 只有 50ms 空窗
        assert result.verdict is Verdict.PASS
        assert result.computed["max_gap_ms"] < 300

    def test_window_shorter_than_threshold_blocked(self):
        """观察窗 < 超时阈值时绝不因“没看到超时”而误 Pass。"""
        result = judge_timeout(
            "S",
            {"message": "M", "timeout_ms": 300, "evidence_source": "R"},
            _arrivals(0, 5, 20),
            (0, 200 * MS),
        )
        assert result.verdict is Verdict.BLOCKED
        assert "观察窗" in result.reason

    def test_missing_window_blocked(self):
        result = judge_timeout(
            "S", {"message": "M", "timeout_ms": 300, "evidence_source": "R"}, [0], None
        )
        assert result.verdict is Verdict.BLOCKED
        assert "监听窗口" in result.reason

    def test_no_frames_full_window_fail(self):
        """整窗无帧 = 从窗口起点即超时。"""
        result = judge_timeout(
            "S", {"message": "M", "timeout_ms": 300, "evidence_source": "R"}, [], (0, 1000 * MS)
        )
        assert result.verdict is Verdict.FAIL
        assert result.computed["max_gap_ms"] >= 1000


class TestJudgeDispatch:
    def test_dispatch_assert_cycle(self):
        result = judge_step(
            {"id": "S1", "action": "assert_cycle", "expected_period_ms": 10,
             "tolerance_ms": 1, "evidence_source": "R"},
            {"arrivals_ns": _arrivals(0, 5, 10)},
        )
        assert result.verdict is Verdict.PASS

    def test_non_assert_action_not_evaluated(self):
        result = judge_step({"id": "S1", "action": "listen_bus"}, {})
        assert result.verdict is Verdict.NOT_EVALUATED

    def test_missing_evidence_keys_blocked(self):
        result = judge_step(
            {"id": "S1", "action": "assert_range", "signal": "X",
             "min": 0, "max": 1, "evidence_source": "R"},
            {},
        )
        assert result.verdict is Verdict.BLOCKED
