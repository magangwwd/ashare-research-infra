"""Strict no-network public historical L2 metadata tests."""
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import unittest

p = Path(__file__).resolve().parents[1] / "data" / "okx_official_catalog.py"
spec = importlib.util.spec_from_file_location("official_archive_catalog", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class CatalogTests(unittest.TestCase):
    def test_query_contract_and_utc_timestamp(self):
        q = mod.query_parameters(dt.date(2026, 10, 10))
        self.assertEqual(q["begin"], q["end"])
        self.assertEqual(q["module"], "4")
        self.assertEqual(q["instFamilyList"], "BTC-USDT")
        self.assertEqual(q["dateAggrType"], "daily")
        self.assertEqual(q["begin"], "1791590400000")

    def test_reject_future_and_wrong_module(self):
        with self.assertRaises(ValueError):
            mod.query_parameters(dt.date.today() + dt.timedelta(days=1))
        with self.assertRaises(ValueError):
            mod.query_parameters(dt.date(2024, 10, 10), 5)
        with self.assertRaises(ValueError):
            mod.query_parameters(dt.date(2026, 10, 10), 6)

    def test_read_only_metadata_no_historical_download(self):
        actual_urls = []
        def fake(req, timeout):
            actual_urls.append(req.full_url)
            return io.BytesIO(json.dumps({
                "code": "0", "msg": "",
                "data": [{"groupDetails": [{"fileName": "example.zip"}], "instFamily": "BTC-USDT"}]
            }).encode())
        result = mod.fetch_catalog(dt.date(2026, 10, 10), 4, fake)
        self.assertEqual(result["entries_count"], 1)
        self.assertFalse(result["file_inventory_verified"])
        self.assertEqual(result["coverage_status"], "METADATA_ONLY_NO_FILE_DOWNLOADED")
        self.assertEqual(len(actual_urls), 1)
        self.assertTrue(actual_urls[0].startswith(mod.BASE + "?"))

    def test_fail_closed_on_unknown_shape_and_error_code(self):
        for body in ({"code": "50076", "data": []}, {"code": "0", "data": {}}, {"code": "0", "data": [1]}):
            with self.subTest(body=body):
                def fake(req, timeout):
                    return io.BytesIO(json.dumps(body).encode())
                with self.assertRaises(ValueError):
                    mod.fetch_catalog(dt.date(2026, 10, 10), 4, fake)


if __name__ == "__main__":
    unittest.main()
