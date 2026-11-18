"""P2 离线评测集跑批 CLI。

用法（仓库根目录）::

    .\\.venv\\Scripts\\python.exe scripts\\eval_ai.py                 # 默认离线桩
    .\\.venv\\Scripts\\python.exe scripts\\eval_ai.py --strict        # 任何失败都返回 1
    .\\.venv\\Scripts\\python.exe scripts\\eval_ai.py --json out.json # 输出机器可读报告

退出码：默认模式下，失败集合 ⊆ 离线桩已知缺口（上下文/冲突感知，AI-01 待补）视为通过；
``--strict`` 下任何失败都返回 1。接入真实 LLMProvider 后实现 Generator 协议并在此注册即可复跑。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from zlg_ai_test_engine.ai_eval import evaluate_suite, load_eval_set  # noqa: E402
from zlg_ai_test_engine.ai_eval.adapters import StubGenerator  # noqa: E402

#: 离线桩已知缺口（上下文/冲突感知）——AI-01 接入后应清零
STUB_KNOWN_GAPS = {
    "EVAL_N_UNKNOWN_SIGNAL",
    "EVAL_N_UNKNOWN_MESSAGE",
    "EVAL_N_CONFLICT",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ZLG AI Test Engineer 离线评测集跑批")
    parser.add_argument("--set", dest="set_path", default=None, help="评测集 JSON 路径")
    parser.add_argument("--strict", action="store_true", help="任何失败都返回非零退出码")
    parser.add_argument("--json", dest="json_out", default=None, help="报告输出 JSON 路径")
    args = parser.parse_args(argv)

    eval_set = load_eval_set(args.set_path)
    generator = StubGenerator()
    report = evaluate_suite(eval_set, generator)

    print(f"评测集: {eval_set.name} v{eval_set.set_version}  生成器: {generator.name}")
    print("-" * 72)
    for outcome in report.outcomes:
        mark = "PASS" if outcome.passed else "FAIL"
        print(f"[{mark}] {outcome.case_id:<28} {outcome.category}")
        for check in outcome.checks:
            if check.skipped:
                note = "skip"
            elif check.passed:
                note = "ok"
            else:
                note = "FAIL"
            line = f"        - {check.name:<24} {note}"
            if not check.passed and not check.skipped and check.detail:
                line += f"  ({check.detail[:80]})"
            print(line)
    print("-" * 72)
    print(
        f"通过 {len(report.pass_ids)}/{len(report.outcomes)}"
        f"（{report.pass_rate:.0%}）  失败: {report.fail_ids or '无'}"
    )

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入: {args.json_out}")

    unexpected = set(report.fail_ids) - STUB_KNOWN_GAPS
    if args.strict:
        return 1 if report.fail_ids else 0
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
