"""Regression for Drive-only research when the verified A-pool has fewer than top N."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ai-research-data-only.yml"
MODE = "DETERMINISTIC_MULTI_ROLE_NO_LLM_NO_NETWORK"
AUTH = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"


def report_check_code():
    content = WORKFLOW.read_text(encoding="utf-8")
    step = content.split("      - name: Run deterministic role research\n", 1)[1]
    script = step.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return textwrap.dedent(script)


def check_count(actual, audit_count=None, requested=5):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ai_output"
        out.mkdir()
        report = {
            "mode": MODE,
            "authorization": AUTH,
            "source": {"clean_coverage_complete": True},
            "stocks": [
                {"manager_agent": {"trade_authorization": AUTH}}
                for _ in range(actual)
            ],
        }
        audit = {
            "symbols": [str(i) for i in range(actual if audit_count is None else audit_count)],
            "authorization": AUTH,
            "all_target_symbols_covered": True,
            "snapshot": {"age_days": 0},
        }
        (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
        (out / "financial_clean_coverage.json").write_text(json.dumps(audit), encoding="utf-8")
        env = {**os.environ, "RUNNER_TEMP": tmp, "RESEARCH_TOP": str(requested)}
        return subprocess.run(
            [sys.executable, "-c", report_check_code()],
            env=env, capture_output=True, text=True, check=False,
        )


class ReportCardinalityTest(unittest.TestCase):
    def test_full_requested_pool(self):
        self.assertEqual(check_count(5).returncode, 0)

    def test_short_verified_pool_is_accepted_with_warning(self):
        result = check_count(4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Only 4 verified A-pool candidates", result.stdout)

    def test_zero_pool_is_rejected(self):
        self.assertNotEqual(check_count(0).returncode, 0)

    def test_more_than_requested_is_rejected(self):
        self.assertNotEqual(check_count(6).returncode, 0)

    def test_clean_coverage_must_match_actual_pool(self):
        self.assertNotEqual(check_count(4, audit_count=5).returncode, 0)


if __name__ == "__main__":
    unittest.main()
