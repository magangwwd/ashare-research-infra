"""Small offline regression tests for the shared market source resolver."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_source_paths import is_gold_market_path, resolve_project_path
from data_source_preflight import check_source


class MarketPathTests(unittest.TestCase):
    def test_only_gold_path_is_redirectable(self):
        self.assertTrue(is_gold_market_path("../down_data/data/gold/main_board"))
        self.assertTrue(is_gold_market_path(r"..\\down_data\\data\\gold\\main_board"))
        self.assertFalse(is_gold_market_path("artifacts/legacy/demo02_data/processed"))

    def test_env_override_does_not_redirect_legacy_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            fake_root = Path(folder) / "gold"
            with patch.dict(os.environ, {"ASTOCK_MARKET_DATA_ROOT": str(fake_root)}):
                got = resolve_project_path(Path(folder), "../down_data/data/gold/main_board")
                legacy = resolve_project_path(Path(folder), "artifacts/legacy/a.parquet")
                self.assertEqual(got, fake_root.resolve())
                self.assertEqual(legacy, (Path(folder) / "artifacts/legacy/a.parquet").resolve())

    def test_preflight_reports_missing_and_stale_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "gold"
            root.mkdir()
            conf = Path(folder) / "config.json"
            conf.write_text('{"market_data_root":"../down_data/data/gold/main_board"}', encoding="utf-8")
            (root / "manifest.json").write_text('{"as_of_date":"2026-09-11","schema_version":"1.0.0"}', encoding="utf-8")
            with patch.dict(os.environ, {"ASTOCK_MARKET_DATA_ROOT": str(root)}):
                report = check_source(conf, min_as_of=__import__("datetime").date(2026, 10, 9))
            self.assertEqual(report["status"], "BLOCKED")
            self.assertTrue(report["data_stale"])
            self.assertTrue(report["missing_data"])

    def test_preflight_accepts_synthetic_complete_market(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "gold"
            root.mkdir()
            from data_source_preflight import REQUIRED_FILES, REQUIRED_DIRS
            for f in REQUIRED_FILES:
                (root / f).write_bytes(b"x")
            for d in REQUIRED_DIRS:
                (root / d).mkdir()
            (root / "manifest.json").write_text('{"as_of_date":"2026-10-09"}', encoding="utf-8")
            conf = Path(folder) / "config.json"
            conf.write_text('{"data_root":"../down_data/data/gold/main_board"}', encoding="utf-8")
            with patch.dict(os.environ, {"ASTOCK_MARKET_DATA_ROOT": str(root)}):
                report = check_source(conf, min_as_of=__import__("datetime").date(2026, 10, 9))
            self.assertEqual(report["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
